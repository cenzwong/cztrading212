"""Read-only concentration and exposure in the account valuation currency."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Context, Decimal, localcontext
from typing import TYPE_CHECKING, cast

from .._generated.models import TradableInstrument
from ..exceptions import DataUnavailableError
from ..services import ServiceResult
from .common import exact_sum, fraction, source
from .models import PortfolioSnapshot, SourceInfo
from .portfolio import AsyncPortfolio, Portfolio, allocation_from_snapshot

if TYPE_CHECKING:
    from ..services import AsyncServices, Services


@dataclass(frozen=True, slots=True, kw_only=True)
class ConcentrationEntry:
    ticker: str
    current_value: Decimal
    weight: Decimal | None


@dataclass(frozen=True, slots=True, kw_only=True)
class ConcentrationReport:
    snapshot: PortfolioSnapshot
    positions_value: Decimal
    entries: tuple[ConcentrationEntry, ...]
    top_3: Decimal | None
    top_5: Decimal | None
    top_10: Decimal | None
    hhi: Decimal | None
    effective_holdings: Decimal | None
    assembled_at: datetime


@dataclass(frozen=True, slots=True, kw_only=True)
class ExposureEntry:
    category: str | None
    value: Decimal
    weight: Decimal | None


@dataclass(frozen=True, slots=True, kw_only=True)
class ExposureReport:
    snapshot: PortfolioSnapshot
    positions_value: Decimal
    by_ticker: tuple[ExposureEntry, ...]
    by_currency: tuple[ExposureEntry, ...]
    by_type: tuple[ExposureEntry, ...]
    instruments_source: SourceInfo
    cash_available_to_trade: Decimal | None
    assembled_at: datetime


def _concentration(snapshot: PortfolioSnapshot, aggregate_by_ticker: bool) -> ConcentrationReport:
    allocation = allocation_from_snapshot(snapshot)
    values = [(e.ticker, e.current_value) for e in allocation.entries]
    if any(v < 0 for _, v in values):
        raise DataUnavailableError("Concentration requires non-negative position values")
    if aggregate_by_ticker:
        groups: dict[str, list[Decimal]] = {}
        for ticker, value in values:
            groups.setdefault(ticker, []).append(value)
        values = [(k, exact_sum(v)) for k, v in groups.items()]
    values.sort(key=lambda item: (item[1].copy_negate(), item[0]))
    total = allocation.positions_value
    entries = tuple(
        ConcentrationEntry(ticker=k, current_value=v, weight=fraction(v, total)) for k, v in values
    )
    with localcontext(Context(prec=34)):
        hhi = (
            sum((e.weight**2 for e in entries if e.weight is not None), Decimal(0))
            if total
            else None
        )
        effective = Decimal(1) / hhi if hhi else None

        def top(n: int) -> Decimal | None:
            return fraction(exact_sum([e.current_value for e in entries[:n]]), total)

        return ConcentrationReport(
            snapshot=snapshot,
            positions_value=total,
            entries=entries,
            top_3=top(3),
            top_5=top(5),
            top_10=top(10),
            hhi=hhi,
            effective_holdings=effective,
            assembled_at=datetime.now(UTC),
        )


def _exposure(
    snapshot: PortfolioSnapshot, metadata: ServiceResult[tuple[TradableInstrument, ...]]
) -> ExposureReport:
    allocation = allocation_from_snapshot(snapshot)
    tickers: dict[str | None, list[Decimal]] = {}
    currencies: dict[str | None, list[Decimal]] = {}
    types: dict[str | None, list[Decimal]] = {}
    metadata_types: dict[str, set[str | None]] = {}
    for instrument in metadata.data:
        if instrument.ticker:
            code = (
                instrument.type
                if instrument.type and instrument.type == instrument.type.strip()
                else None
            )
            metadata_types.setdefault(instrument.ticker, set()).add(code)
    for position, entry in zip(snapshot.positions, allocation.entries, strict=True):
        if entry.current_value < 0:
            raise DataUnavailableError("Exposure requires non-negative position values")
        tickers.setdefault(entry.ticker, []).append(entry.current_value)
        code = position.instrument.currency if position.instrument else None
        if not code or code != code.strip():
            code = None
        currencies.setdefault(code, []).append(entry.current_value)
        candidates = metadata_types.get(entry.ticker, set())
        kind = next(iter(candidates)) if len(candidates) == 1 else None
        types.setdefault(kind, []).append(entry.current_value)

    def entries(groups: dict[str | None, list[Decimal]]) -> tuple[ExposureEntry, ...]:
        return tuple(
            ExposureEntry(
                category=k,
                value=(v := exact_sum(items)),
                weight=fraction(v, allocation.positions_value),
            )
            for k, items in sorted(
                groups.items(), key=lambda item: (item[0] is None, item[0] or "")
            )
        )

    return ExposureReport(
        snapshot=snapshot,
        positions_value=allocation.positions_value,
        by_ticker=entries(tickers),
        by_currency=entries(currencies),
        by_type=entries(types),
        instruments_source=source(metadata, operation="instruments", scope="instrument metadata"),
        cash_available_to_trade=snapshot.cash.available_to_trade if snapshot.cash else None,
        assembled_at=datetime.now(UTC),
    )


class Risk:
    def __init__(self, services: Services) -> None:
        self._portfolio = Portfolio(services)
        self._services = services

    def concentration(
        self, *, aggregate_by_ticker: bool = False, refresh: bool = False
    ) -> ConcentrationReport:
        return _concentration(self._portfolio.snapshot(refresh=refresh), aggregate_by_ticker)

    def exposure(self, *, refresh: bool = False) -> ExposureReport:
        snapshot = self._portfolio.snapshot(refresh=refresh)
        metadata = cast(
            ServiceResult[tuple[TradableInstrument, ...]],
            self._services.call_with_metadata("instruments", refresh=refresh),
        )
        return _exposure(snapshot, metadata)


class AsyncRisk:
    def __init__(self, services: AsyncServices) -> None:
        self._portfolio = AsyncPortfolio(services)
        self._services = services

    async def concentration(
        self, *, aggregate_by_ticker: bool = False, refresh: bool = False
    ) -> ConcentrationReport:
        return _concentration(await self._portfolio.snapshot(refresh=refresh), aggregate_by_ticker)

    async def exposure(self, *, refresh: bool = False) -> ExposureReport:
        snapshot = await self._portfolio.snapshot(refresh=refresh)
        metadata = cast(
            ServiceResult[tuple[TradableInstrument, ...]],
            await self._services.call_with_metadata("instruments", refresh=refresh),
        )
        return _exposure(snapshot, metadata)
