import asyncio
import base64
from decimal import Decimal

import httpx
import pytest

from cztrading212 import APIError, AsyncTrading212, ResponseValidationError, Trading212, codec
from cztrading212.client import OPERATIONS


def test_raw_auth_decimal_and_no_cache():
    requests = []

    def handle(request):
        requests.append(request)
        return httpx.Response(200, text='{"id":123,"totalValue":123.4567890123456789,"newField":1}')

    with Trading212("key", "secret", transport=httpx.MockTransport(handle)) as client:
        result = client.raw.account.get_account_summary()
        account = client.services.call("getAccountSummary")
    assert len(requests) == 2
    assert requests[0].url.host == "demo.trading212.com"
    assert requests[0].url.path == "/api/v0/equity/account/summary"
    assert (
        requests[0].headers["authorization"] == "Basic " + base64.b64encode(b"key:secret").decode()
    )
    assert result["totalValue"] == Decimal("123.4567890123456789")
    assert result["newField"] == 1
    assert account.total_value == Decimal("123.4567890123456789")


def test_request_precision_and_no_retry():
    seen = []

    def handle(request):
        seen.append(request)
        return httpx.Response(503)

    with (
        Trading212("key", "secret", transport=httpx.MockTransport(handle)) as client,
        pytest.raises(APIError),
    ):
        client.raw.orders.place_market_order(
            body={"ticker": "AAPL", "quantity": Decimal("0.10000000000000001")}
        )
    assert len(seen) == 1
    assert b"0.10000000000000001" in seen[0].content


def test_path_escaping_and_unknown_args():
    seen = []

    def handle(request):
        seen.append(request)
        return httpx.Response(200, json={})

    with Trading212("key", "secret", transport=httpx.MockTransport(handle)) as client:
        client.raw.orders.get_order(id="1/2")
        with pytest.raises(TypeError):
            client.raw.orders.get_orders(oops=1)
    assert seen[0].url.raw_path.endswith(b"/1%2F2")


def test_raw_metadata_preserves_success_status_and_empty_body():
    transport = httpx.MockTransport(lambda request: httpx.Response(204))
    with Trading212("key", "secret", transport=transport) as client:
        response = client.raw.call_with_metadata("cancelOrder", id=123)
    assert response.status_code == 204
    assert response.data is None


def test_services_reject_undocumented_success_status_without_caching():
    from cztrading212 import RateLimiter

    requests = []

    def handle(request):
        requests.append(request)
        return httpx.Response(201, json={"id": 1})

    with Trading212(
        "key",
        "secret",
        transport=httpx.MockTransport(handle),
        rate_limiter=RateLimiter(policies={}),
    ) as client:
        for _ in range(2):
            with pytest.raises(ResponseValidationError, match="undocumented success status 201"):
                client.services.account.summary()
    assert len(requests) == 2


@pytest.mark.parametrize("segment", ["", ".", ".."])
def test_path_parameters_cannot_change_the_requested_endpoint(segment):
    seen = []

    def handle(request):
        seen.append(request)
        return httpx.Response(204)

    with (
        Trading212("key", "secret", transport=httpx.MockTransport(handle)) as client,
        pytest.raises(ValueError, match="Invalid path parameter"),
    ):
        client.raw.call("cancelOrder", id=segment)
    assert not seen


@pytest.mark.parametrize("segment", ["", ".", ".."])
def test_async_path_parameters_cannot_change_the_requested_endpoint(segment):
    async def run():
        seen = []

        def handle(request):
            seen.append(request)
            return httpx.Response(204)

        async with AsyncTrading212(
            "key", "secret", transport=httpx.MockTransport(handle)
        ) as client:
            with pytest.raises(ValueError, match="Invalid path parameter"):
                await client.raw.call("cancelOrder", id=segment)
        assert not seen

    asyncio.run(run())


def test_validation_and_forward_compatibility():
    def handle(request):
        return httpx.Response(200, text='{"id":true,"unknown":"ignored"}')

    with (
        Trading212("key", "secret", transport=httpx.MockTransport(handle)) as client,
        pytest.raises(ResponseValidationError, match=r"getAccountSummary.*\$\.id"),
    ):
        client.services.call("getAccountSummary")


@pytest.mark.parametrize("payload", ['{"id":1,"id":2}', '{"nested":{"id":1,"id":2}}'])
def test_raw_rejects_ambiguous_duplicate_json_keys(payload):
    from cztrading212 import Trading212Error

    with (
        Trading212(
            "key",
            "secret",
            transport=httpx.MockTransport(lambda request: httpx.Response(200, text=payload)),
        ) as client,
        pytest.raises(Trading212Error, match="Invalid JSON response"),
    ):
        client.raw.account.get_account_summary()


def test_raw_rejects_malformed_utf8_instead_of_replacing_data():
    from cztrading212 import Trading212Error

    transport = httpx.MockTransport(
        lambda request: httpx.Response(200, content=b'{"currency":"G\xffP"}')
    )
    with (
        Trading212("key", "secret", transport=transport) as client,
        pytest.raises(Trading212Error, match="Invalid JSON response"),
    ):
        client.raw.account.get_account_summary()


def test_async_and_catalog():
    async def run():
        async with AsyncTrading212(
            "key",
            "secret",
            transport=httpx.MockTransport(
                lambda request: httpx.Response(200, text='[{"quantity":0.125}]')
            ),
        ) as client:
            result = await client.raw.positions.get_positions()
            typed = await client.services.call("getPositions")
            assert result[0]["quantity"] == Decimal("0.125")
            assert typed[0].quantity == Decimal("0.125")

    asyncio.run(run())
    assert len(OPERATIONS) == 22


def test_codec_precision_and_rejects_float():
    assert (
        codec.dumps({"value": Decimal("0.100000000000000001")}) == '{"value":0.100000000000000001}'
    )
    with pytest.raises(TypeError):
        codec.dumps(0.1)
    with pytest.raises(ValueError):
        codec.loads('{"value":NaN}')


def test_documented_final_page_accepts_null_cursor():
    def handle(request):
        return httpx.Response(200, text='{"items":[],"nextPagePath":null}')

    with Trading212("key", "secret", transport=httpx.MockTransport(handle)) as client:
        page = client.services.call("dividends")
    assert page.items == ()
    assert page.next_page_path is None


def test_nullable_union_is_validated_and_preserves_decimal():
    from cztrading212.parsing import parse

    schema = {"$ref": "#/components/schemas/PositionWalletImpact"}
    assert parse({"fxImpact": None}, schema).fx_impact is None
    assert parse({"fxImpact": Decimal("0.000000000000000000123")}, schema).fx_impact == Decimal(
        "0.000000000000000000123"
    )
    with pytest.raises(ResponseValidationError, match=r"\$\.fxImpact"):
        parse({"fxImpact": "invalid"}, schema)
