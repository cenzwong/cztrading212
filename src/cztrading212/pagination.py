"""Bounded pagination helpers for the API's three historical collections."""

from __future__ import annotations

from collections.abc import AsyncIterator, Iterator
from typing import TYPE_CHECKING, Any
from urllib.parse import parse_qsl, urlsplit

from ._generated.models import (
    PaginatedResponseHistoricalOrder,
    PaginatedResponseHistoryDividendItem,
    PaginatedResponseHistoryTransactionItem,
)
from .exceptions import PaginationError

if TYPE_CHECKING:
    from .services import AsyncServices, ServiceResult, Services

HistoryPage = (
    PaginatedResponseHistoricalOrder
    | PaginatedResponseHistoryDividendItem
    | PaginatedResponseHistoryTransactionItem
)

_ENDPOINTS = {
    "dividends": ("/api/v0/equity/history/dividends", {"cursor", "ticker", "limit"}),
    "orders_1": ("/api/v0/equity/history/orders", {"cursor", "ticker", "limit"}),
    "transactions": ("/api/v0/equity/history/transactions", {"cursor", "time", "limit"}),
}


def _next_arguments(operation_id: str, next_path: str) -> dict[str, Any]:
    expected_path, allowed = _ENDPOINTS[operation_id]
    if any(ord(char) < 32 or ord(char) == 127 for char in next_path):
        raise PaginationError(f"{operation_id}: invalid next page path")
    try:
        parts = urlsplit(next_path)
        pairs = parse_qsl(parts.query, keep_blank_values=True, strict_parsing=True)
    except ValueError as exc:
        raise PaginationError(f"{operation_id}: invalid next page path") from exc
    # Only the documented relative path for the current operation is accepted.
    if parts.scheme or parts.netloc or parts.fragment or parts.path != expected_path:
        raise PaginationError(
            f"{operation_id}: next page path has an unexpected origin or endpoint"
        )
    arguments: dict[str, Any] = {}
    for name, value in pairs:
        if name not in allowed or name in arguments or not value:
            raise PaginationError(f"{operation_id}: invalid next page parameters")
        if name == "limit" or (name == "cursor" and operation_id != "transactions"):
            try:
                arguments[name] = int(value)
            except ValueError as exc:
                raise PaginationError(
                    f"{operation_id}: invalid numeric pagination parameter"
                ) from exc
        else:
            arguments[name] = value
    try:
        _check_options(operation_id, arguments, 1)
    except ValueError as exc:
        raise PaginationError(f"{operation_id}: invalid next page parameters") from exc
    return arguments


def _check_options(operation_id: str, arguments: dict[str, Any], max_pages: int) -> None:
    if operation_id not in _ENDPOINTS:
        raise ValueError("Pagination is available only for historical collection operations")
    if type(max_pages) is not int or max_pages < 1:
        raise ValueError("max_pages must be a positive integer")
    if not set(arguments) <= _ENDPOINTS[operation_id][1]:
        raise ValueError("Unsupported pagination argument")
    limit = arguments.get("limit")
    if limit is not None and (
        isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 50
    ):
        raise ValueError("limit must be between 1 and 50")


def _identity(arguments: dict[str, Any]) -> tuple[tuple[str, str], ...]:
    return tuple(sorted((key, str(value)) for key, value in arguments.items() if value is not None))


def _page_next(operation_id: str, page: HistoryPage) -> str | None:
    if page.items is None:
        raise PaginationError(f"{operation_id}: page is missing items")
    return page.next_page_path


def iter_pages(
    services: Services,
    operation_id: str,
    *,
    max_pages: int = 20,
    refresh: bool = False,
    **arguments: Any,
) -> Iterator[ServiceResult[HistoryPage]]:
    """Yield validated pages and their freshness; raise rather than truncate at the bound."""
    _check_options(operation_id, arguments, max_pages)
    seen = {_identity(arguments)}
    for index in range(max_pages):
        result = services.call_with_metadata(operation_id, refresh=refresh, **arguments)
        page: HistoryPage = result.data
        next_path = _page_next(operation_id, page)
        yield result
        if next_path is None:
            return
        arguments = _next_arguments(operation_id, next_path)
        identity = _identity(arguments)
        if identity in seen:
            raise PaginationError(f"{operation_id}: repeated page detected")
        seen.add(identity)
        if index + 1 == max_pages:
            raise PaginationError(f"{operation_id}: page limit reached before the end")


async def aiter_pages(
    services: AsyncServices,
    operation_id: str,
    *,
    max_pages: int = 20,
    refresh: bool = False,
    **arguments: Any,
) -> AsyncIterator[ServiceResult[HistoryPage]]:
    """Async equivalent of :func:`iter_pages`."""
    _check_options(operation_id, arguments, max_pages)
    seen = {_identity(arguments)}
    for index in range(max_pages):
        result = await services.call_with_metadata(operation_id, refresh=refresh, **arguments)
        page: HistoryPage = result.data
        next_path = _page_next(operation_id, page)
        yield result
        if next_path is None:
            return
        arguments = _next_arguments(operation_id, next_path)
        identity = _identity(arguments)
        if identity in seen:
            raise PaginationError(f"{operation_id}: repeated page detected")
        seen.add(identity)
        if index + 1 == max_pages:
            raise PaginationError(f"{operation_id}: page limit reached before the end")
