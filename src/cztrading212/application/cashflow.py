"""Currency-separated cash transaction analysis with bounded history traversal."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from typing import TYPE_CHECKING, cast

from .._generated.models import PaginatedResponseHistoryTransactionItem
from ..exceptions import DataUnavailableError
from .common import currency, exact_sum, required, source
from .history_metadata import HistoryProvenance
from .models import SourceInfo

if TYPE_CHECKING:
    from ..services import AsyncServices, Services

# TRANSFER does not establish whether the movement is external to the account.
CATEGORIES = {
    "DEPOSIT": "deposits",
    "WITHDRAW": "withdrawals",
    "FEE": "other",
    "INTEREST_ON_FREE_CASH": "other",
    "LENDING_INTEREST": "other",
}


@dataclass(frozen=True, slots=True, kw_only=True)
class CashFlowCategory:
    category: str
    transaction_type: str | None
    currency: str
    amount: Decimal
    count: int


@dataclass(frozen=True, slots=True, kw_only=True)
class CashFlowPeriod:
    year: int
    month: int
    categories: tuple[CashFlowCategory, ...]


@dataclass(frozen=True, slots=True, kw_only=True)
class CashFlowSummary:
    categories: tuple[CashFlowCategory, ...]
    provenance: HistoryProvenance
    assembled_at: datetime


@dataclass(frozen=True, slots=True, kw_only=True)
class CashFlowMonthly:
    periods: tuple[CashFlowPeriod, ...]
    provenance: HistoryProvenance
    assembled_at: datetime


def _categories(
    groups: dict[tuple[str, str | None, str], list[Decimal]],
) -> tuple[CashFlowCategory, ...]:
    return tuple(
        CashFlowCategory(
            category=category,
            transaction_type=kind,
            currency=code,
            amount=exact_sum(values),
            count=len(values),
        )
        for (category, kind, code), values in sorted(
            groups.items(), key=lambda item: (item[0][0], item[0][1] or "", item[0][2])
        )
    )


class _CashFlowAccumulator:
    def __init__(self, limit: int, max_pages: int, monthly: bool) -> None:
        self.limit, self.max_pages, self.monthly = limit, max_pages, monthly
        self.sources: list[SourceInfo] = []
        self.count = 0
        self.groups: dict[tuple[str, str | None, str], list[Decimal]] = {}
        self.periods: dict[tuple[int, int], dict[tuple[str, str | None, str], list[Decimal]]] = {}

    def add(self, page: PaginatedResponseHistoryTransactionItem, page_source: SourceInfo) -> None:
        self.sources.append(page_source)
        for item in required(page.items, "transactions.items"):
            code = currency(item.currency, "transactions.currency")
            amount = required(item.amount, "transactions.amount")
            exact_sum([amount])
            key = (CATEGORIES.get(item.type or "", "unclassified"), item.type, code)
            self.groups.setdefault(key, []).append(amount)
            if self.monthly:
                date = required(item.date_time, "transactions.dateTime")
                if date.utcoffset() is None:
                    raise DataUnavailableError("Transaction time has no timezone")
                date = date.astimezone(UTC)
                self.periods.setdefault((date.year, date.month), {}).setdefault(key, []).append(
                    amount
                )
            self.count += 1

    def provenance(self) -> HistoryProvenance:
        return HistoryProvenance(
            operation="transactions",
            scope="all returned transaction history",
            pages=tuple(self.sources),
            page_count=len(self.sources),
            record_count=self.count,
            limit=self.limit,
            max_pages=self.max_pages,
        )

    def summary(self) -> CashFlowSummary:
        return CashFlowSummary(
            categories=_categories(self.groups),
            provenance=self.provenance(),
            assembled_at=datetime.now(UTC),
        )

    def monthly_result(self) -> CashFlowMonthly:
        return CashFlowMonthly(
            periods=tuple(
                CashFlowPeriod(year=y, month=m, categories=_categories(groups))
                for (y, m), groups in sorted(self.periods.items())
            ),
            provenance=self.provenance(),
            assembled_at=datetime.now(UTC),
        )


class CashFlow:
    def __init__(self, services: Services) -> None:
        self._services = services

    def _collect(
        self, limit: int, max_pages: int, refresh: bool, monthly: bool
    ) -> _CashFlowAccumulator:
        result = _CashFlowAccumulator(limit, max_pages, monthly)
        for page in self._services.history.iter_pages(
            "transactions", limit=limit, max_pages=max_pages, refresh=refresh
        ):
            result.add(
                cast(PaginatedResponseHistoryTransactionItem, page.data),
                source(page, operation="transactions", scope="history page"),
            )
        return result

    def summary(
        self, *, limit: int = 50, max_pages: int = 20, refresh: bool = False
    ) -> CashFlowSummary:
        return self._collect(limit, max_pages, refresh, False).summary()

    def monthly(
        self, *, limit: int = 50, max_pages: int = 20, refresh: bool = False
    ) -> CashFlowMonthly:
        return self._collect(limit, max_pages, refresh, True).monthly_result()


class AsyncCashFlow:
    def __init__(self, services: AsyncServices) -> None:
        self._services = services

    async def _collect(
        self, limit: int, max_pages: int, refresh: bool, monthly: bool
    ) -> _CashFlowAccumulator:
        result = _CashFlowAccumulator(limit, max_pages, monthly)
        async for page in self._services.history.iter_pages(
            "transactions", limit=limit, max_pages=max_pages, refresh=refresh
        ):
            result.add(
                cast(PaginatedResponseHistoryTransactionItem, page.data),
                source(page, operation="transactions", scope="history page"),
            )
        return result

    async def summary(
        self, *, limit: int = 50, max_pages: int = 20, refresh: bool = False
    ) -> CashFlowSummary:
        return (await self._collect(limit, max_pages, refresh, False)).summary()

    async def monthly(
        self, *, limit: int = 50, max_pages: int = 20, refresh: bool = False
    ) -> CashFlowMonthly:
        return (await self._collect(limit, max_pages, refresh, True)).monthly_result()
