"""Synchronous and asynchronous clients for Trading 212's documented operations."""

from __future__ import annotations

import math
import os
from dataclasses import dataclass
from enum import Enum
from typing import Any, Self
from urllib.parse import quote

import httpx

from . import codec
from ._generated import operations as generated_operations
from .application import Application, AsyncApplication
from .core.cache import Cache, MemoryCache
from .core.cache_policy import DEFAULT_TTLS, namespace
from .core.catalog import OPERATIONS
from .core.reliability import Limiter, RateLimiter, ReadExecutor, RetryPolicy
from .core.safety.journal import OrderJournal
from .exceptions import APIError, NetworkError, Trading212Error
from .services import AsyncServices, Services


class Environment(str, Enum):
    DEMO = "demo"
    LIVE = "live"


def _request(
    operation: str, arguments: dict[str, Any]
) -> tuple[str, str, dict[str, Any], str | None]:
    try:
        op = OPERATIONS[operation]
    except KeyError as exc:
        raise ValueError(f"Unknown Trading 212 operation: {operation}") from exc
    args = dict(arguments)
    path = op["path"]
    params: dict[str, Any] = {}
    for param in op["parameters"]:
        name = param["name"]
        value = args.pop(name, None)
        if value is None:
            if param.get("required"):
                raise ValueError(f"Missing required parameter: {name}")
            continue
        if param["in"] == "path":
            segment = str(value)
            # HTTP URL normalization resolves literal dot segments before the
            # request is sent; never let a parameter change the endpoint path.
            if segment in {"", ".", ".."}:
                raise ValueError(f"Invalid path parameter: {name}")
            path = path.replace("{" + name + "}", quote(segment, safe=""))
        elif param["in"] == "query":
            params[name] = value
    body = args.pop("body", None)
    if args:
        raise TypeError(f"Unexpected arguments: {', '.join(sorted(args))}")
    if body is not None and not op.get("request_body"):
        raise TypeError("This operation does not accept a request body")
    request_body: dict[str, Any] = op.get("request_body") or {}
    if body is None and request_body.get("required"):
        raise ValueError("This operation requires a request body")
    return op["method"], path, params, codec.dumps(body) if body is not None else None


@dataclass(frozen=True, slots=True)
class RawResponse:
    """A direct response, HTTP status, and the broker's rate-limit headers."""

    data: Any
    headers: dict[str, str]
    status_code: int = 200


def _response(response: httpx.Response, operation: str) -> RawResponse:
    allowed = {
        "retry-after",
        "x-ratelimit-limit",
        "x-ratelimit-period",
        "x-ratelimit-remaining",
        "x-ratelimit-reset",
        "x-ratelimit-used",
    }
    headers = {
        key.lower(): value for key, value in response.headers.items() if key.lower() in allowed
    }
    if not 200 <= response.status_code < 300:
        raise APIError(response.status_code, operation, headers=headers)
    if not response.content:
        return RawResponse(None, headers, response.status_code)
    try:
        # httpx's text property replaces invalid byte sequences. JSON exchanged
        # with the API is UTF-8, and replacement could silently alter data.
        return RawResponse(
            codec.loads(response.content.decode("utf-8")), headers, response.status_code
        )
    except (ValueError, UnicodeError) as exc:
        raise Trading212Error(f"Invalid JSON response for {operation}") from exc


class Raw:
    """Direct API operations. Each call makes exactly one request with no retry or cache."""

    def __init__(self, transport: httpx.Client) -> None:
        self._transport = transport
        self.account = generated_operations.Account(self)
        self.history = generated_operations.History(self)
        self.instruments = generated_operations.Instruments(self)
        self.orders = generated_operations.Orders(self)
        self.pies = generated_operations.Pies(self)
        self.positions = generated_operations.Positions(self)

    def call(self, operation_id: str, **arguments: Any) -> Any:
        return self.call_with_metadata(operation_id, **arguments).data

    def call_with_metadata(self, operation_id: str, **arguments: Any) -> RawResponse:
        method, path, params, body = _request(operation_id, arguments)
        try:
            response = self._transport.request(
                method,
                path,
                params=params,
                content=body,
                headers={"Content-Type": "application/json"} if body is not None else None,
            )
        except httpx.RequestError as exc:
            raise NetworkError(f"Network error for {operation_id}; outcome may be unknown") from exc
        return _response(response, operation_id)


class AsyncRaw:
    """Async equivalent of Raw."""

    def __init__(self, transport: httpx.AsyncClient) -> None:
        self._transport = transport
        self.account = generated_operations.AsyncAccount(self)
        self.history = generated_operations.AsyncHistory(self)
        self.instruments = generated_operations.AsyncInstruments(self)
        self.orders = generated_operations.AsyncOrders(self)
        self.pies = generated_operations.AsyncPies(self)
        self.positions = generated_operations.AsyncPositions(self)

    async def call(self, operation_id: str, **arguments: Any) -> Any:
        return (await self.call_with_metadata(operation_id, **arguments)).data

    async def call_with_metadata(self, operation_id: str, **arguments: Any) -> RawResponse:
        method, path, params, body = _request(operation_id, arguments)
        try:
            response = await self._transport.request(
                method,
                path,
                params=params,
                content=body,
                headers={"Content-Type": "application/json"} if body is not None else None,
            )
        except httpx.RequestError as exc:
            raise NetworkError(f"Network error for {operation_id}; outcome may be unknown") from exc
        return _response(response, operation_id)


