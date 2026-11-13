import asyncio
import json
from decimal import Decimal

import httpx
import pytest

from cztrading212 import AsyncTrading212, DataUnavailableError, PaginationError, Trading212

DIVIDENDS = [
    {"ticker": "A", "paidOn": "2025-01-01T00:30:00+01:00", "currency": "GBP", "amount": 4},
    {"ticker": "A", "paidOn": "2025-02-01T00:00:00Z", "currency": "GBP", "amount": 10},
    {"ticker": "A", "paidOn": "2026-02-01T00:00:00Z", "currency": "GBP", "amount": 15},
    {"ticker": "A", "paidOn": "2026-02-01T00:00:00Z", "currency": "USD", "amount": 3},
]
TRANSACTIONS = [
    {
        "reference": "same",
        "dateTime": "2026-03-01T00:30:00+01:00",
        "currency": "GBP",
        "amount": 10,
        "type": "DEPOSIT",
    },
    {
        "reference": "same",
        "dateTime": "2026-01-01T00:00:00Z",
        "currency": "GBP",
        "amount": -4,
        "type": "WITHDRAW",
    },
    {"dateTime": "2026-02-01T00:00:00Z", "currency": "USD", "amount": 5, "type": "TRANSFER"},
    {"dateTime": "2026-02-01T00:00:00Z", "currency": "GBP", "amount": 2, "type": "UNKNOWN"},
]


def transport(dividends=DIVIDENDS, transactions=TRANSACTIONS, next_page=None):
    def handle(request):
        items = dividends if request.url.path.endswith("dividends") else transactions
        return httpx.Response(200, json={"items": items, "nextPagePath": next_page})

    return httpx.MockTransport(handle)


def test_dividend_report_growth_and_utc_boundary():
    with Trading212("key", "secret", transport=transport()) as client:
        report = client.app.dividends.income_report()
        growth = client.app.dividends.growth()
        tickers = client.app.dividends.by_ticker()
    assert report.payment_count == 4
    assert [(e.year, e.month, e.amount) for e in report.monthly] == [
        (2024, 12, 4),
        (2025, 2, 10),
        (2026, 2, 15),
        (2026, 2, 3),
    ]
    assert [(e.currency, e.amount) for e in report.by_currency] == [("GBP", 29), ("USD", 3)]
    assert growth.annual_changes[-1].change == 5
    assert growth.annual_changes[-1].change_fraction == Decimal("0.5")
    assert growth.ticker_changes[-1].ticker == "A"
    assert tickers.entries == report.by_ticker
    assert report.provenance.record_count == 4
    assert report.provenance.pages[0].operation == "dividends"
    assert report.provenance.pages[0].scope == "history page"
    assert report.provenance.completeness == "complete_traversal"


def test_cashflow_uses_exact_types_utc_and_preserves_duplicates():
    with Trading212("key", "secret", transport=transport()) as client:
        report = client.app.cashflow.summary()
        monthly = client.app.cashflow.monthly()
    assert report.provenance.record_count == 4
    assert [(e.category, e.transaction_type, e.currency, e.amount) for e in report.categories] == [
        ("deposits", "DEPOSIT", "GBP", 10),
        ("unclassified", "TRANSFER", "USD", 5),
        ("unclassified", "UNKNOWN", "GBP", 2),
        ("withdrawals", "WITHDRAW", "GBP", -4),
    ]
    assert [(p.year, p.month) for p in monthly.periods] == [(2026, 1), (2026, 2)]
    assert sum(e.count for p in monthly.periods for e in p.categories) == 4


def test_history_rejects_missing_data_and_page_limit():
    for method in ("dividends", "transactions"):
        path = f"/api/v0/equity/history/{method}?cursor=1&limit=50"
        with (
            Trading212("key", "secret", transport=transport(next_page=path)) as client,
            pytest.raises(PaginationError),
        ):
            (
                client.app.dividends.income_report
                if method == "dividends"
                else client.app.cashflow.summary
            )(max_pages=1)
    missing = [{"currency": "GBP", "amount": 1, "type": "DEPOSIT"}]
    with Trading212("key", "secret", transport=transport(transactions=missing)) as client:
        assert client.app.cashflow.summary().provenance.record_count == 1
        with pytest.raises(DataUnavailableError):
            client.app.cashflow.monthly()
    with (
        Trading212(
            "key", "secret", transport=transport(dividends=[{"currency": "GBP", "amount": 1}])
        ) as client,
        pytest.raises(DataUnavailableError),
    ):
        client.app.dividends.income_report()


def test_multiple_pages_and_async_parity():
    requests = []

    def handle(request):
        requests.append(str(request.url))
        items = DIVIDENDS[:2] if "cursor" not in request.url.params else DIVIDENDS[2:]
        next_page = (
            "/api/v0/equity/history/dividends?cursor=1&limit=50"
            if "cursor" not in request.url.params
            else None
        )
        return httpx.Response(200, text=json.dumps({"items": items, "nextPagePath": next_page}))

    async def run():
        async with AsyncTrading212(
            "key", "secret", transport=httpx.MockTransport(handle)
        ) as client:
            report = await client.app.dividends.income_report()
            assert report.payment_count == 4
            assert report.provenance.page_count == 2
            assert len(report.provenance.pages) == 2
        async with AsyncTrading212("key", "secret", transport=transport()) as client:
            assert (await client.app.cashflow.summary()).provenance.record_count == 4
            assert len((await client.app.cashflow.monthly()).periods) == 2

    asyncio.run(run())
    assert len(requests) == 2


def test_zero_dividend_period_has_no_percentage_growth():
    dividends = [
        {"ticker": "A", "paidOn": "2025-01-01T00:00:00Z", "currency": "GBP", "amount": 0},
        {"ticker": "A", "paidOn": "2026-01-01T00:00:00Z", "currency": "GBP", "amount": 5},
    ]
    with Trading212("key", "secret", transport=transport(dividends=dividends)) as client:
        report = client.app.dividends.growth()
    assert report.annual_changes[0].previous_amount == 0
    assert report.annual_changes[0].change == 5
    assert report.annual_changes[0].change_fraction is None
    assert report.ticker_changes[0].change_fraction is None


def test_cashflow_traverses_multiple_pages_without_merging_currencies():
    def handle(request):
        if "cursor" not in request.url.params:
            return httpx.Response(
                200,
                json={
                    "items": TRANSACTIONS[:2],
                    "nextPagePath": "/api/v0/equity/history/transactions?cursor=next&limit=50",
                },
            )
        return httpx.Response(200, json={"items": TRANSACTIONS[2:], "nextPagePath": None})

    with Trading212("key", "secret", transport=httpx.MockTransport(handle)) as client:
        report = client.app.cashflow.summary()
    assert report.provenance.page_count == 2
    assert report.provenance.record_count == 4
    assert {item.currency for item in report.categories} == {"GBP", "USD"}
