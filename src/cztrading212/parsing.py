"""Schema-directed validation for generated dataclasses."""

from __future__ import annotations

import json
import keyword
import re
from datetime import date, datetime
from decimal import Decimal
from importlib.resources import files
from typing import Any, NoReturn, cast

from ._generated import models
from .exceptions import ResponseValidationError

SCHEMAS: dict[str, dict[str, Any]] = json.loads(
    files("cztrading212._generated").joinpath("schemas.json").read_text()
)


def _name(name: str) -> str:
    value = re.sub(r"\W", "_", re.sub(r"(?<!^)(?=[A-Z])", "_", name).lower())
    return value + "_" if keyword.iskeyword(value) else value


def _json_identity(value: Any) -> Any:
    """Hashable JSON equality, keeping booleans distinct from numbers."""
    if value is None or isinstance(value, (bool, str)):
        return (type(value).__name__, value)
    if isinstance(value, (int, Decimal)):
        return ("number", value)
    if isinstance(value, list):
        return ("array", tuple(_json_identity(item) for item in cast(list[Any], value)))
    if isinstance(value, dict):
        return (
            "object",
            frozenset(
                (key, _json_identity(item)) for key, item in cast(dict[str, Any], value).items()
            ),
        )
    raise ValueError("Non-JSON value in unique array")


def parse(
    data: Any, schema: dict[str, Any], *, operation: str = "response", path: str = "$"
) -> Any:
    def fail(expected: str) -> NoReturn:
        raise ResponseValidationError(
            f"{operation}: expected {expected} at {path}, got {type(data).__name__}"
        )

    if "allOf" in schema or "not" in schema:
        raise ResponseValidationError(f"{operation}: unsupported schema composition at {path}")
    if data is None and schema.get("nullable"):
        return None
    for combinator in ("oneOf", "anyOf"):
        if combinator in schema:
            matches: list[Any] = []
            for child in schema[combinator]:
                try:
                    matches.append(parse(data, child, operation=operation, path=path))
                except ResponseValidationError:
                    continue
            if not matches or (combinator == "oneOf" and len(matches) != 1):
                fail(combinator + " schema")
            return matches[0]
    if "$ref" in schema:
        name = schema["$ref"].rsplit("/", 1)[-1]
        target = SCHEMAS[name]
        if data is None or target.get("type") != "object" or not target.get("properties"):
            return parse(data, target, operation=operation, path=path)
        if not isinstance(data, dict):
            fail(name + " object")
        data = cast(dict[str, Any], data)
        required = target.get("required", [])
        for field in required:
            if field not in data:
                raise ResponseValidationError(
                    f"{operation}: missing required field at {path}.{field} ({name})"
                )
        values = {
            _name(k): parse(data[k], v, operation=operation, path=f"{path}.{k}")
            for k, v in target["properties"].items()
            if k in data
        }
        return getattr(models, name)(**values)
    if data is None:
        typ = schema.get("type")
        if typ is None or typ == "null" or (isinstance(typ, list) and "null" in typ):
            return None
        fail("non-null value")
    typ = schema.get("type")
    if isinstance(typ, list):
        typ = cast(list[str], typ)
        for choice in typ:
            if choice == "null":
                continue
            try:
                return parse(data, {**schema, "type": choice}, operation=operation, path=path)
            except ResponseValidationError:
                continue
        fail(" or ".join(str(choice) for choice in typ))
    if typ == "null":
        fail("null")
    if typ == "array":
        if not isinstance(data, list):
            fail("array")
        data = cast(list[Any], data)
        parsed_items = tuple(
            parse(item, schema.get("items", {}), operation=operation, path=f"{path}[{i}]")
            for i, item in enumerate(data)
        )
        if schema.get("uniqueItems"):
            try:
                identities = {_json_identity(item) for item in data}
            except (TypeError, ValueError):
                fail("JSON array with unique items")
            if len(identities) != len(data):
                fail("array with unique items")
        return parsed_items
    if typ == "object":
        if not isinstance(data, dict):
            fail("object")
        data = cast(dict[str, Any], data)
        properties: dict[str, Any] = schema.get("properties", {})
        for field in schema.get("required", []):
            if field not in data:
                raise ResponseValidationError(
                    f"{operation}: missing required field at {path}.{field}"
                )
        additional = schema.get("additionalProperties")
        result: dict[str, Any] = {}
        for key, value in data.items():
            field_schema = properties.get(key)
            if field_schema is None and isinstance(additional, dict):
                field_schema = cast(dict[str, Any], additional)
            result[key] = (
                parse(value, field_schema, operation=operation, path=f"{path}.{key}")
                if field_schema is not None
                else value
            )
        return result
    if typ == "integer":
        if isinstance(data, bool) or not isinstance(data, int):
            fail("integer")
    elif typ == "number":
        if (
            isinstance(data, bool)
            or not isinstance(data, (int, Decimal))
            or not Decimal(data).is_finite()
        ):
            fail("number")
        return Decimal(data)
    elif typ == "boolean":
        if not isinstance(data, bool):
            fail("boolean")
    elif typ == "string":
        if not isinstance(data, str):
            fail("string")
        if "minLength" in schema and len(data) < schema["minLength"]:
            fail(f"string of at least {schema['minLength']} characters")
        if "maxLength" in schema and len(data) > schema["maxLength"]:
            fail(f"string of at most {schema['maxLength']} characters")
        try:
            if schema.get("format") == "date-time":
                if not re.fullmatch(
                    r"\d{4}-\d{2}-\d{2}[Tt]\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:[Zz]|[+-]\d{2}:\d{2})",
                    data,
                ):
                    fail("RFC 3339 date-time")
                if data[-1] not in "Zz" and (int(data[-5:-3]) > 23 or int(data[-2:]) > 59):
                    fail("RFC 3339 date-time offset")
                return datetime.fromisoformat(data[:-1] + "+00:00" if data.endswith("z") else data)
            if schema.get("format") == "date":
                if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", data):
                    fail("date")
                return date.fromisoformat(data)
        except ValueError:
            fail(schema["format"])
    return data
