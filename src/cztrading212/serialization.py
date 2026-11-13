"""Precision-safe, explicit serialization for application adapters."""

from __future__ import annotations

import dataclasses
from datetime import date, datetime
from decimal import Decimal
from enum import Enum
from typing import Any, cast

from . import codec


def to_jsonable(value: Any) -> Any:
    """Convert SDK results to JSON-compatible values; Decimal becomes a string."""
    if isinstance(value, Enum):
        return to_jsonable(value.value)
    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, Decimal):
        if not value.is_finite():
            raise ValueError("Non-finite Decimal cannot be serialized")
        return str(value)
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        return {
            field.name: to_jsonable(getattr(value, field.name))
            for field in dataclasses.fields(value)
        }
    if isinstance(value, (tuple, list)):
        return [to_jsonable(item) for item in cast(tuple[Any, ...] | list[Any], value)]
    if isinstance(value, dict):
        mapping = cast(dict[Any, Any], value)
        if not all(isinstance(key, str) for key in mapping):
            raise TypeError("JSON object keys must be strings")
        return {key: to_jsonable(item) for key, item in mapping.items()}
    raise TypeError(f"Unsupported output type: {type(value).__name__}")


def dumps(value: Any) -> str:
    """Serialize an SDK result as JSON, with exact Decimal strings."""
    return codec.dumps(to_jsonable(value))
