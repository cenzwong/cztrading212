"""Process-local endpoint rate limiting and bounded retries for documented reads."""

from __future__ import annotations

import asyncio
import math
import random
import threading
import time
from collections import defaultdict, deque
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from email.utils import parsedate_to_datetime
from typing import TYPE_CHECKING, Protocol, TypeVar

from ..exceptions import APIError, NetworkError, RateLimitError
from .catalog import OPERATIONS

if TYPE_CHECKING:
    from ..client import RawResponse

# Read retry eligibility is reviewed in openapi/policies.json; limits are
# generated from the endpoint descriptions in the snapshot.
READ_POLICIES: dict[str, tuple[int, float]] = {
    operation: (
        int(metadata["rate_limit_policy"]["requests"]),
        float(metadata["rate_limit_policy"]["period_seconds"]),
    )
    for operation, metadata in OPERATIONS.items()
    if metadata["method"] == "GET"
}
ALL_POLICIES: dict[str, tuple[int, float]] = {
    operation: (
        int(metadata["rate_limit_policy"]["requests"]),
        float(metadata["rate_limit_policy"]["period_seconds"]),
    )
    for operation, metadata in OPERATIONS.items()
}
RETRYABLE_READS = frozenset(
    operation
    for operation, metadata in OPERATIONS.items()
    if metadata["retry_policy"] == "safe_read"
)


class Limiter(Protocol):
    def acquire(self, operation: str, *, strategy: str, max_wait: float) -> None: ...
    async def acquire_async(self, operation: str, *, strategy: str, max_wait: float) -> None: ...
    def observe(
        self, operation: str, headers: Mapping[str, str], *, limited: bool = False
    ) -> None: ...


def _delay_from_headers(headers: Mapping[str, str], wall_time: float) -> float | None:
    delays: list[float] = []
    retry_after = headers.get("retry-after")
    if retry_after:
        try:
            delay = float(retry_after)
        except ValueError:
            try:
                delay = parsedate_to_datetime(retry_after).timestamp() - wall_time
            except (ValueError, TypeError, OverflowError):
                delay = -1
        if math.isfinite(delay) and delay >= 0:
            delays.append(delay)
    reset = headers.get("x-ratelimit-reset")
    if reset:
        try:
            delay = float(reset) - wall_time
        except ValueError:
            delay = -1
        if math.isfinite(delay) and delay >= 0:
            delays.append(delay)
    return max(delays) if delays else None


