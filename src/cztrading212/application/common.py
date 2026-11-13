"""Business-invariant and Decimal helpers."""

from __future__ import annotations

from decimal import ROUND_HALF_EVEN, Context, Decimal, localcontext
from typing import TypeVar

from ..exceptions import DataUnavailableError
from ..services import ServiceResult
from .models import SourceInfo

T = TypeVar("T")


def required(value: T | None, field: str) -> T:
    if value is None:
        raise DataUnavailableError(f"Required application input is missing: {field}")
    return value


def currency(value: str | None, field: str) -> str:
    result = required(value, field)
    if not result or result != result.strip():
        raise DataUnavailableError(f"Invalid currency in application input: {field}")
    return result


def source(
    result: ServiceResult[T], *, operation: str | None = None, scope: str | None = None
) -> SourceInfo:
    return SourceInfo(
        fetched_at=result.fetched_at, from_cache=result.from_cache, operation=operation, scope=scope
    )


def exact_sum(values: list[Decimal]) -> Decimal:
    """Add without rounding, refusing pathological exponents rather than exhausting memory."""
    if not values:
        return Decimal(0)
    if not all(value.is_finite() for value in values):
        raise DataUnavailableError("Cannot calculate with non-finite financial values")
    exponent_min = min(value.as_tuple().exponent for value in values)
    exponent_max = max(value.adjusted() for value in values)
    if not isinstance(exponent_min, int):
        raise DataUnavailableError("Invalid financial value")
    precision = max(28, exponent_max - exponent_min + len(str(len(values))) + 2)
    if precision > 10000 or abs(exponent_max) > 10000 or abs(exponent_min) > 10000:
        raise DataUnavailableError("Financial value exceeds supported calculation range")
    with localcontext(Context(prec=precision, rounding=ROUND_HALF_EVEN)):
        return sum(values[1:], values[0])


def fraction(value: Decimal, total: Decimal) -> Decimal | None:
    exact_sum([value, total])
    if total == 0:
        return None
    with localcontext(Context(prec=34, rounding=ROUND_HALF_EVEN)):
        return value / total
