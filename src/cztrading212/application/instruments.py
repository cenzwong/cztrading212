"""Deterministic local search of the broker's instrument metadata."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING, cast

from .._generated.models import TradableInstrument
from ..services import ServiceResult
from .common import source
from .models import SourceInfo

if TYPE_CHECKING:
    from ..services import AsyncServices, Services


@dataclass(frozen=True, slots=True, kw_only=True)
class InstrumentMatch:
    instrument: TradableInstrument
    matched_by: tuple[str, ...]


@dataclass(frozen=True, slots=True, kw_only=True)
class InstrumentSearchResult:
    query: str
    matches: tuple[InstrumentMatch, ...]
    total_matches: int
    limit: int
    source: SourceInfo
    assembled_at: datetime
    operation: str = "instruments"
    completeness: str = "complete_response"


def _validate(query: str, limit: int) -> None:
    if not query.strip() or query != query.strip():
        raise ValueError("query must be a non-empty string without surrounding whitespace")
    if type(limit) is not int or limit < 1:
        raise ValueError("limit must be a positive integer")


def _search(
    instruments: ServiceResult[tuple[TradableInstrument, ...]],
    query: str,
    limit: int,
    isin_only: bool,
) -> InstrumentSearchResult:
    _validate(query, limit)
    needle = query.casefold()
    matches: list[tuple[int, InstrumentMatch]] = []
    for index, instrument in enumerate(instruments.data):
        reasons: list[str] = []
        if not isin_only and instrument.ticker and instrument.ticker.casefold() == needle:
            reasons.append("ticker")
        if instrument.isin and instrument.isin.casefold() == needle:
            reasons.append("isin")
        if not isin_only and instrument.name and needle in instrument.name.casefold():
            reasons.append("name")
        if reasons:
            matches.append(
                (index, InstrumentMatch(instrument=instrument, matched_by=tuple(reasons)))
            )
    # Original index is a deterministic tie-breaker for identical metadata.
    matches.sort(
        key=lambda item: (
            (item[1].instrument.ticker or "").casefold(),
            (item[1].instrument.isin or "").casefold(),
            (item[1].instrument.name or "").casefold(),
            item[0],
        )
    )
    return InstrumentSearchResult(
        query=query,
        matches=tuple(match for _, match in matches[:limit]),
        total_matches=len(matches),
        limit=limit,
        source=source(instruments, operation="instruments", scope="instrument metadata"),
        assembled_at=datetime.now(UTC),
    )


class Instruments:
    def __init__(self, services: Services) -> None:
        self._services = services

    def search(
        self, query: str, *, limit: int = 50, refresh: bool = False
    ) -> InstrumentSearchResult:
        _validate(query, limit)
        result = cast(
            ServiceResult[tuple[TradableInstrument, ...]],
            self._services.call_with_metadata("instruments", refresh=refresh),
        )
        return _search(result, query, limit, False)

    def lookup_isin(
        self, isin: str, *, limit: int = 50, refresh: bool = False
    ) -> InstrumentSearchResult:
        _validate(isin, limit)
        result = cast(
            ServiceResult[tuple[TradableInstrument, ...]],
            self._services.call_with_metadata("instruments", refresh=refresh),
        )
        return _search(result, isin, limit, True)


class AsyncInstruments:
    def __init__(self, services: AsyncServices) -> None:
        self._services = services

    async def search(
        self, query: str, *, limit: int = 50, refresh: bool = False
    ) -> InstrumentSearchResult:
        _validate(query, limit)
        result = cast(
            ServiceResult[tuple[TradableInstrument, ...]],
            await self._services.call_with_metadata("instruments", refresh=refresh),
        )
        return _search(result, query, limit, False)

    async def lookup_isin(
        self, isin: str, *, limit: int = 50, refresh: bool = False
    ) -> InstrumentSearchResult:
        _validate(isin, limit)
        result = cast(
            ServiceResult[tuple[TradableInstrument, ...]],
            await self._services.call_with_metadata("instruments", refresh=refresh),
        )
        return _search(result, isin, limit, True)
