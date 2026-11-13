import asyncio

import httpx
import pytest

from cztrading212 import AsyncTrading212, PaginationError, Trading212

INSTRUMENTS = [
    {"ticker": "B", "isin": "US1", "name": "Apple Class B", "type": "STOCK"},
    {"ticker": "A", "isin": "US1", "name": "Apple Class A", "type": "STOCK"},
    {"ticker": "A", "isin": "US2", "name": "Other"},
]
TRANSACTIONS = [
    {
        "reference": "duplicate",
        "dateTime": "2026-01-01T00:00:00Z",
        "currency": "GBP",
        "amount": 1,
        "type": "DEPOSIT",
    },
    {
        "reference": "duplicate",
        "dateTime": "2026-01-01T00:00:00Z",
        "currency": "GBP",
        "amount": 1,
        "type": "DEPOSIT",
    },
    {"type": "NEW_TYPE"},
]
ORDERS = [{"order": {"id": 1, "ticker": "A", "instrument": {"ticker": "B"}}}, {"fill": {"id": 2}}]


def transport(next_page=None):
    def handler(request):
        path = request.url.path
        if path.endswith("instruments"):
            return httpx.Response(200, json=INSTRUMENTS)
        items = TRANSACTIONS if path.endswith("transactions") else ORDERS
        return httpx.Response(200, json={"items": items, "nextPagePath": next_page})

    return httpx.MockTransport(handler)


def test_search_exact_identifiers_casefold_names_and_limit():
    with Trading212("key", "secret", transport=transport()) as client:
        results = client.app.instruments.search("apple", limit=1)
        assert results.total_matches == 2
        assert results.matches[0].instrument.ticker == "A"
        assert results.matches[0].matched_by == ("name",)
        assert [m.instrument.ticker for m in client.app.instruments.lookup_isin("us1").matches] == [
            "A",
            "B",
        ]
        assert (
            sum("ticker" in m.matched_by for m in client.app.instruments.search("a").matches) == 2
        )
        assert client.app.instruments.search("no match").matches == ()
        with pytest.raises(ValueError):
            client.app.instruments.search("", limit=0)


def test_audit_reports_evidence_without_claiming_reconciliation():
    with Trading212("key", "secret", transport=transport()) as client:
        result = client.app.audit.reconcile()
    assert not result.fully_reconciled
    report = result.audit
    assert report.transaction_source.record_count == 3
    assert report.order_source.record_count == 2
    codes = {finding.code: finding for finding in report.findings}
    assert codes["duplicate_reference"].certainty == "confirmed"
    assert codes["repeated_transaction"].certainty == "potential"
    assert codes["conflicting_order_tickers"].evidence == (
        "orders[0]",
        "ticker=A",
        "instrument.ticker=B",
    )
    assert "missing_transaction_fields" in codes
    assert "unclassified_transaction_type" in codes
    assert "missing_order" in codes
    assert all("missing_transaction" != f.code for f in report.findings)


def test_audit_does_not_return_partial_result():
    path = "/api/v0/equity/history/transactions?cursor=1&limit=50"
    with (
        Trading212("key", "secret", transport=transport(path)) as client,
        pytest.raises(PaginationError),
    ):
        client.app.audit.detect_gaps(max_pages=1)


def test_async_discovery_and_audit():
    async def run():
        async with AsyncTrading212("key", "secret", transport=transport()) as client:
            assert (await client.app.instruments.lookup_isin("US1")).total_matches == 2
            assert not (await client.app.audit.reconcile()).fully_reconciled
            assert len((await client.app.audit.detect_gaps()).findings) >= 5

    asyncio.run(run())
