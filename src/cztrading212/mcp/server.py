"""Curated read-only MCP tools backed by SDK services and application workflows."""

from __future__ import annotations

import os
from collections.abc import Callable
from pathlib import Path
from typing import Annotated, Any

from mcp.server.fastmcp import FastMCP
from mcp.types import ToolAnnotations
from pydantic import Field

from .. import (
    APIError,
    MemoryCache,
    RateLimiter,
    RateLimitError,
    SQLiteCache,
    Trading212,
    Trading212Error,
)
from ..core.cache import Cache
from ..serialization import to_jsonable

ClientFactory = Callable[[], Trading212]
READ_ONLY = ToolAnnotations(readOnlyHint=True, destructiveHint=False, openWorldHint=True)
Limit = Annotated[
    int, Field(strict=True, ge=1, le=50, description="Maximum records in this page (1–50).")
]
MaxPages = Annotated[
    int, Field(strict=True, ge=1, le=100, description="Maximum pages to retrieve (1–100).")
]
Refresh = Annotated[bool, Field(strict=True, description="Bypass any cached response.")]
DividendCursor = Annotated[int | None, Field(strict=True, description="Dividend page cursor.")]
Year = Annotated[
    int | None,
    Field(strict=True, ge=1, le=9999, description="Payment year in UTC, or null for all years."),
]


def _invoke(factory: ClientFactory, action: Callable[[Trading212], Any]) -> dict[str, Any]:
    try:
        with factory() as client:
            data = action(client)
            return {"ok": True, "environment": client.environment.value, "data": to_jsonable(data)}
    except RateLimitError as exc:
        return {
            "ok": False,
            "error": {
                "code": "rate_limited",
                "message": str(exc),
                "retry_after": str(exc.retry_after),
            },
        }
    except APIError as exc:
        return {
            "ok": False,
            "error": {"code": "broker_error", "message": str(exc), "status_code": exc.status_code},
        }
    except Trading212Error:
        return {
            "ok": False,
            "error": {"code": "sdk_error", "message": "SDK request or response failed validation."},
        }
    except (ValueError, TypeError):
        return {
            "ok": False,
            "error": {"code": "invalid_input", "message": "Invalid configuration or input."},
        }
    except Exception:  # noqa: BLE001 - prevent unexpected tool errors from exposing sensitive details
        # Do not expose implementation details, response values, or credentials to tools.
        return {"ok": False, "error": {"code": "local_error", "message": "Local operation failed."}}


def build_server(
    *,
    environment: str | None = None,
    cache: Cache | None = None,
    client_factory: ClientFactory | None = None,
) -> FastMCP[Any]:
    """Create a server with no network calls; tools initialize clients on demand."""
    selected = environment or os.environ.get("TRADING212_ENVIRONMENT", "demo")
    if selected not in {"demo", "live"}:
        raise ValueError("Trading environment must be demo or live")
    shared_cache = cache if cache is not None else MemoryCache()
    limiter = RateLimiter()
    factory: ClientFactory = client_factory or (
        lambda: Trading212.from_env(environment=selected, cache=shared_cache, rate_limiter=limiter)
    )
    server: FastMCP[Any] = FastMCP(
        "cztrading212",
        log_level="ERROR",
        instructions="Read-only Trading 212 account and history tools. Decimal values are strings. Data may be cached; inspect freshness metadata.",
    )

    @server.tool(annotations=READ_ONLY, structured_output=True)
    def get_account_summary(refresh: Refresh = False) -> dict[str, Any]:
        """Get the account summary and its retrieval metadata."""
        return _invoke(
            factory, lambda c: c.services.call_with_metadata("getAccountSummary", refresh=refresh)
        )

    @server.tool(annotations=READ_ONLY, structured_output=True)
    def get_positions(ticker: str | None = None, refresh: Refresh = False) -> dict[str, Any]:
        """Get open positions and their retrieval metadata."""
        return _invoke(
            factory,
            lambda c: c.services.call_with_metadata("getPositions", ticker=ticker, refresh=refresh),
        )

    @server.tool(annotations=READ_ONLY, structured_output=True)
    def get_orders(refresh: Refresh = False) -> dict[str, Any]:
        """Get active orders and their retrieval metadata."""
        return _invoke(factory, lambda c: c.services.call_with_metadata("orders", refresh=refresh))

    @server.tool(annotations=READ_ONLY, structured_output=True)
    def get_dividends(
        limit: Limit = 20,
        cursor: DividendCursor = None,
        ticker: str | None = None,
        refresh: Refresh = False,
    ) -> dict[str, Any]:
        """Get one bounded page of dividend history and freshness metadata."""
        return _invoke(
            factory,
            lambda c: c.services.call_with_metadata(
                "dividends", limit=limit, cursor=cursor, ticker=ticker, refresh=refresh
            ),
        )

    @server.tool(annotations=READ_ONLY, structured_output=True)
    def get_transactions(
        limit: Limit = 20, cursor: str | None = None, refresh: Refresh = False
    ) -> dict[str, Any]:
        """Get one bounded page of transaction history and freshness metadata."""
        return _invoke(
            factory,
            lambda c: c.services.call_with_metadata(
                "transactions", limit=limit, cursor=cursor, refresh=refresh
            ),
        )

    @server.tool(annotations=READ_ONLY, structured_output=True)
    def get_portfolio_snapshot(refresh: Refresh = False) -> dict[str, Any]:
        """Get a portfolio snapshot with separate account and position source times."""
        return _invoke(factory, lambda c: c.app.portfolio.snapshot(refresh=refresh))

    @server.tool(annotations=READ_ONLY, structured_output=True)
    def get_portfolio_allocation(refresh: Refresh = False) -> dict[str, Any]:
        """Get position allocation in the account's primary currency."""
        return _invoke(factory, lambda c: c.app.portfolio.allocation(refresh=refresh))

    @server.tool(annotations=READ_ONLY, structured_output=True)
    def get_dividend_summary(
        year: Year = None, max_pages: MaxPages = 20, refresh: Refresh = False
    ) -> dict[str, Any]:
        """Summarize dividend amounts by currency, with bounded pagination."""
        return _invoke(
            factory,
            lambda c: c.app.dividends.summary(year=year, max_pages=max_pages, refresh=refresh),
        )

    return server


def run(*, environment: str | None = None, cache_path: Path | None = None) -> None:
    """Serve using stdio only."""
    cache = SQLiteCache(cache_path) if cache_path is not None else None
    build_server(environment=environment, cache=cache).run(transport="stdio")
