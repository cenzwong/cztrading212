import asyncio
from decimal import Decimal

import httpx
import pytest

from cztrading212 import AsyncTrading212, DataUnavailableError, ResponseValidationError, Trading212

ACCOUNT = '{"currency":"GBP","totalValue":120,"cash":{"availableToTrade":20},"investments":{"currentValue":100,"totalCost":90,"unrealizedProfitLoss":10,"realizedProfitLoss":4}}'
POSITIONS = '[{"instrument":{"ticker":"A","currency":"USD"},"walletImpact":{"currency":"GBP","currentValue":75,"totalCost":60,"unrealizedProfitLoss":15,"fxImpact":2}},{"instrument":{"ticker":"A","currency":"GBP"},"walletImpact":{"currency":"GBP","currentValue":25,"totalCost":30,"unrealizedProfitLoss":-5}}]'


def transport(positions=POSITIONS):
    return httpx.MockTransport(
        lambda request: httpx.Response(
            200, text=ACCOUNT if request.url.path.endswith("summary") else positions
        )
    )


def test_concentration_exposure_and_duplicate_tickers():
    with Trading212("key", "secret", transport=transport()) as client:
        report = client.app.risk.concentration()
        assert report.hhi == Decimal(".625")
        assert report.effective_holdings == Decimal("1.6")
        assert len(report.entries) == 2
        assert client.app.risk.concentration(aggregate_by_ticker=True).hhi == 1
        exposure = client.app.risk.exposure()
        assert [(e.category, e.value) for e in exposure.by_currency] == [("GBP", 25), ("USD", 75)]
        assert exposure.by_ticker[0].value == 100
        assert exposure.cash_available_to_trade == 20


def test_empty_and_invalid_concentration():
    with Trading212("key", "secret", transport=transport("[]")) as client:
        assert client.app.risk.concentration().hhi is None
    for value in ("-1", "null"):
        positions = (
            '[{"instrument":{"ticker":"A","currency":"GBP"},"walletImpact":{"currency":"GBP","currentValue":'
            + value
            + "}}]"
        )
        with (
            Trading212("key", "secret", transport=transport(positions)) as client,
            pytest.raises((DataUnavailableError, ResponseValidationError)),
        ):
            client.app.risk.concentration()


def test_performance_uses_reported_values_and_preserves_fx():
    with Trading212("key", "secret", transport=transport()) as client:
        report = client.app.performance.pnl_summary()
        assert report.realized_profit_loss == 4
        assert report.unrealized_profit_loss == 10
        assert report.positions[0].fx_impact == 2
        assert report.positions[0].return_fraction == Decimal(".25")
        ranking = client.app.performance.winners_losers()
        assert ranking.winners[0].unrealized_profit_loss == 15
        assert ranking.losers[0].unrealized_profit_loss == -5


def test_async_parity():
    async def run():
        async with AsyncTrading212("key", "secret", transport=transport()) as client:
            assert (await client.app.risk.concentration()).hhi == Decimal(".625")
            assert (await client.app.performance.pnl_summary()).realized_profit_loss == 4

    asyncio.run(run())


def test_type_exposure_handles_unknown_and_conflicting_metadata():
    positions = '[{"instrument":{"ticker":"A","currency":"USD"},"walletImpact":{"currency":"GBP","currentValue":70}},{"instrument":{"ticker":"B"},"walletImpact":{"currency":"GBP","currentValue":30}}]'
    instruments = (
        '[{"ticker":"A","type":"STOCK"},{"ticker":"B","type":"ETF"},{"ticker":"B","type":"STOCK"}]'
    )

    def handle(request):
        if request.url.path.endswith("summary"):
            return httpx.Response(200, text=ACCOUNT)
        return httpx.Response(
            200, text=instruments if request.url.path.endswith("instruments") else positions
        )

    with Trading212("key", "secret", transport=httpx.MockTransport(handle)) as client:
        result = client.app.risk.exposure()
    assert [(e.category, e.value) for e in result.by_type] == [("STOCK", 70), (None, 30)]
    assert [(e.category, e.value) for e in result.by_currency] == [("USD", 70), (None, 30)]
    assert not result.instruments_source.from_cache


