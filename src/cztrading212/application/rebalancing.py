"""Read-only, value-based allocation planning; never creates or submits orders."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import ROUND_HALF_EVEN, Context, Decimal, localcontext
from typing import TYPE_CHECKING, cast

from .._generated.models import TradableInstrument
from ..exceptions import DataUnavailableError
from ..services import ServiceResult
from .common import exact_sum, fraction, source
from .models import PortfolioSnapshot, SourceInfo
from .portfolio import AsyncPortfolio, Portfolio, allocation_from_snapshot

if TYPE_CHECKING:
    from ..services import AsyncServices, Services

WEIGHT_SUM_TOLERANCE = Decimal("0.000000000001")


@dataclass(frozen=True, slots=True, kw_only=True)
class CashValuation:
    amount: Decimal
    currency: str
    valued_at: datetime
    source: str


@dataclass(frozen=True, slots=True, kw_only=True)
class RebalancingEntry:
    ticker: str
    current_value: Decimal
    current_weight: Decimal
    target_weight: Decimal
    drift: Decimal
    target_value: Decimal
    value_difference: Decimal
    proposed_adjustment: Decimal
    action: str
    currency: str
    valuation_fetched_at: datetime
    warnings: tuple[str, ...]


@dataclass(frozen=True, slots=True, kw_only=True)
class RebalancingPlan:
    currency: str
    valuation_basis: str
    total_value: Decimal
    positions_value: Decimal
    cash: CashValuation | None
    available_cash: CashValuation | None
    cash_target_weight: Decimal | None
    entries: tuple[RebalancingEntry, ...]
    total_buy_value: Decimal
    total_sell_value: Decimal
    cash_target_shortfall: Decimal | None
    remaining_cash_requirement: Decimal | None
    snapshot: PortfolioSnapshot
    instruments_source: SourceInfo
    assembled_at: datetime
    warnings: tuple[str, ...]


@dataclass(frozen=True, slots=True, kw_only=True)
class _Options:
    target_weights: Mapping[str, Decimal]
    minimum_trade_value: Decimal
    target_tolerance: Decimal
    buy_only: bool
    cash: CashValuation | None
    cash_target_weight: Decimal | None
    available_cash: CashValuation | None


def _nonnegative(value: Decimal, name: str) -> None:
    if type(value) is not Decimal or not value.is_finite() or value < 0:
        raise ValueError(f"{name} must be a finite non-negative Decimal")
    try:
        exact_sum([value])
    except DataUnavailableError as exc:
        raise ValueError(f"{name} exceeds supported calculation range") from exc


def _check_cash(cash: CashValuation, name: str) -> None:
    _nonnegative(cash.amount, f"{name}.amount")
    if not cash.currency or cash.currency != cash.currency.strip():
        raise ValueError(f"{name}.currency must be non-empty")
    if cash.valued_at.utcoffset() is None:
        raise ValueError(f"{name}.valued_at must have a timezone")
    if not cash.source or not cash.source.strip():
        raise ValueError(f"{name}.source must identify its origin")


def _validate(options: _Options) -> None:
    _nonnegative(options.minimum_trade_value, "minimum_trade_value")
    _nonnegative(options.target_tolerance, "target_tolerance")
    if options.target_tolerance > 1:
        raise ValueError("target_tolerance cannot exceed one")
    if type(options.buy_only) is not bool:
        raise ValueError("buy_only must be a boolean")
    if not options.target_weights and options.cash is None:
        raise ValueError("target_weights must not be empty")
    for ticker, weight in options.target_weights.items():
        if type(ticker) is not str or not ticker or ticker != ticker.strip():
            raise ValueError("target ticker must be a non-empty exact broker identifier")
        _nonnegative(weight, f"target weight for {ticker}")
    if (options.cash is None) != (options.cash_target_weight is None):
        raise ValueError("cash and cash_target_weight must be supplied together")
    if options.cash is not None:
        _check_cash(options.cash, "cash")
    if options.available_cash is not None:
        _check_cash(options.available_cash, "available_cash")
    weights = list(options.target_weights.values())
    if options.cash_target_weight is not None:
        _nonnegative(options.cash_target_weight, "cash_target_weight")
        weights.append(options.cash_target_weight)
    total = exact_sum(weights)
    if exact_sum([total, Decimal(-1)]).copy_abs() > WEIGHT_SUM_TOLERANCE:
        raise ValueError("target weights must sum to one within 0.000000000001")


def _plan(
    snapshot: PortfolioSnapshot,
    instruments: ServiceResult[tuple[TradableInstrument, ...]],
    options: _Options,
) -> RebalancingPlan:
    _validate(options)
    for cash in (options.cash, options.available_cash):
        if cash is not None and cash.currency != snapshot.currency:
            raise DataUnavailableError("Cash valuation currency differs from account currency")
    allocation = allocation_from_snapshot(snapshot)
    if any(entry.current_value < 0 for entry in allocation.entries):
        raise DataUnavailableError("Rebalancing requires non-negative position values")
    values: dict[str, list[Decimal]] = {}
    for entry in allocation.entries:
        values.setdefault(entry.ticker, []).append(entry.current_value)
    tickers = set(values) | set(options.target_weights)
    known = {item.ticker for item in instruments.data if item.ticker}
    missing = sorted(tickers - known)
    if missing:
        raise DataUnavailableError(
            f"Tickers absent from broker instrument metadata: {', '.join(missing)}"
        )
    total = exact_sum(
        [allocation.positions_value, options.cash.amount if options.cash else Decimal(0)]
    )
    if total <= 0:
        raise DataUnavailableError("Rebalancing requires a positive portfolio value")
    warnings = [
        "Values are indicative; prices, trade eligibility, fees, FX and settlement are not validated.",
        "Account, positions and instrument data are separate non-atomic reads.",
    ]
    if options.buy_only:
        warnings.append(
            "Buy-only adjustments may leave targets unmet and additional funding changes portfolio weights."
        )
    if len(values) < len(allocation.entries):
        warnings.append(
            "Repeated ticker positions were explicitly aggregated for ticker-level planning."
        )
    entries: list[RebalancingEntry] = []
    with localcontext(Context(prec=34, rounding=ROUND_HALF_EVEN)):
        for ticker in sorted(tickers):
            current = exact_sum(values.get(ticker, []))
            weight = cast(Decimal, fraction(current, total))
            target = options.target_weights.get(ticker, Decimal(0))
            drift = exact_sum([weight, target.copy_negate()])
            target_value = total * target
            difference = exact_sum([target_value, current.copy_negate()])
            adjustment = difference
            entry_warnings: list[str] = []
            if drift.copy_abs() <= options.target_tolerance:
                adjustment = Decimal(0)
                entry_warnings.append("Within target tolerance.")
            elif difference.copy_abs() < options.minimum_trade_value:
                adjustment = Decimal(0)
                entry_warnings.append("Below minimum trade value.")
            elif options.buy_only and difference < 0:
                adjustment = Decimal(0)
                entry_warnings.append("Sale omitted in buy-only mode.")
            entries.append(
                RebalancingEntry(
                    ticker=ticker,
                    current_value=current,
                    current_weight=weight,
                    target_weight=target,
                    drift=drift,
                    target_value=target_value,
                    value_difference=difference,
                    proposed_adjustment=adjustment,
                    action="buy" if adjustment > 0 else "sell" if adjustment < 0 else "hold",
                    currency=snapshot.currency,
                    valuation_fetched_at=snapshot.positions_source.fetched_at,
                    warnings=tuple(entry_warnings),
                )
            )
    buys = exact_sum([e.proposed_adjustment for e in entries if e.proposed_adjustment > 0])
    sells = exact_sum(
        [e.proposed_adjustment.copy_negate() for e in entries if e.proposed_adjustment < 0]
    )
    cash_shortfall: Decimal | None = None
    if options.cash is not None and options.cash_target_weight is not None:
        with localcontext(Context(prec=34, rounding=ROUND_HALF_EVEN)):
            target_cash = total * options.cash_target_weight
        cash_shortfall = max(
            Decimal(0),
            exact_sum([target_cash, options.cash.amount.copy_negate(), sells.copy_negate(), buys]),
        )
    remaining: Decimal | None = None
    if options.available_cash is not None:
        liquidity_shortfall = max(
            Decimal(0),
            exact_sum([buys, sells.copy_negate(), options.available_cash.amount.copy_negate()]),
        )
        remaining = max(liquidity_shortfall, cash_shortfall or Decimal(0))
        warnings.append(
            "Remaining cash requirement assumes suggested sales settle and caller-supplied available cash is usable; neither is verified."
        )
    else:
        warnings.append(
            "Remaining cash requirement is unavailable without a caller-supplied available cash valuation."
        )
    if (
        options.cash
        and options.available_cash
        and options.available_cash.amount > options.cash.amount
    ):
        warnings.append(
            "Available cash exceeds the supplied total cash; input consistency is unresolved."
        )
    return RebalancingPlan(
        currency=snapshot.currency,
        valuation_basis="positions_and_cash" if options.cash else "positions_only",
        total_value=total,
        positions_value=allocation.positions_value,
        cash=options.cash,
        available_cash=options.available_cash,
        cash_target_weight=options.cash_target_weight,
        entries=tuple(entries),
        total_buy_value=buys,
        total_sell_value=sells,
        cash_target_shortfall=cash_shortfall,
        remaining_cash_requirement=remaining,
        snapshot=snapshot,
        instruments_source=source(
            instruments, operation="instruments", scope="instrument metadata"
        ),
        assembled_at=datetime.now(UTC),
        warnings=tuple(warnings),
    )


class Rebalancing:
    def __init__(self, services: Services) -> None:
        self._services = services
        self._portfolio = Portfolio(services)

    def plan(
        self,
        *,
        target_weights: Mapping[str, Decimal],
        minimum_trade_value: Decimal = Decimal(0),
        target_tolerance: Decimal = Decimal(0),
        buy_only: bool = False,
        cash: CashValuation | None = None,
        cash_target_weight: Decimal | None = None,
        available_cash: CashValuation | None = None,
        refresh: bool = False,
    ) -> RebalancingPlan:
        options = _Options(
            target_weights=dict(target_weights),
            minimum_trade_value=minimum_trade_value,
            target_tolerance=target_tolerance,
            buy_only=buy_only,
            cash=cash,
            cash_target_weight=cash_target_weight,
            available_cash=available_cash,
        )
        _validate(options)
        snapshot = self._portfolio.snapshot(refresh=refresh)
        instruments = cast(
            ServiceResult[tuple[TradableInstrument, ...]],
            self._services.call_with_metadata("instruments", refresh=refresh),
        )
        return _plan(snapshot, instruments, options)


class AsyncRebalancing:
    def __init__(self, services: AsyncServices) -> None:
        self._services = services
        self._portfolio = AsyncPortfolio(services)

    async def plan(
        self,
        *,
        target_weights: Mapping[str, Decimal],
        minimum_trade_value: Decimal = Decimal(0),
        target_tolerance: Decimal = Decimal(0),
        buy_only: bool = False,
        cash: CashValuation | None = None,
        cash_target_weight: Decimal | None = None,
        available_cash: CashValuation | None = None,
        refresh: bool = False,
    ) -> RebalancingPlan:
        options = _Options(
            target_weights=dict(target_weights),
            minimum_trade_value=minimum_trade_value,
            target_tolerance=target_tolerance,
            buy_only=buy_only,
            cash=cash,
            cash_target_weight=cash_target_weight,
            available_cash=available_cash,
        )
        _validate(options)
        snapshot = await self._portfolio.snapshot(refresh=refresh)
        instruments = cast(
            ServiceResult[tuple[TradableInstrument, ...]],
            await self._services.call_with_metadata("instruments", refresh=refresh),
        )
        return _plan(snapshot, instruments, options)