def _credentials(
    api_key: str | None, api_secret: str | None, environment: Environment | str
) -> tuple[str, str, str]:
    key = api_key or os.environ.get("TRADING212_API_KEY")
    secret = api_secret or os.environ.get("TRADING212_API_SECRET")
    if not key or not secret:
        raise ValueError("API key and secret are required")
    if ":" in key:
        raise ValueError("API key cannot contain a colon")
    env = Environment(environment)
    return key, secret, f"https://{env.value}.trading212.com"


def _ttls(overrides: dict[str, float] | None) -> dict[str, float]:
    policies = {**DEFAULT_TTLS, **(overrides or {})}
    for ttl in policies.values():
        if type(ttl) not in (int, float) or not math.isfinite(ttl) or ttl < 0:
            raise ValueError("Cache TTLs must be finite, non-negative numbers")
    return policies


def _trading_config(
    environment: Environment | str,
    journal: object,
    enable_trading: bool,
    enable_live_trading: bool,
) -> OrderJournal | None:
    if type(enable_trading) is not bool or type(enable_live_trading) is not bool:
        raise TypeError("Trading opt-in flags must be booleans")
    if enable_live_trading and not enable_trading:
        raise ValueError("Live trading opt-in requires enable_trading")
    if not enable_trading:
        if journal is not None:
            raise ValueError("Providing a journal requires explicit enable_trading")
        return None
    if journal is None:
        raise ValueError("Trading requires a persistent OrderJournal")
    if not isinstance(journal, OrderJournal):
        raise TypeError("Trading requires a persistent OrderJournal")
    if Environment(environment) is Environment.LIVE and not enable_live_trading:
        raise ValueError("Live order submission requires explicit enable_live_trading")
    return journal


class Trading212:
    def __init__(
        self,
        api_key: str | None = None,
        api_secret: str | None = None,
        *,
        environment: Environment | str = Environment.DEMO,
        timeout: float = 10.0,
        transport: httpx.BaseTransport | None = None,
        cache: Cache | None = None,
        cache_ttls: dict[str, float] | None = None,
        rate_limiter: Limiter | None = None,
        rate_limit_strategy: str = "wait",
        max_rate_limit_wait: float = 30,
        retry_policy: RetryPolicy | None = None,
        order_journal: OrderJournal | None = None,
        enable_trading: bool = False,
        enable_live_trading: bool = False,
    ) -> None:
        journal = _trading_config(environment, order_journal, enable_trading, enable_live_trading)
        key, secret, base_url = _credentials(api_key, api_secret, environment)
        self.environment = Environment(environment)
        self._http = httpx.Client(
            base_url=base_url,
            auth=(key, secret),
            timeout=timeout,
            transport=transport,
            follow_redirects=False,
        )
        self.raw = Raw(self._http)
        self.services = Services(
            self.raw,
            cache if cache is not None else MemoryCache(),
            namespace(Environment(environment).value, key, secret),
            _ttls(cache_ttls),
            ReadExecutor(
                rate_limiter if rate_limiter is not None else RateLimiter(),
                strategy=rate_limit_strategy,
                max_wait=max_rate_limit_wait,
                policy=retry_policy or RetryPolicy(),
            ),
            journal,
        )
        self.app = Application(self.services)

    @classmethod
    def from_env(cls, **kwargs: Any) -> Trading212:
        return cls(**kwargs)

    def close(self) -> None:
        self._http.close()

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *args: object) -> None:
        self.close()


class AsyncTrading212:
    def __init__(
        self,
        api_key: str | None = None,
        api_secret: str | None = None,
        *,
        environment: Environment | str = Environment.DEMO,
        timeout: float = 10.0,
        transport: httpx.AsyncBaseTransport | None = None,
        cache: Cache | None = None,
        cache_ttls: dict[str, float] | None = None,
        rate_limiter: Limiter | None = None,
        rate_limit_strategy: str = "wait",
        max_rate_limit_wait: float = 30,
        retry_policy: RetryPolicy | None = None,
        order_journal: OrderJournal | None = None,
        enable_trading: bool = False,
        enable_live_trading: bool = False,
    ) -> None:
        journal = _trading_config(environment, order_journal, enable_trading, enable_live_trading)
        key, secret, base_url = _credentials(api_key, api_secret, environment)
        self.environment = Environment(environment)
        self._http = httpx.AsyncClient(
            base_url=base_url,
            auth=(key, secret),
            timeout=timeout,
            transport=transport,
            follow_redirects=False,
        )
        self.raw = AsyncRaw(self._http)
        self.services = AsyncServices(
            self.raw,
            cache if cache is not None else MemoryCache(),
            namespace(Environment(environment).value, key, secret),
            _ttls(cache_ttls),
            ReadExecutor(
                rate_limiter if rate_limiter is not None else RateLimiter(),
                strategy=rate_limit_strategy,
                max_wait=max_rate_limit_wait,
                policy=retry_policy or RetryPolicy(),
            ),
            journal,
        )
        self.app = AsyncApplication(self.services)

    @classmethod
    def from_env(cls, **kwargs: Any) -> AsyncTrading212:
        return cls(**kwargs)

    async def aclose(self) -> None:
        await self._http.aclose()

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(self, *args: object) -> None:
        await self.aclose()
