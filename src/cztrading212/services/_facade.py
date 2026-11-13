"""Service layer components."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from ..core.cache import Cache
from ..core.cache_policy import cache_key
from ..core.reliability import ReadExecutor
from ..core.safety.journal import OrderJournal
from ..core.safety.submission import AsyncOrderSubmissionService, OrderSubmissionService
from ._common import ServiceResult, _cached, _store, _typed  # pyright: ignore[reportPrivateUsage]
from .account import AccountService, AsyncAccountService
from .history import AsyncHistoryService, HistoryService
from .instruments import AsyncInstrumentsService, InstrumentsService
from .orders import AsyncOrdersService, OrdersService
from .positions import AsyncPositionsService, PositionsService

if TYPE_CHECKING:
    from ..client import AsyncRaw, Raw


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
        from ..client import OPERATIONS

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
        from ..client import OPERATIONS

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