def test_negative_portfolio_pnl_does_not_swap_winners_and_losers():
    account = '{"currency":"GBP","totalValue":100,"investments":{"unrealizedProfitLoss":-10}}'
    positions = '[{"instrument":{"ticker":"A"},"walletImpact":{"currency":"GBP","unrealizedProfitLoss":5,"totalCost":0}},{"instrument":{"ticker":"B"},"walletImpact":{"currency":"GBP","unrealizedProfitLoss":-15,"totalCost":10}},{"instrument":{"ticker":"C"}}]'
    with Trading212(
        "key",
        "secret",
        transport=httpx.MockTransport(
            lambda request: httpx.Response(
                200, text=account if request.url.path.endswith("summary") else positions
            )
        ),
    ) as client:
        report = client.app.performance.winners_losers(rank_by="contribution")
    assert report.winners[0].ticker == "A" and report.winners[0].pnl_contribution == Decimal("-.5")
    assert report.losers[0].ticker == "B" and report.losers[0].pnl_contribution == Decimal("1.5")
    assert report.summary.positions[0].return_fraction is None
    assert report.summary.positions[2].currency is None


def test_sources_identify_operation_and_preserve_freshness():
    with Trading212("key", "secret", transport=transport()) as client:
        first = client.app.portfolio.snapshot()
        cached = client.app.portfolio.snapshot()
    assert first.account_source.operation == "getAccountSummary"
    assert first.positions_source.operation == "getPositions"
    assert first.account_source.completeness == "unknown"
    assert cached.account_source.from_cache
    assert cached.account_source.fetched_at == first.account_source.fetched_at


def test_fraction_rejects_nonfinite_and_unsupported_values():
    from cztrading212.application.common import fraction

    with pytest.raises(DataUnavailableError):
        fraction(Decimal("NaN"), Decimal(1))
    with pytest.raises(DataUnavailableError):
        fraction(Decimal("1e10001"), Decimal(1))


def test_performance_currency_mismatch_and_missing_cost():
    bad = (
        '[{"instrument":{"ticker":"A"},"walletImpact":{"currency":"USD","unrealizedProfitLoss":1}}]'
    )
    with (
        Trading212("key", "secret", transport=transport(bad)) as client,
        pytest.raises(DataUnavailableError),
    ):
        client.app.performance.pnl_summary()
    missing = (
        '[{"instrument":{"ticker":"A"},"walletImpact":{"currency":"GBP","unrealizedProfitLoss":1}}]'
    )
    with Trading212("key", "secret", transport=transport(missing)) as client:
        result = client.app.performance.pnl_summary()
    assert result.positions[0].total_cost is None
    assert result.positions[0].return_fraction is None
    assert result.positions[0].unrealized_profit_loss == 1


def test_async_exposure_and_contribution_rank_match_sync():
    def handle(request):
        if request.url.path.endswith("summary"):
            return httpx.Response(200, text=ACCOUNT)
        if request.url.path.endswith("instruments"):
            return httpx.Response(200, json=[{"ticker": "A", "type": "STOCK"}])
        return httpx.Response(200, text=POSITIONS)

    with Trading212("key", "secret", transport=httpx.MockTransport(handle)) as client:
        expected_exposure = client.app.risk.exposure()
        expected_rank = client.app.performance.winners_losers(rank_by="contribution")

    async def run():
        async with AsyncTrading212(
            "key", "secret", transport=httpx.MockTransport(handle)
        ) as client:
            exposure = await client.app.risk.exposure()
            ranking = await client.app.performance.winners_losers(rank_by="contribution")
            assert exposure.by_type == expected_exposure.by_type
            assert exposure.by_currency == expected_exposure.by_currency
            assert ranking.winners == expected_rank.winners
            assert ranking.losers == expected_rank.losers

    asyncio.run(run())
