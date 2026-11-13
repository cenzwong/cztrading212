"""Schema-validated, cached read services shared by both client variants."""

from __future__ import annotations

from collections.abc import AsyncIterator, Iterator
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from typing import TYPE_CHECKING, Any, Generic, TypeVar, cast

from . import codec
from ._generated import models, parsers
from .application.trading import ConditionalOrderDraft, MarketOrderDraft, Trading
from .core.cache import Cache
from .core.cache_policy import cache_key
from .core.reliability import ReadExecutor
from .core.safety.journal import JournalEntry, OrderJournal
from .core.safety.submission import (
    AsyncOrderSubmissionService,
    OrderSubmission,
    OrderSubmissionService,
    Reconciliation,
    reconciliation_result,
    reconciliation_target,
)
from .exceptions import APIError, JournalStateError, ResponseValidationError, TradingDisabledError

if TYPE_CHECKING:
    from .client import AsyncRaw, Raw
    from .pagination import HistoryPage

T = TypeVar("T")


@dataclass(frozen=True, slots=True, kw_only=True)
class ServiceResult(Generic[T]):
    """A value and the time it was obtained from the broker, including cache hits."""

    data: T
    fetched_at: datetime
    from_cache: bool


def _typed(operation: str, result: Any, *, status_code: int = 200) -> Any:
    if status_code != 200:
        raise ResponseValidationError(f"{operation}: undocumented success status {status_code}")
    parser = parsers.OPERATION_PARSERS.get(operation)
    if parser is None:
        raise ResponseValidationError(f"{operation}: no generated response parser is available")
    return parser(result)


def _cached(cache: Cache, key: str, operation: str) -> ServiceResult[Any] | None:
    encoded = cache.get(key)
    if encoded is None:
        return None
    try:
        envelope = codec.loads(encoded)
        if not isinstance(envelope, dict):
            raise TypeError("Invalid cache record")
        envelope = cast(dict[str, Any], envelope)
        if not isinstance(envelope.get("fetched_at"), str):
            raise TypeError("Invalid cache timestamp")
        timestamp = datetime.fromisoformat(envelope["fetched_at"])
        if timestamp.tzinfo is None:
            raise ValueError("Cache timestamp is missing a timezone")
        data = _typed(operation, envelope["data"])
    except (ValueError, KeyError, TypeError, ResponseValidationError) as exc:
        cache.invalidate(key)
        raise ResponseValidationError(f"{operation}: invalid cached response") from exc
    return ServiceResult(data=data, fetched_at=timestamp, from_cache=True)


def _store(
    cache: Cache,
    namespace: str,
    generation: int,
    key: str,
    raw: Any,
    ttl: float,
    fetched_at: datetime,
) -> None:
    cache.put_if_generation(
        namespace,
        generation,
        key,
        codec.dumps({"fetched_at": fetched_at.isoformat(), "data": raw}),
        ttl,
    )


class Services:
    """Read-only services. Mutations require a separate safety design."""

    def __init__(
        self,
        raw: Raw,
        cache: Cache,
        namespace: str,
        ttls: dict[str, float],
        executor: ReadExecutor,
        journal: OrderJournal | None = None,
    ) -> None:
        self.raw = raw
        self.cache = cache
        self._namespace = namespace
        self._ttls = ttls
        self._executor = executor
        self._cache_suspended = False
        self.account = AccountService(self)
        self.positions = PositionsService(self)
        submission = (
            OrderSubmissionService(
                self.raw,
                journal,
                self._namespace,
                self._executor,
                self.invalidate_all,
                self._suspend_cache,
            )
            if journal is not None
            else None
        )
        self.orders = OrdersService(self, submission)
        self.instruments = InstrumentsService(self)
        self.history = HistoryService(self)

    def call(self, operation_id: str, *, refresh: bool = False, **arguments: Any) -> Any:
        return self.call_with_metadata(operation_id, refresh=refresh, **arguments).data

    def call_with_metadata(
        self, operation_id: str, *, refresh: bool = False, **arguments: Any
    ) -> ServiceResult[Any]:
        from .client import OPERATIONS

        if operation_id not in OPERATIONS:
            raise ValueError(f"Unknown Trading 212 operation: {operation_id}")
        if OPERATIONS[operation_id]["method"] != "GET":
            raise ValueError("Services currently support read-only operations")
        ttl = self._ttls.get(operation_id, 0) if not self._cache_suspended else 0
        generation = self.cache.generation(self._namespace) if ttl > 0 else 0
        key = cache_key(self._namespace, operation_id, arguments, generation)
        if ttl > 0 and not refresh:
            cached = _cached(self.cache, key, operation_id)
            if cached is not None and self.cache.generation(self._namespace) == generation:
                return cached
            generation = self.cache.generation(self._namespace)
            key = cache_key(self._namespace, operation_id, arguments, generation)
        response = self._executor.call(
            operation_id, lambda: self.raw.call_with_metadata(operation_id, **arguments)
        )
        raw = response.data
        fetched_at = datetime.now(UTC)
        data = _typed(operation_id, raw, status_code=response.status_code)
        if ttl > 0:
            _store(self.cache, self._namespace, generation, key, raw, ttl, fetched_at)
        return ServiceResult(data=data, fetched_at=fetched_at, from_cache=False)

    def invalidate(self, operation_id: str, **arguments: Any) -> None:
        # A key deletion alone allows an earlier in-flight request to repopulate
        # it. Advance the shared namespace generation to reject that fill.
        self.invalidate_all()

    def invalidate_all(self) -> None:
        """Invalidate this credential/environment namespace, including in-flight cache fills."""
        self.cache.invalidate_namespace(self._namespace)

    def _suspend_cache(self) -> None:
        self._cache_suspended = True


