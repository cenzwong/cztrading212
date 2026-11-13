import asyncio
import json
import sys
from datetime import UTC, datetime
from decimal import Decimal
from tempfile import TemporaryFile

import httpx
import pytest
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from typer.testing import CliRunner

from cztrading212 import RateLimiter, SQLiteCache, Trading212
from cztrading212._generated.models import AccountSummary
from cztrading212.cli.main import app
from cztrading212.mcp.server import build_server
from cztrading212.serialization import dumps, to_jsonable
from cztrading212.services import ServiceResult


def test_shared_serialization_precision_dataclasses_and_datetime():
    result = ServiceResult(
        data=AccountSummary(id=5, total_value=Decimal("0.1234567890123456789")),
        fetched_at=datetime(2026, 1, 1, tzinfo=UTC),
        from_cache=False,
    )
    decoded = json.loads(dumps(result))
    assert decoded["data"]["total_value"] == "0.1234567890123456789"
    assert decoded["fetched_at"] == "2026-01-01T00:00:00+00:00"
    with pytest.raises(TypeError):
        to_jsonable(0.1)


def test_cli_json_and_demo_live_configuration(monkeypatch):
    requests = []

    def handle(request):
        requests.append(request)
        return httpx.Response(200, text='{"id":5,"totalValue":0.1234567890123456789}')

    def factory(**kwargs):
        return Trading212(
            "test-key",
            "test-secret",
            transport=httpx.MockTransport(handle),
            rate_limiter=RateLimiter(policies={}),
            **kwargs,
        )

    monkeypatch.setattr(Trading212, "from_env", factory)
    result = CliRunner(mix_stderr=False).invoke(
        app, ["--env", "live", "account", "summary", "--json"]
    )
    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    assert payload["environment"] == "live"
    assert payload["data"]["data"]["total_value"] == "0.1234567890123456789"
    assert payload["data"]["from_cache"] is False
    assert result.stderr == ""
    assert requests[0].url.host == "live.trading212.com"


def test_cli_redacts_errors_and_validates_limits(monkeypatch):
    def factory(**kwargs):
        return Trading212(
            "secret-key",
            "secret-value",
            transport=httpx.MockTransport(lambda request: httpx.Response(401, text="secret-value")),
            rate_limiter=RateLimiter(policies={}),
            **kwargs,
        )

    monkeypatch.setattr(Trading212, "from_env", factory)
    runner = CliRunner(mix_stderr=False)
    result = runner.invoke(app, ["account", "summary", "--json"])
    assert result.exit_code == 3
    assert result.stdout == "" and "401" in result.stderr
    assert "secret-value" not in result.output and "secret-key" not in result.output
    invalid = runner.invoke(app, ["transactions", "list", "--limit", "51", "--json"])
    assert invalid.exit_code == 2


def test_cli_cache_stats_and_explicit_clear(tmp_path):
    path = tmp_path / "cache.sqlite"
    cache = SQLiteCache(path, clock=lambda: 100)
    cache.put("active", "{}", 10000000000)
    cache.put("expired", "{}", 1)
    runner = CliRunner(mix_stderr=False)
    stats = runner.invoke(app, ["--cache-path", str(path), "cache", "stats", "--json"])
    assert stats.exit_code == 0
    assert json.loads(stats.stdout) == {"active_entries": 1, "expired_entries": 1}
    refused = runner.invoke(app, ["--cache-path", str(path), "cache", "clear"])
    assert refused.exit_code == 2
    assert cache.stats().active_entries == 2
    cleared = runner.invoke(
        app, ["--cache-path", str(path), "cache", "clear", "--all-accounts", "--json"]
    )
    assert cleared.exit_code == 0 and json.loads(cleared.stdout)["cleared"]
    assert cache.stats().active_entries == 0


def test_mcp_tools_are_read_only_bounded_and_return_structured_precision():
    async def run():
        calls = []

        def handle(request):
            calls.append(request)
            return httpx.Response(200, text='{"id":1,"totalValue":0.1234567890123456789}')

        server = build_server(
            client_factory=lambda: Trading212(
                "key",
                "secret",
                transport=httpx.MockTransport(handle),
                rate_limiter=RateLimiter(policies={}),
            )
        )
        tools = await server.list_tools()
        assert {t.name for t in tools} == {
            "get_account_summary",
            "get_positions",
            "get_orders",
            "get_dividends",
            "get_transactions",
            "get_portfolio_snapshot",
            "get_portfolio_allocation",
            "get_dividend_summary",
        }
        assert all(t.annotations and t.annotations.readOnlyHint is True for t in tools)
        dividend_tool = next(t for t in tools if t.name == "get_dividends")
        assert dividend_tool.inputSchema["properties"]["limit"]["maximum"] == 50
        content, result = await server.call_tool("get_account_summary", {})
        assert content and result["ok"] is True
        assert result["environment"] == "demo"
        assert result["data"]["data"]["total_value"] == "0.1234567890123456789"
        with pytest.raises(Exception, match="less than or equal to 50"):
            await server.call_tool("get_dividends", {"limit": 51})
        assert len(calls) == 1

    asyncio.run(run())


def test_mcp_redacts_credential_values_and_unexpected_errors():
    async def run():
        def fail():
            raise RuntimeError("credential-value-must-not-escape")

        server = build_server(client_factory=fail)
        _, result = await server.call_tool("get_orders", {})
        assert result == {
            "ok": False,
            "error": {"code": "local_error", "message": "Local operation failed."},
        }
        assert "credential-value" not in json.dumps(result)

    asyncio.run(run())


def test_mcp_stdio_protocol_lists_tools_and_rejects_invalid_input():
    async def run():
        params = StdioServerParameters(
            command=sys.executable,
            args=["-c", "from cztrading212.mcp import main; main()"],
            env={"TRADING212_API_KEY": "test", "TRADING212_API_SECRET": "test"},
        )
        with TemporaryFile(mode="w+") as errors:
            async with (
                stdio_client(params, errlog=errors) as (reader, writer),
                ClientSession(reader, writer) as session,
            ):
                await session.initialize()
                tools = await session.list_tools()
                assert len(tools.tools) == 8
                result = await session.call_tool("get_dividends", {"limit": 51})
                assert result.isError

    asyncio.run(run())


def test_unexpected_cli_errors_do_not_expose_sensitive_details(monkeypatch):
    def fail(**kwargs):
        raise RuntimeError("credential-value-must-not-escape")

    monkeypatch.setattr(Trading212, "from_env", fail)
    result = CliRunner(mix_stderr=False).invoke(app, ["account", "summary", "--json"])
    assert result.exit_code == 1
    assert "credential-value" not in result.output
    assert result.stdout == ""


def test_mcp_strict_validation_rejects_bool_coercion_and_page_overflow():
    async def run():
        server = build_server()
        with pytest.raises(Exception, match="valid boolean"):
            await server.call_tool("get_orders", {"refresh": "true"})
        with pytest.raises(Exception, match="less than or equal to 100"):
            await server.call_tool("get_dividend_summary", {"max_pages": 101})

    asyncio.run(run())


def test_mcp_dividend_cursor_rejects_boolean_and_string():
    async def run():
        server = build_server()
        for invalid in (True, "12"):
            with pytest.raises(Exception, match="valid integer"):
                await server.call_tool("get_dividends", {"cursor": invalid})

    asyncio.run(run())
