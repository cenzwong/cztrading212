import asyncio
import csv
from decimal import Decimal, localcontext
from io import StringIO

import httpx
import pytest

from cztrading212 import (
    AsyncTrading212,
    DataUnavailableError,
    PaginationError,
    ResponseValidationError,
    Trading212,
)
from cztrading212.application.common import exact_sum

ACCOUNT = '{"id": 10, "currency":"GBP", "totalValue":100.000000000000000001,"cash":{"availableToTrade":3.5},"investments":{"currentValue":50}}'
POSITIONS = '[{"instrument":{"ticker":"A_US_EQ","currency":"USD"},"walletImpact":{"currency":"GBP","currentValue":0.100000000000000001,"fxImpact":null}},{"instrument":{"ticker":"B_US_EQ","currency":"GBP"},"walletImpact":{"currency":"GBP","currentValue":0.2}}]'


def test_portfolio_report_uses_account_currency_exact_values_and_source_metadata():
    requests = []

    def handle(request):
        requests.append(request)
        return httpx.Response(
            200, text=ACCOUNT if request.url.path.endswith("summary") else POSITIONS
        )

    with Trading212("key", "secret", transport=httpx.MockTransport(handle)) as client:
        report = client.app.portfolio.report()
        assert report.snapshot.total_value == Decimal("100.000000000000000001")
        assert report.snapshot.position_count == 2
        assert report.snapshot.cash.available_to_trade == Decimal("3.5")
        assert report.allocation.positions_value == Decimal("0.300000000000000001")
        assert report.allocation.entries[0].current_value == Decimal("0.100000000000000001")
        assert report.allocation.entries[0].fraction == Decimal(
            "0.3333333333333333355555555555555555"
        )
        assert report.allocation.currency == "GBP"
        assert not report.snapshot.account_source.from_cache
        assert client.app.portfolio.snapshot().account_source.from_cache
        assert len(requests) == 2


def test_portfolio_rejects_missing_fields_and_currency_mismatch():
    def handler(position):
        def handle(request):
            return httpx.Response(
                200, text=ACCOUNT if request.url.path.endswith("summary") else position
            )

        return handle

    for positions in (
        '[{"instrument":{"ticker":"A"},"walletImpact":{"currency":"USD","currentValue":1}}]',
        '[{"instrument":{"ticker":"A"},"walletImpact":{"currency":"GBP"}}]',
    ):
        with (
            Trading212(
                "key", "secret", transport=httpx.MockTransport(handler(positions))
            ) as client,
            pytest.raises(DataUnavailableError),
        ):
            client.app.portfolio.allocation()


def test_zero_value_allocation_has_no_fraction_and_empty_portfolio_is_valid():
    def handler(positions):
        return lambda request: httpx.Response(
            200, text=ACCOUNT if request.url.path.endswith("summary") else positions
        )

    with Trading212("key", "secret", transport=httpx.MockTransport(handler("[]"))) as client:
        allocation = client.app.portfolio.allocation()
        assert allocation.positions_value == 0 and allocation.entries == ()
    with Trading212(
        "key",
        "secret",
        transport=httpx.MockTransport(
            handler(
                '[{"instrument":{"ticker":"A"},"walletImpact":{"currency":"GBP","currentValue":0}}]'
            )
        ),
    ) as client:
        assert client.app.portfolio.allocation().entries[0].fraction is None


def test_exact_sum_ignores_global_decimal_precision_and_rejects_pathological_exponents():
    with localcontext() as context:
        context.prec = 3
        assert exact_sum([Decimal("123456789.123456789"), Decimal("0.000000001")]) == Decimal(
            "123456789.123456790"
        )
    with pytest.raises(DataUnavailableError, match="range"):
        exact_sum([Decimal("1e10001"), Decimal(1)])


def test_dividend_summary_uses_utc_year_and_separates_currencies():
    text = '{"items":[{"paidOn":"2026-01-01T00:30:00+01:00","currency":"GBP","amount":100},{"paidOn":"2026-01-01T00:30:00Z","currency":"GBP","amount":0.100000000000000001},{"paidOn":"2026-05-01T00:00:00Z","currency":"USD","amount":0.2}],"nextPagePath":null}'
    with Trading212(
        "key",
        "secret",
        transport=httpx.MockTransport(lambda request: httpx.Response(200, text=text)),
    ) as client:
        summary = client.app.dividends.summary(year=2026)
    assert [(v.currency, v.amount, v.count) for v in summary.totals] == [
        ("GBP", Decimal("0.100000000000000001"), 1),
        ("USD", Decimal("0.2"), 1),
    ]
    assert len(summary.sources) == 1 and not summary.sources[0].from_cache