class RateLimiter:
    """Thread-safe sliding-window reservations plus server-supplied cooldowns.

    Sharing an instance coordinates clients in this process. Separate processes,
    other keys, and direct raw requests are not coordinated.
    """

    def __init__(
        self,
        *,
        policies: Mapping[str, tuple[int, float]] | None = None,
        clock: Callable[[], float] = time.monotonic,
        wall_clock: Callable[[], float] = time.time,
        sleep: Callable[[float], None] = time.sleep,
        async_sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        self.policies = dict(ALL_POLICIES if policies is None else policies)
        for limit, period in self.policies.values():
            if (
                type(limit) is not int
                or limit <= 0
                or type(period) not in (int, float)
                or not math.isfinite(period)
                or period <= 0
            ):
                raise ValueError("Rate policies require a positive limit and finite period")
        self._clock, self._wall_clock, self._sleep, self._async_sleep = (
            clock,
            wall_clock,
            sleep,
            async_sleep,
        )
        self._lock = threading.Lock()
        self._requests: dict[str, deque[float]] = defaultdict(deque)
        self._cooldowns: dict[str, float] = {}
        self._server_windows: dict[str, tuple[float, int]] = {}

    def _reserve(self, operation: str) -> float:
        with self._lock:
            now = self._clock()
            delay = max(0.0, self._cooldowns.get(operation, 0.0) - now)
            window = self._server_windows.get(operation)
            if window and window[0] <= now:
                self._server_windows.pop(operation, None)
                window = None
            if window and window[1] <= 0:
                delay = max(delay, window[0] - now)
            policy = self.policies.get(operation)
            if policy:
                limit, period = policy
                requests = self._requests[operation]
                while requests and requests[0] + period <= now:
                    requests.popleft()
                if len(requests) >= limit:
                    delay = max(delay, requests[0] + period - now)
            if delay <= 0:
                if policy:
                    self._requests[operation].append(now)
                if window:
                    self._server_windows[operation] = (window[0], window[1] - 1)
            return delay

    @staticmethod
    def _validate_wait(strategy: str, max_wait: float) -> None:
        if strategy not in {"wait", "fail_fast"}:
            raise ValueError("rate limit strategy must be 'wait' or 'fail_fast'")
        if type(max_wait) not in (int, float) or not math.isfinite(max_wait) or max_wait < 0:
            raise ValueError("max_wait must be finite and non-negative")

    def acquire(self, operation: str, *, strategy: str, max_wait: float) -> None:
        self._validate_wait(strategy, max_wait)
        deadline = self._clock() + max_wait
        while (delay := self._reserve(operation)) > 0:
            if strategy == "fail_fast" or delay > max(0.0, deadline - self._clock()):
                raise RateLimitError(operation, delay)
            self._sleep(delay)

    async def acquire_async(self, operation: str, *, strategy: str, max_wait: float) -> None:
        self._validate_wait(strategy, max_wait)
        deadline = self._clock() + max_wait
        while (delay := self._reserve(operation)) > 0:
            if strategy == "fail_fast" or delay > max(0.0, deadline - self._clock()):
                raise RateLimitError(operation, delay)
            await self._async_sleep(delay)

    def observe(self, operation: str, headers: Mapping[str, str], *, limited: bool = False) -> None:
        remaining: int | None = None
        try:
            if "x-ratelimit-remaining" in headers:
                remaining = int(headers["x-ratelimit-remaining"])
                if remaining < 0:
                    remaining = None
        except ValueError:
            pass
        if limited:
            remaining = 0
        if remaining is None:
            return
        delay = _delay_from_headers(headers, self._wall_clock())
        if delay is None:
            try:
                period = float(headers.get("x-ratelimit-period", ""))
                if not math.isfinite(period) or period <= 0:
                    raise ValueError
                delay = period
            except ValueError:
                delay = self.policies.get(operation, (1, 1))[1]
        if delay <= 0:
            return
        with self._lock:
            now = self._clock()
            reset = now + delay
            previous = self._server_windows.get(operation)
            if previous and previous[0] > now:
                # Responses can arrive out of order; never restore reservations
                # from an older response while the observed window is active.
                reset = max(reset, previous[0])
                remaining = min(remaining, previous[1])
            self._server_windows[operation] = (reset, remaining)
            if remaining == 0:
                self._cooldowns[operation] = max(self._cooldowns.get(operation, 0), reset)


@dataclass(frozen=True, slots=True, kw_only=True)
class RetryPolicy:
    max_retries: int = 2
    base_delay: float = 0.25
    max_delay: float = 5.0
    max_elapsed: float = 30.0

    def __post_init__(self) -> None:
        if type(self.max_retries) is not int or self.max_retries < 0:
            raise ValueError("max_retries must be a non-negative integer")
        for value in (self.base_delay, self.max_delay, self.max_elapsed):
            if type(value) not in (int, float) or not math.isfinite(value) or value < 0:
                raise ValueError("Retry timings must be finite, non-negative numbers")


T = TypeVar("T")


class ReadExecutor:
    """Retry only the explicit documented-read allowlist, never raw mutations."""

    def __init__(
        self,
        limiter: Limiter,
        *,
        strategy: str,
        max_wait: float,
        policy: RetryPolicy,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
        async_sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        jitter: Callable[[float, float], float] = random.uniform,
    ) -> None:
        if strategy not in {"wait", "fail_fast"}:
            raise ValueError("rate_limit_strategy must be 'wait' or 'fail_fast'")
        if type(max_wait) not in (int, float) or not math.isfinite(max_wait) or max_wait < 0:
            raise ValueError("max_rate_limit_wait must be finite and non-negative")
        self.limiter, self.strategy, self.max_wait, self.policy = (
            limiter,
            strategy,
            max_wait,
            policy,
        )
        self.clock, self.sleep, self.async_sleep, self.jitter = clock, sleep, async_sleep, jitter

    def _error(
        self, operation: str, exc: APIError | NetworkError, attempt: int, started: float
    ) -> float:
        header_delay = 0.0
        retry_limit = self.policy.max_retries if operation in RETRYABLE_READS else 0
        if isinstance(exc, APIError):
            self.limiter.observe(operation, exc.headers, limited=exc.status_code == 429)
            if exc.status_code == 429 and (self.strategy == "fail_fast" or attempt >= retry_limit):
                delay = _delay_from_headers(exc.headers, time.time())
                if delay is None:
                    delay = READ_POLICIES[operation][1]
                raise RateLimitError(operation, delay, status_code=429) from exc
            if exc.status_code not in {408, 429, 500, 502, 503, 504}:
                raise exc
            if "retry-after" in exc.headers:
                header_delay = (
                    _delay_from_headers({"retry-after": exc.headers["retry-after"]}, time.time())
                    or 0.0
                )
        if attempt >= retry_limit:
            raise exc
        remaining = self.policy.max_elapsed - (self.clock() - started)
        delay = max(
            header_delay,
            self.jitter(0, min(self.policy.max_delay, self.policy.base_delay * 2**attempt)),
        )
        if remaining <= 0 or delay >= remaining:
            if isinstance(exc, APIError) and exc.status_code == 429:
                raise RateLimitError(
                    operation, header_delay or READ_POLICIES[operation][1], status_code=429
                ) from exc
            raise exc
        return delay

    def call(self, operation: str, request: Callable[[], RawResponse]) -> RawResponse:
        if operation not in READ_POLICIES:
            raise ValueError("Operation is not eligible for automatic read retries")
        started = self.clock()
        last_error: APIError | NetworkError | None = None
        for attempt in range(self.policy.max_retries + 1):
            remaining = self.policy.max_elapsed - (self.clock() - started)
            wait = self.max_wait if attempt == 0 else min(self.max_wait, max(0, remaining))
            self.limiter.acquire(operation, strategy=self.strategy, max_wait=wait)
            if attempt == 0:
                started = self.clock()
            if last_error is not None and self.clock() - started >= self.policy.max_elapsed:
                raise last_error
            try:
                response = request()
            except (APIError, NetworkError) as exc:
                last_error = exc
                self.sleep(self._error(operation, exc, attempt, started))
                continue
            self.limiter.observe(operation, response.headers)
            return response
        raise AssertionError("retry loop exhausted")

    async def call_async(
        self, operation: str, request: Callable[[], Awaitable[RawResponse]]
    ) -> RawResponse:
        if operation not in READ_POLICIES:
            raise ValueError("Operation is not eligible for automatic read retries")
        started = self.clock()
        last_error: APIError | NetworkError | None = None
        for attempt in range(self.policy.max_retries + 1):
            remaining = self.policy.max_elapsed - (self.clock() - started)
            wait = self.max_wait if attempt == 0 else min(self.max_wait, max(0, remaining))
            await self.limiter.acquire_async(operation, strategy=self.strategy, max_wait=wait)
            if attempt == 0:
                started = self.clock()
            if last_error is not None and self.clock() - started >= self.policy.max_elapsed:
                raise last_error
            try:
                response = await request()
            except (APIError, NetworkError) as exc:
                last_error = exc
                await self.async_sleep(self._error(operation, exc, attempt, started))
                continue
            self.limiter.observe(operation, response.headers)
            return response
        raise AssertionError("retry loop exhausted")
