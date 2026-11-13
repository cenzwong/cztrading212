"""Read-only Typer adapter to the SDK services and application."""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Annotated, Any

import typer

from .. import APIError, RateLimitError, SQLiteCache, Trading212, Trading212Error
from ..core.cache import CacheStats
from ..serialization import dumps, to_jsonable

app = typer.Typer(no_args_is_help=True, help="Read-only Trading 212 SDK commands.")
account = typer.Typer(no_args_is_help=True)
positions = typer.Typer(no_args_is_help=True)
orders = typer.Typer(no_args_is_help=True)
portfolio = typer.Typer(no_args_is_help=True)
dividends = typer.Typer(no_args_is_help=True)
transactions = typer.Typer(no_args_is_help=True)
cache_app = typer.Typer(no_args_is_help=True)
mcp_app = typer.Typer(no_args_is_help=True)
for name, group in (
    ("account", account),
    ("positions", positions),
    ("orders", orders),
    ("portfolio", portfolio),
    ("dividends", dividends),
    ("transactions", transactions),
    ("cache", cache_app),
    ("mcp", mcp_app),
):
    app.add_typer(group, name=name)


@dataclass(frozen=True, slots=True)
class Options:
    environment: str
    cache_path: Path | None


@app.callback()
def options(
    ctx: typer.Context,
    environment: Annotated[
        str,
        typer.Option(
            "--env", envvar="TRADING212_ENVIRONMENT", help="Trading environment: demo or live."
        ),
    ] = "demo",
    cache_path: Annotated[
        Path | None,
        typer.Option("--cache-path", envvar="CZ212_CACHE_PATH", help="Optional SQLite cache path."),
    ] = None,
) -> None:
    if environment not in {"demo", "live"}:
        raise typer.BadParameter("Choose demo or live", param_hint="--env")
    ctx.obj = Options(environment=environment, cache_path=cache_path)


def _options(ctx: typer.Context) -> Options:
    if not isinstance(ctx.obj, Options):
        raise TypeError("CLI configuration missing")
    return ctx.obj


def _emit(value: Any, json_output: bool) -> None:
    if json_output:
        typer.echo(dumps(value))
    else:
        typer.echo(json.dumps(to_jsonable(value), ensure_ascii=False, indent=2))


def _error(exc: Exception) -> None:
    if isinstance(exc, RateLimitError):
        typer.echo(f"Rate limited: {exc}", err=True)
        raise typer.Exit(4) from None
    if isinstance(exc, APIError):
        typer.echo(f"Broker request failed: {exc}", err=True)
        raise typer.Exit(3) from None
    if isinstance(exc, Trading212Error):
        typer.echo("SDK request or response failed validation.", err=True)
        raise typer.Exit(1) from None
    if isinstance(exc, (ValueError, TypeError)):
        typer.echo("Invalid configuration or input.", err=True)
        raise typer.Exit(2) from None
    typer.echo("Local operation failed; check cache permissions and configuration.", err=True)
    raise typer.Exit(1) from None


def _run(ctx: typer.Context, action: Callable[[Trading212], Any], json_output: bool) -> None:
    settings = _options(ctx)
    try:
        cache = SQLiteCache(settings.cache_path) if settings.cache_path is not None else None
        with Trading212.from_env(environment=settings.environment, cache=cache) as client:
            data = action(client)
        _emit({"environment": settings.environment, "data": data}, json_output)
    except (Trading212Error, ValueError, TypeError, OSError, sqlite3.Error) as exc:
        _error(exc)
    except Exception as exc:  # noqa: BLE001 - do not expose credentials in unexpected adapter errors
        _error(exc)


@account.command("summary")
def account_summary(
    ctx: typer.Context,
    json_output: Annotated[bool, typer.Option("--json")] = False,
    refresh: bool = False,
) -> None:
    _run(
        ctx,
        lambda c: c.services.call_with_metadata("getAccountSummary", refresh=refresh),
        json_output,
    )