def test_dividend_missing_timestamp_currency_and_incomplete_pagination_fail():
    for item in (
        '{"currency":"GBP","amount":1}',
        '{"paidOn":"2026-01-01T00:00:00Z","amount":1}',
        '{"paidOn":"2026-01-01T00:00:00","currency":"GBP","amount":1}',
    ):
        with (
            Trading212(
                "key",
                "secret",
                transport=httpx.MockTransport(
                    lambda request, item=item: httpx.Response(
                        200, text='{"items":[' + item + '],"nextPagePath":null}'
                    )
                ),
            ) as client,
            pytest.raises((DataUnavailableError, ResponseValidationError)),
        ):
            client.app.dividends.summary(year=2026)
    payload = '{"items":[],"nextPagePath":"/api/v0/equity/history/dividends?cursor=1&limit=50"}'
    with (
        Trading212(
            "key",
            "secret",
            transport=httpx.MockTransport(lambda request: httpx.Response(200, text=payload)),
        ) as client,
        pytest.raises(PaginationError),
    ):
        client.app.dividends.summary(max_pages=1)


def test_transaction_export_and_order_analysis_do_not_combine_currencies():
    def handle(request):
        if request.url.path.endswith("transactions"):
            return httpx.Response(
                200,
                text='{"items":[{"reference":"x,1","dateTime":"2026-01-01T00:00:00Z","type":"DEPOSIT","currency":"GBP","amount":0.100000000000000001},{"reference":"x2","currency":"USD","amount":-2}],"nextPagePath":null}',
            )
        return httpx.Response(
            200,
            text='{"items":[{"order":{"status":"FILLED","side":"BUY"}},{"order":{"status":"FILLED","side":"SELL"}},{"order":{"status":"CANCELLED","side":"BUY"}}],"nextPagePath":null}',
        )

    output = StringIO(newline="")
    with Trading212("key", "secret", transport=httpx.MockTransport(handle)) as client:
        exported = client.app.history.transactions_csv(output)
        analysis = client.app.history.order_analysis()
    rows = list(csv.reader(StringIO(output.getvalue())))
    assert exported.row_count == 2 and rows[1][0] == "x,1"
    assert rows[1][4] == "0.100000000000000001" and rows[2][3:] == ["USD", "-2"]
    assert analysis.item_count == 3
    assert [(x.category, x.count) for x in analysis.status_counts] == [
        ("CANCELLED", 1),
        ("FILLED", 2),
    ]


def test_async_application_uses_same_workflows():
    async def run():
        def handle(request):
            if request.url.path.endswith("summary"):
                return httpx.Response(200, text=ACCOUNT)
            if request.url.path.endswith("positions"):
                return httpx.Response(200, text=POSITIONS)
            return httpx.Response(200, text='{"items":[],"nextPagePath":null}')

        async with AsyncTrading212(
            "key", "secret", transport=httpx.MockTransport(handle)
        ) as client:
            report = await client.app.portfolio.report()
            assert report.allocation.positions_value == Decimal("0.300000000000000001")
            assert (await client.app.dividends.summary(year=2026)).totals == ()
            assert (await client.app.history.order_analysis()).item_count == 0
            output = StringIO()
            assert (await client.app.history.transactions_csv(output)).row_count == 0

    asyncio.run(run())


def test_dividend_summary_processes_all_pages_and_records_each_source():
    requests = []

    def handle(request):
        requests.append(request)
        if "cursor" not in request.url.params:
            return httpx.Response(
                200,
                text='{"items":[{"paidOn":"2026-04-01T00:00:00Z","currency":"GBP","amount":0.1}],"nextPagePath":"/api/v0/equity/history/dividends?cursor=1&limit=50"}',
            )
        return httpx.Response(
            200,
            text='{"items":[{"paidOn":"2026-05-01T00:00:00Z","currency":"GBP","amount":0.200000000000000001}],"nextPagePath":null}',
        )

    with Trading212("key", "secret", transport=httpx.MockTransport(handle)) as client:
        first = client.app.dividends.summary(year=2026)
        second = client.app.dividends.summary(year=2026)
    assert first.totals[0].amount == Decimal("0.300000000000000001")
    assert first.totals[0].count == 2
    assert [source.from_cache for source in first.sources] == [False, False]
    assert [source.from_cache for source in second.sources] == [True, True]
    assert len(requests) == 2


def test_transaction_export_can_be_partial_when_page_limit_is_reached():
    payload = '{"items":[{"reference":"first","currency":"GBP","amount":1}],"nextPagePath":"/api/v0/equity/history/transactions?cursor=1&limit=50"}'
    output = StringIO()
    with (
        Trading212(
            "key",
            "secret",
            transport=httpx.MockTransport(lambda request: httpx.Response(200, text=payload)),
        ) as client,
        pytest.raises(PaginationError),
    ):
        client.app.history.transactions_csv(output, max_pages=1)
    assert "first" in output.getvalue()


def test_fraction_rounding_is_independent_of_callers_decimal_traps():
    from decimal import Inexact

    from cztrading212.application.common import fraction

    with localcontext() as context:
        context.traps[Inexact] = True
        assert fraction(Decimal(1), Decimal(3)) == Decimal("0.3333333333333333333333333333333333")
