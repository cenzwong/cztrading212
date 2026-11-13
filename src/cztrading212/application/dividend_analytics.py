"""Pure aggregation of historical dividend cash receipts."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal

from .._generated.models import PaginatedResponseHistoryDividendItem
from ..exceptions import DataUnavailableError
from .common import currency, exact_sum, fraction, required
from .history_metadata import HistoryProvenance
from .models import DividendCurrencyTotal, SourceInfo


@dataclass(frozen=True, slots=True, kw_only=True)
class DividendTickerSummary:
    ticker: str
    year: int
    month: int
    currency: str
    amount: Decimal
    count: int


@dataclass(frozen=True, slots=True, kw_only=True)
class DividendPeriodSummary:
    year: int
    month: int | None
    currency: str
    amount: Decimal
    count: int


@dataclass(frozen=True, slots=True, kw_only=True)
class DividendChange:
    ticker: str | None
    year: int
    currency: str
    current_amount: Decimal
    previous_amount: Decimal
    change: Decimal
    change_fraction: Decimal | None


@dataclass(frozen=True, slots=True, kw_only=True)
class DividendIncomeReport:
    by_ticker: tuple[DividendTickerSummary, ...]
    annual: tuple[DividendPeriodSummary, ...]
    monthly: tuple[DividendPeriodSummary, ...]
    by_currency: tuple[DividendCurrencyTotal, ...]
    payment_count: int
    provenance: HistoryProvenance
    assembled_at: datetime


@dataclass(frozen=True, slots=True, kw_only=True)
class DividendTickerReport:
    entries: tuple[DividendTickerSummary, ...]
    provenance: HistoryProvenance
    assembled_at: datetime


@dataclass(frozen=True, slots=True, kw_only=True)
class DividendGrowthReport:
    annual_changes: tuple[DividendChange, ...]
    ticker_changes: tuple[DividendChange, ...]
    monthly: tuple[DividendPeriodSummary, ...]
    provenance: HistoryProvenance
    assembled_at: datetime


def _add(
    groups: dict[tuple[str, int, int, str], list[Decimal]],
    ticker: str,
    year: int,
    month: int,
    code: str,
    value: Decimal,
) -> None:
    groups.setdefault((ticker, year, month, code), []).append(value)


class DividendAccumulator:
    def __init__(self, limit: int, max_pages: int) -> None:
        self.limit = limit
        self.max_pages = max_pages
        self.groups: dict[tuple[str, int, int, str], list[Decimal]] = {}
        self.sources: list[SourceInfo] = []
        self.count = 0

    def add(self, page: PaginatedResponseHistoryDividendItem, page_source: SourceInfo) -> None:
        self.sources.append(page_source)
        for item in required(page.items, "dividends.items"):
            paid = required(item.paid_on, "dividends.paidOn")
            if paid.utcoffset() is None:
                raise DataUnavailableError("Dividend payment time has no timezone")
            date = paid.astimezone(UTC)
            nested_ticker = item.instrument.ticker if item.instrument else None
            if item.ticker and nested_ticker and item.ticker != nested_ticker:
                raise DataUnavailableError("Conflicting dividend ticker identifiers")
            ticker = item.ticker or nested_ticker
            ticker = required(ticker, "dividends.ticker")
            if not ticker or ticker != ticker.strip():
                raise DataUnavailableError("Invalid dividend ticker")
            code = currency(item.currency, "dividends.currency")
            value = required(item.amount, "dividends.amount")
            exact_sum([value])
            _add(self.groups, ticker, date.year, date.month, code, value)
            self.count += 1

    def result(self) -> DividendIncomeReport:
        ticker_entries = tuple(
            DividendTickerSummary(
                ticker=t, year=y, month=m, currency=c, amount=exact_sum(values), count=len(values)
            )
            for (t, y, m, c), values in sorted(self.groups.items())
        )
        annual: dict[tuple[int, str], list[Decimal]] = {}
        monthly: dict[tuple[int, int, str], list[Decimal]] = {}
        currencies: dict[str, list[Decimal]] = {}
        for (_, year, month, code), values in self.groups.items():
            annual.setdefault((year, code), []).extend(values)
            monthly.setdefault((year, month, code), []).extend(values)
            currencies.setdefault(code, []).extend(values)
        return DividendIncomeReport(
            by_ticker=ticker_entries,
            annual=tuple(
                DividendPeriodSummary(
                    year=y, month=None, currency=c, amount=exact_sum(v), count=len(v)
                )
                for (y, c), v in sorted(annual.items())
            ),
            monthly=tuple(
                DividendPeriodSummary(
                    year=y, month=m, currency=c, amount=exact_sum(v), count=len(v)
                )
                for (y, m, c), v in sorted(monthly.items())
            ),
            by_currency=tuple(
                DividendCurrencyTotal(currency=c, amount=exact_sum(v), count=len(v))
                for c, v in sorted(currencies.items())
            ),
            payment_count=self.count,
            provenance=HistoryProvenance(
                operation="dividends",
                scope="all returned dividend history",
                pages=tuple(self.sources),
                page_count=len(self.sources),
                record_count=self.count,
                limit=self.limit,
                max_pages=self.max_pages,
            ),
            assembled_at=datetime.now(UTC),
        )


def growth(report: DividendIncomeReport) -> DividendGrowthReport:
    annual = {(e.year, e.currency): e.amount for e in report.annual}
    tickers: dict[tuple[str, int, str], list[Decimal]] = {}
    for item in report.by_ticker:
        tickers.setdefault((item.ticker, item.year, item.currency), []).append(item.amount)
    totals = {key: exact_sum(values) for key, values in tickers.items()}
    # Only compare adjacent years that both have observed payments; an absent
    # year is not proof of zero income across an unknown broker history range.
    changes: list[DividendChange] = []
    ticker_changes: list[DividendChange] = []
    for (year, code), value in sorted(annual.items()):
        previous = annual.get((year - 1, code))
        if previous is not None:
            delta = exact_sum([value, previous.copy_negate()])
            changes.append(
                DividendChange(
                    ticker=None,
                    year=year,
                    currency=code,
                    current_amount=value,
                    previous_amount=previous,
                    change=delta,
                    change_fraction=fraction(delta, previous) if previous > 0 else None,
                )
            )
    for (ticker, year, code), value in sorted(totals.items()):
        previous = totals.get((ticker, year - 1, code))
        if previous is not None:
            delta = exact_sum([value, previous.copy_negate()])
            ticker_changes.append(
                DividendChange(
                    ticker=ticker,
                    year=year,
                    currency=code,
                    current_amount=value,
                    previous_amount=previous,
                    change=delta,
                    change_fraction=fraction(delta, previous) if previous > 0 else None,
                )
            )
    return DividendGrowthReport(
        annual_changes=tuple(changes),
        ticker_changes=tuple(ticker_changes),
        monthly=report.monthly,
        provenance=report.provenance,
        assembled_at=datetime.now(UTC),
    )
