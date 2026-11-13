import asyncio
import json
import re
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import httpx
import pytest

from cztrading212 import (
    APIError,
    AsyncTrading212,
    RateLimiter,
    RateLimitError,
    RetryPolicy,
    Trading212,
)
from cztrading212.client import RawResponse
from cztrading212.core.reliability import READ_POLICIES, ReadExecutor


class VirtualClock:
    def __init__(self):
        self.value = 0.0
        self.sleeps = []

    def now(self):
        return self.value

    def sleep(self, delay):
        self.sleeps.append(delay)
        self.value += delay

    async def asleep(self, delay):
        self.sleep(delay)


def test_documented_read_policies_cover_all_get_endpoints():
    spec = json.loads((Path(__file__).resolve().parents[2] / "openapi/upstream.json").read_text())
    reads = {
        operations["get"]["operationId"]
        for operations in spec["paths"].values()
        if "get" in operations
    }
    assert set(READ_POLICIES) == reads
    assert READ_POLICIES["getAccountSummary"] == (1, 5)
    assert READ_POLICIES["dividends"] == (20, 60)
    for operations in spec["paths"].values():
        if "get" not in operations:
            continue
        operation = operations["get"]
        match = re.search(
            r"\*\*Rate limit:\*\* (\d+) req / (?:(\d+)m)?(\d+)s", operation["description"]
        )
        assert match, operation["operationId"]
        count, minutes, seconds = match.groups()
        assert READ_POLICIES[operation["operationId"]] == (
            int(count),
            int(minutes or 0) * 60 + int(seconds),
        )


def test_sliding_window_burst_bound_and_endpoint_independence():
    clock = VirtualClock()
    limiter = RateLimiter(policies={"a": (2, 10), "b": (1, 20)}, clock=clock.now, sleep=clock.sleep)
    limiter.acquire("a", strategy="wait", max_wait=10)
    limiter.acquire("a", strategy="wait", max_wait=10)
    limiter.acquire("b", strategy="wait", max_wait=10)
    with pytest.raises(RateLimitError) as error:
        limiter.acquire("a", strategy="fail_fast", max_wait=10)
    assert error.value.retry_after == 10
    limiter.acquire("a", strategy="wait", max_wait=10)
    assert clock.sleeps == [10]
    with pytest.raises(RateLimitError):
        limiter.acquire("b", strategy="wait", max_wait=9)


def test_headers_and_unreasonable_waits_are_bounded():
    clock = VirtualClock()
    limiter = RateLimiter(policies={}, clock=clock.now, wall_clock=lambda: 1000, sleep=clock.sleep)
    limiter.observe("a", {"x-ratelimit-remaining": "0", "x-ratelimit-reset": "1015"})
    with pytest.raises(RateLimitError) as error:
        limiter.acquire("a", strategy="wait", max_wait=10)
    assert error.value.retry_after == 15
    limiter.acquire("a", strategy="wait", max_wait=15)
    assert clock.sleeps == [15]


def test_concurrent_reservations_do_not_overbook():
    limiter = RateLimiter(policies={"a": (3, 60)}, clock=lambda: 0)

    def attempt(_):
        try:
            limiter.acquire("a", strategy="fail_fast", max_wait=0)
            return True
        except RateLimitError:
            return False

    with ThreadPoolExecutor(max_workers=12) as pool:
        assert sum(pool.map(attempt, range(40))) == 3


def test_retry_transient_read_only_and_respect_budget():
    clock = VirtualClock()
    limiter = RateLimiter(policies={}, clock=clock.now, sleep=clock.sleep)
    executor = ReadExecutor(
        limiter,
        strategy="wait",
        max_wait=10,
        policy=RetryPolicy(max_retries=2, base_delay=1, max_elapsed=10),
        clock=clock.now,
        sleep=clock.sleep,
        jitter=lambda low, high: high,
    )
    calls = []

    def request():
        calls.append(1)
        if len(calls) < 3:
            raise APIError(503, "orders", headers={"retry-after": "2"})
        return RawResponse({}, {})

    assert executor.call("orders", request).data == {}
    assert len(calls) == 3 and clock.sleeps == [2, 2]
    with pytest.raises(ValueError):
        executor.call("placeMarketOrder", request)
    clock.value = 0
    calls.clear()
    limited = ReadExecutor(
        limiter,
        strategy="wait",
        max_wait=10,
        policy=RetryPolicy(max_retries=2, max_elapsed=1),
        clock=clock.now,
        sleep=clock.sleep,
        jitter=lambda low, high: high,
    )
    with pytest.raises(APIError):
        limited.call("orders", request)
    assert len(calls) == 1


