import asyncio
from datetime import UTC, datetime
from decimal import Decimal, Inexact, localcontext

import httpx
import pytest

from cztrading212 import AsyncTrading212, DataUnavailableError, Trading212
from cztrading212.application.rebalancing import CashValuation

ACCOUNT = {"currency": "GBP", "totalValue": 120, "cash": {"availableToTrade": 20}}
POSITIONS = [
    {"instrument": {"ticker": "A"}, "walletImpact": {"currency": "GBP", "currentValue": 80}},
    {"instrument": {"ticker": "B"}, "walletImpact": {"currency": "GBP", "currentValue": 20}},
]
INSTRUMENTS = [{"ticker": "A"}, {"ticker": "B"}, {"ticker": "C"}]


def transport(positions=POSITIONS, requests=None):
    def handle(request):
        if requests is not None:
            requests.append(request)
        if request.url.path.endswith("summary"):
            return httpx.Response(200, json=ACCOUNT)
        if request.url.path.endswith("positions"):
            return httpx.Response(200, json=positions)
        return httpx.Response(200, json=INSTRUMENTS)

    return httpx.MockTransport(handle)


def cash(amount, currency="GBP"):
    return CashValuation(
        amount=Decimal(amount),
        currency=currency,
        valued_at=datetime(2026, 1, 1, tzinfo=UTC),
        source="caller statement",
    )


def test_invested_plan_and_no_trading_side_effects():
    requests = []
    with Trading212("key", "secret", transport=transport(requests=requests)) as client:
        plan = client.app.rebalancing.plan(target_weights={"A": Decimal(".5"), "C": Decimal(".5")})
    assert plan.valuation_basis == "positions_only" and plan.total_value == 100
    assert [(e.ticker, e.action, e.proposed_adjustment) for e in plan.entries] == [
        ("A", "sell", -30),
        ("B", "sell", -20),
        ("C", "buy", 50),
    ]
    assert plan.total_buy_value == plan.total_sell_value == 50
    assert plan.remaining_cash_requirement is None
    assert all(request.method == "GET" for request in requests)
    assert len(requests) == 3


def test_cash_inclusive_buy_only_and_insufficient_funding():
    with Trading212("key", "secret", transport=transport()) as client:
        plan = client.app.rebalancing.plan(
            target_weights={"A": Decimal(".5"), "B": Decimal(".25")},
            cash=cash("20"),
            cash_target_weight=Decimal(".25"),
            available_cash=cash("5"),
            buy_only=True,
        )
    assert plan.total_value == 120 and plan.valuation_basis == "positions_and_cash"
    assert [(e.action, e.proposed_adjustment) for e in plan.entries] == [("hold", 0), ("buy", 10)]
    assert plan.cash_target_shortfall == 20
    assert plan.remaining_cash_requirement == 20
    assert any("buy-only" in warning for warning in plan.entries[0].warnings)


def test_invalid_inputs_missing_instrument_and_currency():
    with Trading212("key", "secret", transport=transport()) as client:
        for targets in ({"A": Decimal(".9")}, {"A": Decimal(-1), "B": Decimal(2)}, {"A": 1.0}):
            with pytest.raises(ValueError):
                client.app.rebalancing.plan(target_weights=targets)
        with pytest.raises(ValueError):
            client.app.rebalancing.plan(target_weights={"A": Decimal(1)}, cash=cash("10"))
        with pytest.raises(DataUnavailableError):
            client.app.rebalancing.plan(target_weights={"UNKNOWN": Decimal(1)})
        with pytest.raises(DataUnavailableError):
            client.app.rebalancing.plan(
                target_weights={"A": Decimal(1)}, available_cash=cash("10", "USD")
            )
    with (
        Trading212("key", "secret", transport=transport([])) as client,
        pytest.raises(DataUnavailableError),
    ):
        client.app.rebalancing.plan(target_weights={"A": Decimal(1)})


def test_minimum_tolerance_and_decimal_context():
    with localcontext() as ctx:
        ctx.prec = 3
        ctx.traps[Inexact] = True
        with Trading212("key", "secret", transport=transport()) as client:
            result = client.app.rebalancing.plan(
                target_weights={"A": Decimal(".79"), "B": Decimal(".21")},
                minimum_trade_value=Decimal(2),
            )
    assert all(e.action == "hold" for e in result.entries)
    with Trading212("key", "secret", transport=transport()) as client:
        result = client.app.rebalancing.plan(
            target_weights={"A": Decimal(".79"), "B": Decimal(".21")},
            target_tolerance=Decimal(".01"),
        )
    assert all(e.action == "hold" for e in result.entries)


def test_async_plan():
    async def run():
        async with AsyncTrading212("key", "secret", transport=transport()) as client:
            plan = await client.app.rebalancing.plan(
                target_weights={"A": Decimal(".5"), "B": Decimal(".5")}, available_cash=cash("0")
            )
            assert plan.total_buy_value == 30 and plan.total_sell_value == 30
            assert plan.remaining_cash_requirement == 0

    asyncio.run(run())


def test_duplicate_positions_aggregate_and_cash_target_is_visible_without_liquidity():
    positions = [
        {"instrument": {"ticker": "A"}, "walletImpact": {"currency": "GBP", "currentValue": 30}},
        {"instrument": {"ticker": "A"}, "walletImpact": {"currency": "GBP", "currentValue": 50}},
    ]
    with Trading212("key", "secret", transport=transport(positions)) as client:
        result = client.app.rebalancing.plan(
            target_weights={"A": Decimal(".5")},
            cash=cash("20"),
            cash_target_weight=Decimal(".5"),
            buy_only=True,
        )
    assert result.entries[0].current_value == 80
    assert result.cash_target_shortfall == 30
    assert result.remaining_cash_requirement is None
    assert any("aggregated" in message for message in result.warnings)
