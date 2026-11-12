"""Historical order counts and streaming transaction CSV exports."""

from __future__ import annotations

import csv
from collections import Counter
from datetime import UTC, datetime
from typing import TYPE_CHECKING, TextIO, cast

from .._generated.models import (
    PaginatedResponseHistoricalOrder,
    PaginatedResponseHistoryTransactionItem,
)
from .common import required, source
from .models import CategoryCount, ExportResult, HistoricalOrderAnalysis, SourceInfo

if TYPE_CHECKING:
    from ..services import AsyncServices, Services


class _OrderCounts:
    def __init__(self) -> None:
        self.status: Counter[str | None] = Counter()
        self.side: Counter[str | None] = Counter()
        self.sources: list[SourceInfo] = []
        self.count = 0

    def add(self, page: PaginatedResponseHistoricalOrder, page_source: SourceInfo) -> None:
        self.sources.append(page_source)
        for item in required(page.items, "history.orders.items"):
            self.count += 1
            self.status[item.order.status if item.order else None] += 1
            self.side[item.order.side if item.order else None] += 1

    def result(self) -> HistoricalOrderAnalysis:
        def groups(values: Counter[str | None]) -> tuple[CategoryCount, ...]:
            return tuple(
                CategoryCount(category=k, count=v)
                for k, v in sorted(
                    values.items(), key=lambda item: (item[0] is not None, item[0] or "")
                )
            )

        return HistoricalOrderAnalysis(
            item_count=self.count,
            status_counts=groups(self.status),
            side_counts=groups(self.side),
            sources=tuple(self.sources),
            assembled_at=datetime.now(UTC),
        )


class _TransactionCSV:
    def __init__(self, output: TextIO) -> None:
        self.writer = csv.writer(output, lineterminator="\n")
        self.writer.writerow(("reference", "date_time", "type", "currency", "amount"))
        self.sources: list[SourceInfo] = []
        self.count = 0

    def add(self, page: PaginatedResponseHistoryTransactionItem, page_source: SourceInfo) -> None:
        self.sources.append(page_source)
        for item in required(page.items, "history.transactions.items"):
            self.writer.writerow(
                (
                    item.reference or "",
                    item.date_time.isoformat() if item.date_time else "",
                    item.type or "",
                    item.currency or "",
                    str(item.amount) if item.amount is not None else "",
                )
            )
            self.count += 1

    def result(self) -> ExportResult:
        return ExportResult(
            row_count=self.count, sources=tuple(self.sources), assembled_at=datetime.now(UTC)
        )


class History:
    def __init__(self, services: Services) -> None:
        self._services = services

    def order_analysis(
        self, *, limit: int = 50, max_pages: int = 20, refresh: bool = False
    ) -> HistoricalOrderAnalysis:
        result = _OrderCounts()
        for page in self._services.history.iter_pages(
            "orders_1", limit=limit, max_pages=max_pages, refresh=refresh
        ):
            result.add(cast(PaginatedResponseHistoricalOrder, page.data), source(page))
        return result.result()

    def transactions_csv(
        self, output: TextIO, *, limit: int = 50, max_pages: int = 20, refresh: bool = False
    ) -> ExportResult:
        result = _TransactionCSV(output)
        for page in self._services.history.iter_pages(
            "transactions", limit=limit, max_pages=max_pages, refresh=refresh
        ):
            result.add(cast(PaginatedResponseHistoryTransactionItem, page.data), source(page))
        return result.result()


class AsyncHistory:
    def __init__(self, services: AsyncServices) -> None:
        self._services = services

    async def order_analysis(
        self, *, limit: int = 50, max_pages: int = 20, refresh: bool = False
    ) -> HistoricalOrderAnalysis:
        result = _OrderCounts()
        async for page in self._services.history.iter_pages(
            "orders_1", limit=limit, max_pages=max_pages, refresh=refresh
        ):
            result.add(cast(PaginatedResponseHistoricalOrder, page.data), source(page))
        return result.result()

    async def transactions_csv(
        self, output: TextIO, *, limit: int = 50, max_pages: int = 20, refresh: bool = False
    ) -> ExportResult:
        result = _TransactionCSV(output)
        async for page in self._services.history.iter_pages(
            "transactions", limit=limit, max_pages=max_pages, refresh=refresh
        ):
            result.add(cast(PaginatedResponseHistoryTransactionItem, page.data), source(page))
        return result.result()
