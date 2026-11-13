"""Portfolio workflows, composed solely from typed service responses."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from typing import TYPE_CHECKING, cast

from .._generated.models import AccountSummary, Position
from ..exceptions import DataUnavailableError
from ..services import ServiceResult
from .common import currency, exact_sum, fraction, required, source
from .models import AllocationEntry, PortfolioAllocation, PortfolioReport, PortfolioSnapshot

if TYPE_CHECKING:
    from ..services import AsyncServices, Services


def _snapshot(
    account: ServiceResult[AccountSummary], positions: ServiceResult[tuple[Position, ...]]
) -> PortfolioSnapshot:
    return PortfolioSnapshot(
        currency=currency(account.data.currency, "account.currency"),
        total_value=required(account.data.total_value, "account.totalValue"),
        cash=account.data.cash,
        investments=account.data.investments,
        positions=positions.data,
        account_source=source(account, operation="getAccountSummary", scope="account summary"),
        positions_source=source(positions, operation="getPositions", scope="all positions"),
        assembled_at=datetime.now(UTC),
    )


def allocation_from_snapshot(snapshot: PortfolioSnapshot) -> PortfolioAllocation:
    values: list[tuple[str, Decimal]] = []
    for index, position in enumerate(snapshot.positions):
        instrument = required(position.instrument, f"positions[{index}].instrument")
        ticker = required(instrument.ticker, f"positions[{index}].instrument.ticker")
        if not ticker:
            raise DataUnavailableError(f"Missing ticker for positions[{index}]")
        impact = required(position.wallet_impact, f"positions[{index}].walletImpact")
        item_currency = currency(impact.currency, f"positions[{index}].walletImpact.currency")
        if item_currency != snapshot.currency:
            raise DataUnavailableError(f"Currency mismatch for positions[{index}].walletImpact")
        values.append(
            (
                ticker,
                required(impact.current_value, f"positions[{index}].walletImpact.currentValue"),
            )
        )
    total = exact_sum([value for _, value in values])
    return PortfolioAllocation(
        currency=snapshot.currency,
        positions_value=total,
        entries=tuple(
            AllocationEntry(ticker=ticker, current_value=value, fraction=fraction(value, total))
            for ticker, value in values
        ),
        account_source=snapshot.account_source,
        positions_source=snapshot.positions_source,
        assembled_at=datetime.now(UTC),
    )


class Portfolio:
    def __init__(self, services: Services) -> None:
        self._services = services

    def snapshot(self, *, refresh: bool = False) -> PortfolioSnapshot:
        account = cast(
            ServiceResult[AccountSummary],
            self._services.call_with_metadata("getAccountSummary", refresh=refresh),
        )
        positions = cast(
            ServiceResult[tuple[Position, ...]],
            self._services.call_with_metadata("getPositions", refresh=refresh),
        )
        return _snapshot(account, positions)

    def allocation(self, *, refresh: bool = False) -> PortfolioAllocation:
        return allocation_from_snapshot(self.snapshot(refresh=refresh))

    def report(self, *, refresh: bool = False) -> PortfolioReport:
        snapshot = self.snapshot(refresh=refresh)
        return PortfolioReport(snapshot=snapshot, allocation=allocation_from_snapshot(snapshot))


class AsyncPortfolio:
    def __init__(self, services: AsyncServices) -> None:
        self._services = services

    async def snapshot(self, *, refresh: bool = False) -> PortfolioSnapshot:
        account = cast(
            ServiceResult[AccountSummary],
            await self._services.call_with_metadata("getAccountSummary", refresh=refresh),
        )
        positions = cast(
            ServiceResult[tuple[Position, ...]],
            await self._services.call_with_metadata("getPositions", refresh=refresh),
        )
        return _snapshot(account, positions)

    async def allocation(self, *, refresh: bool = False) -> PortfolioAllocation:
        return allocation_from_snapshot(await self.snapshot(refresh=refresh))

    async def report(self, *, refresh: bool = False) -> PortfolioReport:
        snapshot = await self.snapshot(refresh=refresh)
        return PortfolioReport(snapshot=snapshot, allocation=allocation_from_snapshot(snapshot))
