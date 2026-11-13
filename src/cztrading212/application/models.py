"""Immutable results for user-oriented workflows."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from .._generated.models import Cash, Investments, Position


@dataclass(frozen=True, slots=True, kw_only=True)
class SourceInfo:
    fetched_at: datetime
    from_cache: bool
    operation: str | None = None
    scope: str | None = None
    completeness: str = "unknown"


@dataclass(frozen=True, slots=True, kw_only=True)
class PortfolioSnapshot:
    currency: str
    total_value: Decimal
    cash: Cash | None
    investments: Investments | None
    positions: tuple[Position, ...]
    account_source: SourceInfo
    positions_source: SourceInfo
    assembled_at: datetime

    @property
    def position_count(self) -> int:
        return len(self.positions)


@dataclass(frozen=True, slots=True, kw_only=True)
class AllocationEntry:
    ticker: str
    current_value: Decimal
    fraction: Decimal | None


@dataclass(frozen=True, slots=True, kw_only=True)
class PortfolioAllocation:
    currency: str
    positions_value: Decimal
    entries: tuple[AllocationEntry, ...]
    account_source: SourceInfo
    positions_source: SourceInfo
    assembled_at: datetime


@dataclass(frozen=True, slots=True, kw_only=True)
class PortfolioReport:
    snapshot: PortfolioSnapshot
    allocation: PortfolioAllocation


@dataclass(frozen=True, slots=True, kw_only=True)
class DividendCurrencyTotal:
    currency: str
    amount: Decimal
    count: int


@dataclass(frozen=True, slots=True, kw_only=True)
class DividendSummary:
    year: int | None
    totals: tuple[DividendCurrencyTotal, ...]
    sources: tuple[SourceInfo, ...]
    assembled_at: datetime


@dataclass(frozen=True, slots=True, kw_only=True)
class CategoryCount:
    category: str | None
    count: int


@dataclass(frozen=True, slots=True, kw_only=True)
class HistoricalOrderAnalysis:
    item_count: int
    status_counts: tuple[CategoryCount, ...]
    side_counts: tuple[CategoryCount, ...]
    sources: tuple[SourceInfo, ...]
    assembled_at: datetime


@dataclass(frozen=True, slots=True, kw_only=True)
class ExportResult:
    row_count: int
    sources: tuple[SourceInfo, ...]
    assembled_at: datetime