def test_service_429_fail_fast_and_raw_request_has_no_retry():
    requests = []

    def handle(request):
        requests.append(request)
        return httpx.Response(429, headers={"retry-after": "12", "set-cookie": "private"})

    with Trading212(
        "key",
        "secret",
        transport=httpx.MockTransport(handle),
        rate_limiter=RateLimiter(policies={}),
        rate_limit_strategy="fail_fast",
    ) as client:
        with pytest.raises(RateLimitError) as error:
            client.services.orders.list()
        assert error.value.status_code == 429 and error.value.retry_after == 12
        with pytest.raises(APIError) as raw:
            client.raw.orders.place_market_order(body={"ticker": "ABC", "quantity": 1})
        assert raw.value.headers == {"retry-after": "12"}
    assert len(requests) == 2


def test_service_retry_on_transient_http_error_without_real_sleep():
    clock = VirtualClock()
    limiter = RateLimiter(policies={}, clock=clock.now, sleep=clock.sleep)
    requests = []

    def handle(request):
        requests.append(request)
        return httpx.Response(503 if len(requests) == 1 else 200, text="[]")

    with Trading212(
        "key",
        "secret",
        transport=httpx.MockTransport(handle),
        rate_limiter=limiter,
        retry_policy=RetryPolicy(max_retries=1, base_delay=0),
    ) as client:
        assert client.services.orders.list() == ()
    assert len(requests) == 2


def test_async_cancellation_while_waiting_does_not_reserve_slot():
    async def run():
        sleeping = asyncio.Event()

        async def sleep_forever(delay):
            sleeping.set()
            await asyncio.Event().wait()

        limiter = RateLimiter(
            policies={"getPositions": (1, 60)}, clock=lambda: 0, async_sleep=sleep_forever
        )
        limiter.acquire("getPositions", strategy="wait", max_wait=0)
        task = asyncio.create_task(
            limiter.acquire_async("getPositions", strategy="wait", max_wait=60)
        )
        await sleeping.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert len(limiter._requests["getPositions"]) == 1

    asyncio.run(run())


def test_async_client_retry_and_metadata():
    async def run():
        calls = []

        async def handle(request):
            calls.append(request)
            return httpx.Response(
                502 if len(calls) == 1 else 200, text="[]", headers={"x-ratelimit-remaining": "7"}
            )

        async with AsyncTrading212(
            "key",
            "secret",
            transport=httpx.MockTransport(handle),
            rate_limiter=RateLimiter(policies={}),
            retry_policy=RetryPolicy(max_retries=1, base_delay=0),
        ) as client:
            assert await client.services.positions.list() == ()
            response = await client.raw.call_with_metadata("getPositions")
            assert response.headers["x-ratelimit-remaining"] == "7"
        assert len(calls) == 3

    asyncio.run(run())


def test_server_remaining_is_reserved_conservatively_with_out_of_order_responses():
    clock = VirtualClock()
    limiter = RateLimiter(policies={}, clock=clock.now, wall_clock=lambda: 1000, sleep=clock.sleep)
    limiter.observe("a", {"x-ratelimit-remaining": "2", "x-ratelimit-reset": "1010"})
    limiter.acquire("a", strategy="fail_fast", max_wait=0)
    limiter.observe("a", {"x-ratelimit-remaining": "2", "x-ratelimit-reset": "1010"})
    limiter.acquire("a", strategy="fail_fast", max_wait=0)
    with pytest.raises(RateLimitError) as error:
        limiter.acquire("a", strategy="fail_fast", max_wait=0)
    assert error.value.retry_after == 10
    limiter.acquire("a", strategy="wait", max_wait=10)
    assert clock.sleeps == [10]


