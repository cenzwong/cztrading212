"""Current broker-reported P&L, without reconstructing historical returns."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from typing import TYPE_CHECKING

from ..exceptions import DataUnavailableError
from .common import fraction
from .models import PortfolioSnapshot
from .portfolio import AsyncPortfolio, Portfolio

if TYPE_CHECKING:
    from ..services import AsyncServices, Services


@dataclass(frozen=True, slots=True, kw_only=True)
class PositionPerformance:
    ticker: str | None
    currency: str | None
    current_value: Decimal | None
    total_cost: Decimal | None
    unrealized_profit_loss: Decimal | None
    fx_impact: Decimal | None
    return_fraction: Decimal | None
    pnl_contribution: Decimal | None


@dataclass(frozen=True, slots=True, kw_only=True)
class PnLSummary:
    snapshot: PortfolioSnapshot
    current_value: Decimal | None
    total_cost: Decimal | None
    unrealized_profit_loss: Decimal | None
    realized_profit_loss: Decimal | None
    positions: tuple[PositionPerformance, ...]
    assembled_at: datetime


@dataclass(frozen=True, slots=True, kw_only=True)
class WinnersLosersReport:
    summary: PnLSummary
    rank_by: str
    winners: tuple[PositionPerformance, ...]
    losers: tuple[PositionPerformance, ...]


def _summary(snapshot: PortfolioSnapshot) -> PnLSummary:
    investments = snapshot.investments
    total = investments.unrealized_profit_loss if investments else None
    if investments:
        for value in (
            investments.current_value,
            investments.total_cost,
            investments.unrealized_profit_loss,
            investments.realized_profit_loss,
        ):
            if value is not None and not value.is_finite():
                raise DataUnavailableError("Non-finite account performance value")
    entries: list[PositionPerformance] = []
    for position in snapshot.positions:
        impact = position.wallet_impact
        if impact is None or not impact.currency:
            entries.append(
                PositionPerformance(
                    ticker=position.instrument.ticker if position.instrument else None,
                    currency=None,
                    current_value=None,
                    total_cost=None,
                    unrealized_profit_loss=None,
                    fx_impact=None,
                    return_fraction=None,
                    pnl_contribution=None,
                )
            )
            continue
        if impact.currency != snapshot.currency:
            raise DataUnavailableError("Position wallet currency differs from account currency")
        for value in (
            impact.current_value,
            impact.total_cost,
            impact.unrealized_profit_loss,
            impact.fx_impact,
            total,
        ):
            if value is not None and not value.is_finite():
                raise DataUnavailableError("Non-finite performance value")
        cost = impact.total_cost
        pnl = impact.unrealized_profit_loss
        entries.append(
            PositionPerformance(
                ticker=position.instrument.ticker if position.instrument else None,
                currency=snapshot.currency,
                current_value=impact.current_value,
                total_cost=cost,
                unrealized_profit_loss=pnl,
                fx_impact=impact.fx_impact,
                return_fraction=fraction(pnl, cost)
                if pnl is not None and cost is not None and cost > 0
                else None,
                pnl_contribution=fraction(pnl, total)
                if pnl is not None and total is not None
                else None,
            )
        )
    return PnLSummary(
        snapshot=snapshot,
        current_value=investments.current_value if investments else None,
        total_cost=investments.total_cost if investments else None,
        unrealized_profit_loss=total,
        realized_profit_loss=investments.realized_profit_loss if investments else None,
        positions=tuple(entries),
        assembled_at=datetime.now(UTC),
    )


def _rank(summary: PnLSummary, limit: int, rank_by: str) -> WinnersLosersReport:
    if type(limit) is not int or limit < 1:
        raise ValueError("limit must be a positive integer")
    if rank_by not in ("absolute", "percentage", "contribution"):
        raise ValueError("rank_by must be absolute, percentage, or contribution")

    def metric(entry: PositionPerformance) -> Decimal | None:
        if rank_by == "percentage":
            return entry.return_fraction
        if rank_by == "contribution":
            return entry.pnl_contribution
        return entry.unrealized_profit_loss

    eligible = [(metric(entry), index, entry) for index, entry in enumerate(summary.positions)]
    # A negative aggregate reverses the sign of P&L contributions; identify
    # winners and losers by P&L, not by the contribution ratio's sign.
    reverse_contribution = (
        rank_by == "contribution"
        and summary.unrealized_profit_loss is not None
        and summary.unrealized_profit_loss < 0
    )

    def ordered_metric(value: Decimal) -> Decimal:
        return value.copy_negate() if reverse_contribution else value

    winners = sorted(
        (
            (v, i, e)
            for v, i, e in eligible
            if v is not None
            and e.unrealized_profit_loss is not None
            and e.unrealized_profit_loss > 0
        ),
        key=lambda x: (ordered_metric(x[0]).copy_negate(), x[1]),
    )
    losers = sorted(
        (
            (v, i, e)
            for v, i, e in eligible
            if v is not None
            and e.unrealized_profit_loss is not None
            and e.unrealized_profit_loss < 0
        ),
        key=lambda x: (ordered_metric(x[0]), x[1]),
    )
    return WinnersLosersReport(
        summary=summary,
        rank_by=rank_by,
        winners=tuple(e for _, _, e in winners[:limit]),
        losers=tuple(e for _, _, e in losers[:limit]),
    )


class Performance:
    def __init__(self, services: Services) -> None:
        self._portfolio = Portfolio(services)

    def pnl_summary(self, *, refresh: bool = False) -> PnLSummary:
        return _summary(self._portfolio.snapshot(refresh=refresh))

    def winners_losers(
        self, *, limit: int = 5, rank_by: str = "absolute", refresh: bool = False
    ) -> WinnersLosersReport:
        return _rank(self.pnl_summary(refresh=refresh), limit, rank_by)


class AsyncPerformance:
    def __init__(self, services: AsyncServices) -> None:
        self._portfolio = AsyncPortfolio(services)

    async def pnl_summary(self, *, refresh: bool = False) -> PnLSummary:
        return _summary(await self._portfolio.snapshot(refresh=refresh))

    async def winners_losers(
        self, *, limit: int = 5, rank_by: str = "absolute", refresh: bool = False
    ) -> WinnersLosersReport:
        return _rank(await self.pnl_summary(refresh=refresh), limit, rank_by)
