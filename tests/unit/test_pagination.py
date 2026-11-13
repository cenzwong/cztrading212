import asyncio
import json

import httpx
import pytest

from cztrading212 import AsyncTrading212, PaginationError, Trading212


def test_history_pagination_yields_typed_pages_and_preserves_filters():
    requests = []

    def handle(request):
        requests.append(request)
        if len(requests) == 1:
            payload = {
                "items": [{"amount": 1}],
                "nextPagePath": "/api/v0/equity/history/dividends?limit=2&cursor=123&ticker=ABC",
            }
        else:
            payload = {"items": [{"amount": 2}], "nextPagePath": None}
        return httpx.Response(200, json=payload)

    with Trading212("key", "secret", transport=httpx.MockTransport(handle)) as client:
        pages = list(client.services.history.iter_pages("dividends", limit=2, ticker="ABC"))
    assert [page.data.items[0].amount for page in pages] == [1, 2]
    assert dict(requests[1].url.params) == {"limit": "2", "cursor": "123", "ticker": "ABC"}
    assert all(not page.from_cache for page in pages)


@pytest.mark.parametrize(
    "next_path",
    [
        "https://evil.test/api/v0/equity/history/dividends?cursor=1",
        "//evil.test/api/v0/equity/history/dividends?cursor=1",
        "/api/v0/equity/orders?cursor=1",
        "/api/v0/equity/history/dividends?cursor=1&cursor=2",
        "/api/v0/equity/history/dividends?limit=100&cursor=1",
        "/api/v0/equity/history/dividends?cursor=1&unknown=x",
    ],
)
def test_invalid_next_page_path_never_sends_another_request(next_path):
    requests = []

    def handle(request):
        requests.append(request)
        return httpx.Response(200, json={"items": [], "nextPagePath": next_path})

    with (
        Trading212("key", "secret", transport=httpx.MockTransport(handle)) as client,
        pytest.raises(PaginationError),
    ):
        list(client.services.history.iter_pages("dividends"))
    assert len(requests) == 1


def test_pagination_limit_and_cycle_are_not_silent():
    def handle(request):
        return httpx.Response(
            200, json={"items": [], "nextPagePath": "/api/v0/equity/history/orders?cursor=1"}
        )

    with Trading212("key", "secret", transport=httpx.MockTransport(handle)) as client:
        with pytest.raises(PaginationError, match="limit"):
            list(client.services.history.iter_pages("orders_1", max_pages=1))
        with pytest.raises(PaginationError, match="repeated"):
            list(client.services.history.iter_pages("orders_1", max_pages=4))


def test_async_pagination_and_missing_items():
    async def run():
        def handle(request):
            payload = {"items": [], "nextPagePath": None}
            if request.url.path.endswith("transactions"):
                payload = {"nextPagePath": None}
            return httpx.Response(200, text=json.dumps(payload))

        async with AsyncTrading212(
            "key", "secret", transport=httpx.MockTransport(handle)
        ) as client:
            pages = [page async for page in client.services.history.iter_pages("dividends")]
            assert len(pages) == 1
            with pytest.raises(PaginationError, match="missing items"):
                _ = [page async for page in client.services.history.iter_pages("transactions")]

    asyncio.run(run())