class AsyncServices:
    def __init__(
        self,
        raw: AsyncRaw,
        cache: Cache,
        namespace: str,
        ttls: dict[str, float],
        executor: ReadExecutor,
        journal: OrderJournal | None = None,
    ) -> None:
        self.raw = raw
        self.cache = cache
        self._namespace = namespace
        self._ttls = ttls
        self._executor = executor
        self._cache_suspended = False
        self.account = AsyncAccountService(self)
        self.positions = AsyncPositionsService(self)
        submission = (
            AsyncOrderSubmissionService(
                self.raw,
                journal,
                self._namespace,
                self._executor,
                self.invalidate_all,
                self._suspend_cache,
            )
            if journal is not None
            else None
        )
        self.orders = AsyncOrdersService(self, submission)
        self.instruments = AsyncInstrumentsService(self)
        self.history = AsyncHistoryService(self)

    async def call(self, operation_id: str, *, refresh: bool = False, **arguments: Any) -> Any:
        return (await self.call_with_metadata(operation_id, refresh=refresh, **arguments)).data

    async def call_with_metadata(
        self, operation_id: str, *, refresh: bool = False, **arguments: Any
    ) -> ServiceResult[Any]:
        from .client import OPERATIONS

        if operation_id not in OPERATIONS:
            raise ValueError(f"Unknown Trading 212 operation: {operation_id}")
        if OPERATIONS[operation_id]["method"] != "GET":
            raise ValueError("Services currently support read-only operations")
        ttl = self._ttls.get(operation_id, 0) if not self._cache_suspended else 0
        generation = self.cache.generation(self._namespace) if ttl > 0 else 0
        key = cache_key(self._namespace, operation_id, arguments, generation)
        if ttl > 0 and not refresh:
            cached = _cached(self.cache, key, operation_id)
            if cached is not None and self.cache.generation(self._namespace) == generation:
                return cached
            generation = self.cache.generation(self._namespace)
            key = cache_key(self._namespace, operation_id, arguments, generation)
        response = await self._executor.call_async(
            operation_id, lambda: self.raw.call_with_metadata(operation_id, **arguments)
        )
        raw = response.data
        fetched_at = datetime.now(UTC)
        data = _typed(operation_id, raw, status_code=response.status_code)
        if ttl > 0:
            _store(self.cache, self._namespace, generation, key, raw, ttl, fetched_at)
        return ServiceResult(data=data, fetched_at=fetched_at, from_cache=False)

    def invalidate(self, operation_id: str, **arguments: Any) -> None:
        self.invalidate_all()

    def invalidate_all(self) -> None:
        """Invalidate this credential/environment namespace, including in-flight cache fills."""
        self.cache.invalidate_namespace(self._namespace)

    def _suspend_cache(self) -> None:
        self._cache_suspended = True


class AccountService:
    def __init__(self, services: Services) -> None:
        self._services = services

    def summary(self, *, refresh: bool = False) -> models.AccountSummary:
        return cast(
            models.AccountSummary, self._services.call("getAccountSummary", refresh=refresh)
        )


class PositionsService:
    def __init__(self, services: Services) -> None:
        self._services = services

    def list(
        self, *, ticker: str | None = None, refresh: bool = False
    ) -> tuple[models.Position, ...]:
        return cast(
            tuple[models.Position, ...],
            self._services.call("getPositions", ticker=ticker, refresh=refresh),
        )