@positions.command("list")
def positions_list(
    ctx: typer.Context,
    json_output: Annotated[bool, typer.Option("--json")] = False,
    refresh: bool = False,
    ticker: str | None = None,
) -> None:
    _run(
        ctx,
        lambda c: c.services.call_with_metadata("getPositions", refresh=refresh, ticker=ticker),
        json_output,
    )


@orders.command("list")
def orders_list(
    ctx: typer.Context,
    json_output: Annotated[bool, typer.Option("--json")] = False,
    refresh: bool = False,
) -> None:
    _run(ctx, lambda c: c.services.call_with_metadata("orders", refresh=refresh), json_output)


@portfolio.command("snapshot")
def portfolio_snapshot(
    ctx: typer.Context,
    json_output: Annotated[bool, typer.Option("--json")] = False,
    refresh: bool = False,
) -> None:
    _run(ctx, lambda c: c.app.portfolio.snapshot(refresh=refresh), json_output)


@portfolio.command("allocation")
def portfolio_allocation(
    ctx: typer.Context,
    json_output: Annotated[bool, typer.Option("--json")] = False,
    refresh: bool = False,
) -> None:
    _run(ctx, lambda c: c.app.portfolio.allocation(refresh=refresh), json_output)


@dividends.command("summary")
def dividends_summary(
    ctx: typer.Context,
    year: Annotated[int | None, typer.Option(min=1, max=9999)] = None,
    max_pages: Annotated[int, typer.Option(min=1, max=100)] = 20,
    json_output: Annotated[bool, typer.Option("--json")] = False,
    refresh: bool = False,
) -> None:
    _run(
        ctx,
        lambda c: c.app.dividends.summary(year=year, max_pages=max_pages, refresh=refresh),
        json_output,
    )


@transactions.command("list")
def transactions_list(
    ctx: typer.Context,
    limit: Annotated[int, typer.Option(min=1, max=50)] = 20,
    cursor: str | None = None,
    json_output: Annotated[bool, typer.Option("--json")] = False,
    refresh: bool = False,
) -> None:
    _run(
        ctx,
        lambda c: c.services.call_with_metadata(
            "transactions", limit=limit, cursor=cursor, refresh=refresh
        ),
        json_output,
    )


def _persistent_cache(ctx: typer.Context) -> SQLiteCache:
    path = _options(ctx).cache_path
    if path is None:
        raise ValueError("A persistent cache path is required")
    if not path.expanduser().is_file():
        raise ValueError("The specified cache file does not exist")
    return SQLiteCache(path)


@cache_app.command("stats")
def cache_stats(
    ctx: typer.Context, json_output: Annotated[bool, typer.Option("--json")] = False
) -> None:
    try:
        stats: CacheStats = _persistent_cache(ctx).stats()
        _emit(stats, json_output)
    except (ValueError, OSError, sqlite3.Error) as exc:
        _error(exc)


@cache_app.command("clear")
def cache_clear(
    ctx: typer.Context,
    all_accounts: Annotated[
        bool,
        typer.Option(
            "--all-accounts", help="Clear every account and environment in the selected cache."
        ),
    ] = False,
    json_output: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    if not all_accounts:
        typer.echo(
            "Pass --all-accounts to clear the selected cache for all accounts and environments.",
            err=True,
        )
        raise typer.Exit(2)
    try:
        store = _persistent_cache(ctx)
        store.clear()
        _emit({"cleared": True}, json_output)
    except (ValueError, OSError, sqlite3.Error) as exc:
        _error(exc)


@mcp_app.command("serve")
def mcp_serve(
    ctx: typer.Context,
    transport: Annotated[str, typer.Option(help="Only stdio is supported.")] = "stdio",
) -> None:
    if transport != "stdio":
        raise typer.BadParameter("Only stdio is supported")
    try:
        from ..mcp.server import run
    except ModuleNotFoundError as exc:
        if exc.name == "mcp":
            typer.echo("The MCP extra is required: pip install 'cztrading212[mcp]'", err=True)
            raise typer.Exit(2) from None
        raise
    settings = _options(ctx)
    try:
        run(environment=settings.environment, cache_path=settings.cache_path)
    except (ValueError, OSError, sqlite3.Error) as exc:
        _error(exc)