def test_429_retry_uses_server_delay_and_exhaustion_is_structured():
    clock = VirtualClock()
    limiter = RateLimiter(policies={}, clock=clock.now, sleep=clock.sleep, wall_clock=lambda: 0)
    executor = ReadExecutor(
        limiter,
        strategy="wait",
        max_wait=10,
        policy=RetryPolicy(max_retries=1, base_delay=0),
        clock=clock.now,
        sleep=clock.sleep,
        jitter=lambda low, high: 0,
    )
    calls = []

    def request():
        calls.append(1)
        raise APIError(429, "orders", headers={"retry-after": "3"})

    with pytest.raises(RateLimitError) as error:
        executor.call("orders", request)
    assert error.value.status_code == 429
    assert len(calls) == 2 and clock.sleeps == [3]


def test_default_client_limiter_applies_to_service_cache_misses_only():
    calls = []

    def handle(request):
        calls.append(request)
        return httpx.Response(200, text='{"id":7}')

    with Trading212(
        "key", "secret", transport=httpx.MockTransport(handle), rate_limit_strategy="fail_fast"
    ) as client:
        client.services.account.summary()
        client.services.account.summary()
        with pytest.raises(RateLimitError):
            client.services.account.summary(refresh=True)
        client.raw.account.get_account_summary()
    assert len(calls) == 2


def test_auth_error_is_not_retried():
    calls = []

    def handle(request):
        calls.append(request)
        return httpx.Response(401)

    with (
        Trading212(
            "key",
            "secret",
            transport=httpx.MockTransport(handle),
            rate_limiter=RateLimiter(policies={}),
        ) as client,
        pytest.raises(APIError) as error,
    ):
        client.services.account.summary()
    assert error.value.status_code == 401 and len(calls) == 1


def test_first_rate_limit_wait_is_separate_from_retry_budget():
    clock = VirtualClock()
    limiter = RateLimiter(policies={"orders": (1, 5)}, clock=clock.now, sleep=clock.sleep)
    limiter.acquire("orders", strategy="wait", max_wait=0)
    executor = ReadExecutor(
        limiter,
        strategy="wait",
        max_wait=5,
        policy=RetryPolicy(max_retries=0, max_elapsed=0),
        clock=clock.now,
        sleep=clock.sleep,
    )
    assert executor.call("orders", lambda: RawResponse([], {})).data == []
    assert clock.sleeps == [5]


def test_async_sliding_window_enforces_wait():
    async def run():
        clock = VirtualClock()
        limiter = RateLimiter(
            policies={"orders": (1, 5)}, clock=clock.now, async_sleep=clock.asleep
        )
        await limiter.acquire_async("orders", strategy="wait", max_wait=5)
        await limiter.acquire_async("orders", strategy="wait", max_wait=5)
        assert clock.sleeps == [5]

    asyncio.run(run())


@pytest.mark.parametrize("strategy, wait", [("unknown", 10), ("wait", float("nan")), ("wait", -1)])
def test_public_limiter_rejects_invalid_wait_configuration(strategy, wait):
    with pytest.raises(ValueError):
        RateLimiter().acquire("orders", strategy=strategy, max_wait=wait)


def test_read_marked_never_retry_is_still_allowed_once(monkeypatch):
    from cztrading212.core import reliability

    monkeypatch.setattr(reliability, "RETRYABLE_READS", reliability.RETRYABLE_READS - {"orders"})
    clock = VirtualClock()
    limiter = RateLimiter(policies={}, clock=clock.now, sleep=clock.sleep)
    executor = ReadExecutor(
        limiter,
        strategy="wait",
        max_wait=0,
        policy=RetryPolicy(max_retries=2),
        clock=clock.now,
        sleep=clock.sleep,
    )
    calls = []

    def request():
        calls.append(1)
        raise APIError(503, "orders")

    with pytest.raises(APIError):
        executor.call("orders", request)
    assert len(calls) == 1