class OrdersService:
    def __init__(self, services: Services, submission: OrderSubmissionService | None) -> None:
        self._services = services
        self._submission = submission

    def _enabled(self) -> OrderSubmissionService:
        if self._submission is None:
            raise TradingDisabledError(
                "Order submission requires explicit opt-in and an order journal"
            )
        return self._submission

    def list(self, *, refresh: bool = False) -> tuple[models.Order, ...]:
        return cast(tuple[models.Order, ...], self._services.call("orders", refresh=refresh))

    def prepare_order(
        self, intent_id: str, draft: MarketOrderDraft | ConditionalOrderDraft
    ) -> JournalEntry:
        return self._enabled().prepare(intent_id, draft.operation_id, draft.request_body())

    def submit_draft(
        self, intent_id: str, draft: MarketOrderDraft | ConditionalOrderDraft
    ) -> OrderSubmission:
        return self._enabled().submit(intent_id, draft.operation_id, draft.request_body())

    def submit_market(
        self, *, ticker: str, quantity: Decimal | int, intent_id: str, extended_hours: bool = False
    ) -> OrderSubmission:
        submission = self._enabled()
        draft = Trading().prepare_market(
            ticker=ticker, quantity=quantity, extended_hours=extended_hours
        )
        return submission.submit(intent_id, draft.operation_id, draft.request_body())

    def submit_prepared(self, intent_id: str) -> OrderSubmission:
        return self._enabled().submit_prepared(intent_id)

    def intent(self, intent_id: str) -> JournalEntry | None:
        return self._enabled().entry(intent_id)

    def reconcile(self, intent_id: str, *, candidate_order_id: int | None = None) -> Reconciliation:
        entry = self._enabled().entry(intent_id)
        if entry is None:
            raise JournalStateError("Intent was not found")
        target = reconciliation_target(entry, candidate_order_id)
        if target is None:
            return reconciliation_result(entry, None, None)
        try:
            result = self._services.call_with_metadata("orderById", id=target, refresh=True)
        except APIError as exc:
            if exc.status_code != 404:
                raise
            return reconciliation_result(entry, target, None)
        return reconciliation_result(
            entry, target, cast(models.Order, result.data), result.fetched_at
        )


class InstrumentsService:
    def __init__(self, services: Services) -> None:
        self._services = services

    def list(self, *, refresh: bool = False) -> tuple[models.TradableInstrument, ...]:
        return cast(
            tuple[models.TradableInstrument, ...],
            self._services.call("instruments", refresh=refresh),
        )

    def exchanges(self, *, refresh: bool = False) -> tuple[models.Exchange, ...]:
        return cast(tuple[models.Exchange, ...], self._services.call("exchanges", refresh=refresh))


class HistoryService:
    def __init__(self, services: Services) -> None:
        self._services = services

    def iter_pages(
        self, operation_id: str, *, max_pages: int = 20, refresh: bool = False, **arguments: Any
    ) -> Iterator[ServiceResult[HistoryPage]]:
        from .pagination import iter_pages

        return iter_pages(
            self._services, operation_id, max_pages=max_pages, refresh=refresh, **arguments
        )

    def dividends(
        self,
        *,
        cursor: int | None = None,
        ticker: str | None = None,
        limit: int | None = None,
        refresh: bool = False,
    ) -> models.PaginatedResponseHistoryDividendItem:
        return cast(
            models.PaginatedResponseHistoryDividendItem,
            self._services.call(
                "dividends", cursor=cursor, ticker=ticker, limit=limit, refresh=refresh
            ),
        )

    def orders(
        self,
        *,
        cursor: int | None = None,
        ticker: str | None = None,
        limit: int | None = None,
        refresh: bool = False,
    ) -> models.PaginatedResponseHistoricalOrder:
        return cast(
            models.PaginatedResponseHistoricalOrder,
            self._services.call(
                "orders_1", cursor=cursor, ticker=ticker, limit=limit, refresh=refresh
            ),
        )

    def transactions(
        self,
        *,
        cursor: str | None = None,
        time: str | None = None,
        limit: int | None = None,
        refresh: bool = False,
    ) -> models.PaginatedResponseHistoryTransactionItem:
        return cast(
            models.PaginatedResponseHistoryTransactionItem,
            self._services.call(
                "transactions", cursor=cursor, time=time, limit=limit, refresh=refresh
            ),
        )


class AsyncAccountService:
    def __init__(self, services: AsyncServices) -> None:
        self._services = services

    async def summary(self, *, refresh: bool = False) -> models.AccountSummary:
        return cast(
            models.AccountSummary, await self._services.call("getAccountSummary", refresh=refresh)
        )


class AsyncPositionsService:
    def __init__(self, services: AsyncServices) -> None:
        self._services = services

    async def list(
        self, *, ticker: str | None = None, refresh: bool = False
    ) -> tuple[models.Position, ...]:
        return cast(
            tuple[models.Position, ...],
            await self._services.call("getPositions", ticker=ticker, refresh=refresh),
        )


