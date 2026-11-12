"""JSON codec that preserves Decimal values as JSON number tokens."""

import json
from decimal import Decimal
from typing import Any, cast


def loads(text: str) -> Any:
    def reject(value: str) -> None:
        raise ValueError(f"Invalid JSON constant: {value}")

    def unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                # Keys can themselves contain private data. Do not echo them.
                raise ValueError("Duplicate JSON object key")
            result[key] = value
        return result

    return json.loads(
        text, parse_float=Decimal, parse_constant=reject, object_pairs_hook=unique_object
    )


def dumps(value: Any) -> str:
    if value is None or isinstance(value, (str, bool, int)):
        return json.dumps(value, ensure_ascii=False)
    if isinstance(value, Decimal):
        if not value.is_finite():
            raise ValueError("Non-finite Decimal is not valid JSON")
        return str(value)
    if isinstance(value, float):
        raise TypeError("Use Decimal instead of float for JSON numbers")
    if isinstance(value, (list, tuple)):
        value = cast(list[Any] | tuple[Any, ...], value)
        return "[" + ",".join(dumps(item) for item in value) + "]"
    if isinstance(value, dict):
        value = cast(dict[str, Any], value)
        if not all(isinstance(key, str) for key in value):
            raise TypeError("JSON object keys must be strings")
        return (
            "{"
            + ",".join(json.dumps(k, ensure_ascii=False) + ":" + dumps(v) for k, v in value.items())
            + "}"
        )
    raise TypeError(f"Unsupported JSON type: {type(value).__name__}")
