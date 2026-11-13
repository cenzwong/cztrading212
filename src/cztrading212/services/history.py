"""Service layer components."""

from __future__ import annotations

from collections.abc import AsyncIterator, Iterator
from typing import TYPE_CHECKING, Any, cast

from .._generated import models

if TYPE_CHECKING:
    from ..pagination import HistoryPage
    from ._common import ServiceResult
    from ._facade import AsyncServices, Services


class HistoryService:
    def __init__(self, services: Services) -> None:
        self._services = services

    def iter_pages(
        self, operation_id: str, *, max_pages: int = 20, refresh: bool = False, **arguments: Any
    ) -> Iterator[ServiceResult[HistoryPage]]:
        from ..pagination import iter_pages

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


class AsyncHistoryService:
    def __init__(self, services: AsyncServices) -> None:
        self._services = services

    def iter_pages(
        self, operation_id: str, *, max_pages: int = 20, refresh: bool = False, **arguments: Any
    ) -> AsyncIterator[ServiceResult[HistoryPage]]:
        from ..pagination import aiter_pages

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
