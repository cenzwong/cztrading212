"""Bounded dividend aggregation in the broker-reported currencies."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from typing import TYPE_CHECKING, cast

from .._generated.models import PaginatedResponseHistoryDividendItem
from ..exceptions import DataUnavailableError
from .common import currency, exact_sum, required, source
from .models import DividendCurrencyTotal, DividendSummary, SourceInfo

if TYPE_CHECKING:
    from ..services import AsyncServices, Services


class _Accumulator:
    def __init__(self, year: int | None) -> None:
        if year is not None and (type(year) is not int or not 1 <= year <= 9999):
            raise ValueError("year must be an integer between 1 and 9999")
        self.year = year
        self.totals: dict[str, tuple[Decimal, int]] = {}
        self.sources: list[SourceInfo] = []

    def add(self, page: PaginatedResponseHistoryDividendItem, page_source: SourceInfo) -> None:
        self.sources.append(page_source)
        for index, item in enumerate(required(page.items, "dividends.items")):
            if self.year is not None:
                paid = required(item.paid_on, f"dividends.items[{index}].paidOn")
                if paid.utcoffset() is None:
                    raise DataUnavailableError("Dividend payment time has no timezone")
                if paid.astimezone(UTC).year != self.year:
                    continue
            code = currency(item.currency, f"dividends.items[{index}].currency")
            amount = required(item.amount, f"dividends.items[{index}].amount")
            previous, count = self.totals.get(code, (Decimal(0), 0))
            self.totals[code] = (exact_sum([previous, amount]), count + 1)

    def result(self) -> DividendSummary:
        return DividendSummary(
            year=self.year,
            totals=tuple(
                DividendCurrencyTotal(currency=code, amount=amount, count=count)
                for code, (amount, count) in sorted(self.totals.items())
            ),
            sources=tuple(self.sources),
            assembled_at=datetime.now(UTC),
        )


class Dividends:
    def __init__(self, services: Services) -> None:
        self._services = services

    def summary(
        self,
        *,
        year: int | None = None,
        limit: int = 50,
        max_pages: int = 20,
        refresh: bool = False,
    ) -> DividendSummary:
        accumulator = _Accumulator(year)
        for page in self._services.history.iter_pages(
            "dividends", limit=limit, max_pages=max_pages, refresh=refresh
        ):
            accumulator.add(cast(PaginatedResponseHistoryDividendItem, page.data), source(page))
        return accumulator.result()


class AsyncDividends:
    def __init__(self, services: AsyncServices) -> None:
        self._services = services

    async def summary(
        self,
        *,
        year: int | None = None,
        limit: int = 50,
        max_pages: int = 20,
        refresh: bool = False,
    ) -> DividendSummary:
        accumulator = _Accumulator(year)
        async for page in self._services.history.iter_pages(
            "dividends", limit=limit, max_pages=max_pages, refresh=refresh
        ):
            accumulator.add(cast(PaginatedResponseHistoryDividendItem, page.data), source(page))
        return accumulator.result()
