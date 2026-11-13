"""Service layer components."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Generic, TypeVar, cast

from .. import codec
from .._generated import parsers
from ..core.cache import Cache
from ..exceptions import ResponseValidationError

__all__ = ["ServiceResult", "_cached", "_store", "_typed"]

T = TypeVar("T")


@dataclass(frozen=True, slots=True, kw_only=True)
class ServiceResult(Generic[T]):
    """A value and the time it was obtained from the broker, including cache hits."""

    data: T
    fetched_at: datetime
    from_cache: bool


def _typed(operation: str, result: Any, *, status_code: int = 200) -> Any:
    if status_code != 200:
        raise ResponseValidationError(f"{operation}: undocumented success status {status_code}")
    parser = parsers.OPERATION_PARSERS.get(operation)
    if parser is None:
        raise ResponseValidationError(f"{operation}: no generated response parser is available")
    return parser(result)


def _cached(cache: Cache, key: str, operation: str) -> ServiceResult[Any] | None:
    encoded = cache.get(key)
    if encoded is None:
        return None
    try:
        envelope = codec.loads(encoded)
        if not isinstance(envelope, dict):
            raise TypeError("Invalid cache record")
        envelope = cast(dict[str, Any], envelope)
        if not isinstance(envelope.get("fetched_at"), str):
            raise TypeError("Invalid cache timestamp")
        timestamp = datetime.fromisoformat(envelope["fetched_at"])
        if timestamp.tzinfo is None:
            raise ValueError("Cache timestamp is missing a timezone")
        data = _typed(operation, envelope["data"])
    except (ValueError, KeyError, TypeError, ResponseValidationError) as exc:
        cache.invalidate(key)
        raise ResponseValidationError(f"{operation}: invalid cached response") from exc
    return ServiceResult(data=data, fetched_at=timestamp, from_cache=True)


def _store(
    cache: Cache,
    namespace: str,
    generation: int,
    key: str,
    raw: Any,
    ttl: float,
    fetched_at: datetime,
) -> None:
    cache.put_if_generation(
        namespace,
        generation,
        key,
        codec.dumps({"fetched_at": fetched_at.isoformat(), "data": raw}),
        ttl,
    )