class AsyncOrdersService:
    def __init__(
        self, services: AsyncServices, submission: AsyncOrderSubmissionService | None
    ) -> None:
        self._services = services
        self._submission = submission

    def _enabled(self) -> AsyncOrderSubmissionService:
        if self._submission is None:
            raise TradingDisabledError(
                "Order submission requires explicit opt-in and an order journal"
            )
        return self._submission

    async def list(self, *, refresh: bool = False) -> tuple[models.Order, ...]:
        return cast(tuple[models.Order, ...], await self._services.call("orders", refresh=refresh))

    def prepare_order(
        self, intent_id: str, draft: MarketOrderDraft | ConditionalOrderDraft
    ) -> JournalEntry:
        return self._enabled().prepare(intent_id, draft.operation_id, draft.request_body())

    async def submit_draft(
        self, intent_id: str, draft: MarketOrderDraft | ConditionalOrderDraft
    ) -> OrderSubmission:
        return await self._enabled().submit(intent_id, draft.operation_id, draft.request_body())

    async def submit_market(
        self, *, ticker: str, quantity: Decimal | int, intent_id: str, extended_hours: bool = False
    ) -> OrderSubmission:
        submission = self._enabled()
        draft = Trading().prepare_market(
            ticker=ticker, quantity=quantity, extended_hours=extended_hours
        )
        return await submission.submit(intent_id, draft.operation_id, draft.request_body())

    async def submit_prepared(self, intent_id: str) -> OrderSubmission:
        return await self._enabled().submit_prepared(intent_id)

    def intent(self, intent_id: str) -> JournalEntry | None:
        return self._enabled().entry(intent_id)

    async def reconcile(
        self, intent_id: str, *, candidate_order_id: int | None = None
    ) -> Reconciliation:
        entry = self._enabled().entry(intent_id)
        if entry is None:
            raise JournalStateError("Intent was not found")
        target = reconciliation_target(entry, candidate_order_id)
        if target is None:
            return reconciliation_result(entry, None, None)
        try:
            result = await self._services.call_with_metadata("orderById", id=target, refresh=True)
        except APIError as exc:
            if exc.status_code != 404:
                raise
            return reconciliation_result(entry, target, None)
        return reconciliation_result(
            entry, target, cast(models.Order, result.data), result.fetched_at
        )


class AsyncInstrumentsService:
    def __init__(self, services: AsyncServices) -> None:
        self._services = services

    async def list(self, *, refresh: bool = False) -> tuple[models.TradableInstrument, ...]:
        return cast(
            tuple[models.TradableInstrument, ...],
            await self._services.call("instruments", refresh=refresh),
        )

    async def exchanges(self, *, refresh: bool = False) -> tuple[models.Exchange, ...]:
        return cast(
            tuple[models.Exchange, ...], await self._services.call("exchanges", refresh=refresh)
        )


class AsyncHistoryService:
    def __init__(self, services: AsyncServices) -> None:
        self._services = services

    def iter_pages(
        self, operation_id: str, *, max_pages: int = 20, refresh: bool = False, **arguments: Any
    ) -> AsyncIterator[ServiceResult[HistoryPage]]:
        from .pagination import aiter_pages

        return aiter_pages(
            self._services, operation_id, max_pages=max_pages, refresh=refresh, **arguments
        )

    async def dividends(
        self,
        *,
        cursor: int | None = None,
        ticker: str | None = None,
        limit: int | None = None,
        refresh: bool = False,
    ) -> models.PaginatedResponseHistoryDividendItem:
        return cast(
            models.PaginatedResponseHistoryDividendItem,
            await self._services.call(
                "dividends", cursor=cursor, ticker=ticker, limit=limit, refresh=refresh
            ),
        )

    async def orders(
        self,
        *,
        cursor: int | None = None,
        ticker: str | None = None,
        limit: int | None = None,
        refresh: bool = False,
    ) -> models.PaginatedResponseHistoricalOrder:
        return cast(
            models.PaginatedResponseHistoricalOrder,
            await self._services.call(
                "orders_1", cursor=cursor, ticker=ticker, limit=limit, refresh=refresh
            ),
        )

    async def transactions(
        self,
        *,
        cursor: str | None = None,
        time: str | None = None,
        limit: int | None = None,
        refresh: bool = False,
    ) -> models.PaginatedResponseHistoryTransactionItem:
        return cast(
            models.PaginatedResponseHistoryTransactionItem,
            await self._services.call(
                "transactions", cursor=cursor, time=time, limit=limit, refresh=refresh
            ),
        )
