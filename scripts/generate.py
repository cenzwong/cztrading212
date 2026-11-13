"""Generate deterministic raw methods, dataclasses, and parser entry points."""

from __future__ import annotations

import builtins
import hashlib
import json
import keyword
import re
from pathlib import Path
from typing import Any

from normalize_schema import normalize

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "src/cztrading212/_generated"
SNAPSHOT = ROOT / "openapi/upstream.json"
OVERRIDES = ROOT / "openapi/overrides.json"
POLICIES = ROOT / "openapi/policies.json"

# Every operation must be reviewed before it gets a public method name.
NAMES = {
    "getAccountSummary": ("account", "get_account_summary"),
    "dividends": ("history", "get_dividends"),
    "getReports": ("history", "get_reports"),
    "requestReport": ("history", "request_report"),
    "orders_1": ("history", "get_orders"),
    "transactions": ("history", "get_transactions"),
    "exchanges": ("instruments", "get_exchanges"),
    "instruments": ("instruments", "get_instruments"),
    "orders": ("orders", "get_orders"),
    "placeLimitOrder": ("orders", "place_limit_order"),
    "placeMarketOrder": ("orders", "place_market_order"),
    "placeStopOrder_1": ("orders", "place_stop_order"),
    "placeStopOrder": ("orders", "place_stop_limit_order"),
    "cancelOrder": ("orders", "cancel_order"),
    "orderById": ("orders", "get_order"),
    "getAll": ("pies", "get_pies"),
    "create": ("pies", "create_pie"),
    "delete": ("pies", "delete_pie"),
    "getDetailed": ("pies", "get_pie"),
    "update": ("pies", "update_pie"),
    "duplicatePie": ("pies", "duplicate_pie"),
    "getPositions": ("positions", "get_positions"),
}


def snake(name: str) -> str:
    value = re.sub(r"\W", "_", re.sub(r"(?<!^)(?=[A-Z])", "_", name).lower())
    return value + "_" if keyword.iskeyword(value) else value


def validate_schema(schema: dict[str, Any], path: str, names: set[str]) -> None:
    # Fail closed on assertions the runtime parser does not implement. Metadata
    # such as descriptions and examples is intentionally not included here.
    unsupported = {
        "minimum",
        "maximum",
        "exclusiveMinimum",
        "exclusiveMaximum",
        "multipleOf",
        "pattern",
        "minItems",
        "maxItems",
        "minProperties",
        "maxProperties",
        "const",
        "contains",
        "minContains",
        "maxContains",
        "patternProperties",
        "propertyNames",
        "dependentRequired",
        "dependentSchemas",
        "if",
        "then",
        "else",
        "prefixItems",
        "unevaluatedProperties",
        "unevaluatedItems",
    }
    if found := sorted(unsupported.intersection(schema)):
        raise ValueError(f"Unsupported schema constraint {found[0]} at {path}; review parser")
    reference = schema.get("$ref")
    if reference is not None:
        prefix = "#/components/schemas/"
        if (
            not isinstance(reference, str)
            or not reference.startswith(prefix)
            or reference[len(prefix) :] not in names
        ):
            raise ValueError(f"Unsupported or missing schema reference at {path}")
    if "allOf" in schema or "not" in schema:
        raise ValueError(f"Unsupported schema composition at {path}; review generator and parser")
    typ = schema.get("type")
    allowed = {"null", "object", "array", "number", "integer", "boolean", "string"}
    if typ is not None:
        choices = typ if isinstance(typ, list) else [typ]
        if not choices or any(
            not isinstance(choice, str) or choice not in allowed for choice in choices
        ):
            raise ValueError(f"Unsupported schema type at {path}")
    for key in ("oneOf", "anyOf"):
        for index, child in enumerate(schema.get(key, [])):
            validate_schema(child, f"{path}.{key}[{index}]", names)
    for name, child in schema.get("properties", {}).items():
        validate_schema(child, f"{path}.{name}", names)
    for key in ("items", "additionalProperties"):
        child = schema.get(key)
        if isinstance(child, dict):
            validate_schema(child, f"{path}.{key}", names)


def annotation(schema: dict[str, Any], spec: dict[str, Any], *, prefix: str = "") -> str:
    result = _annotation(schema, spec, prefix=prefix)
    if schema.get("nullable") and "None" not in result.split(" | "):
        return result + " | None"
    return result


def _annotation(schema: dict[str, Any], spec: dict[str, Any], *, prefix: str = "") -> str:
    if "$ref" in schema:
        ref = schema["$ref"].rsplit("/", 1)[-1]
        target = spec["components"]["schemas"][ref]
        return (
            prefix + ref
            if target.get("type") == "object" and target.get("properties")
            else annotation(target, spec, prefix=prefix)
        )
    for key in ("anyOf", "oneOf"):
        if key in schema:
            return " | ".join(
                dict.fromkeys(annotation(item, spec, prefix=prefix) for item in schema[key])
            )
    typ = schema.get("type")
    if isinstance(typ, list):
        return " | ".join(
            dict.fromkeys(annotation({**schema, "type": item}, spec, prefix=prefix) for item in typ)
        )
    if typ == "null":
        return "None"
    if typ == "array":
        return f"tuple[{annotation(schema.get('items', {}), spec, prefix=prefix)}, ...]"
    if typ == "object":
        return (
            f"dict[str, {annotation(schema.get('additionalProperties', {}), spec, prefix=prefix)}]"
        )
    if typ == "number":
        return "Decimal"
    if typ == "integer":
        return "int"
    if typ == "boolean":
        return "bool"
    if typ == "string":
        return {"date-time": "datetime", "date": "date"}.get(schema.get("format"), "str")
    return "Any"


def _rate_policy(operation_id: str, operation: dict[str, Any]) -> dict[str, int]:
    match = re.search(
        r"\*\*Rate limit:\*\* (\d+) req / (?:(\d+)m)?(\d+)s", operation.get("description", "")
    )
    if not match:
        raise ValueError(f"Rate limit requires review for {operation_id}")
    requests, minutes, seconds = match.groups()
    count, period = int(requests), int(minutes or 0) * 60 + int(seconds)
    if count < 1 or period < 1:
        raise ValueError(f"Invalid rate limit for {operation_id}")
    return {"requests": count, "period_seconds": period}


def _parameter_type(schema: dict[str, Any]) -> str:
    return {"integer": "int", "number": "Decimal", "boolean": "bool", "string": "str"}.get(
        schema.get("type"), "Any"
    )


def json_text(value: Any) -> str:
    return json.dumps(value, indent=2, sort_keys=True, ensure_ascii=False) + "\n"


def build(spec: dict[str, Any], policies: dict[str, Any]) -> dict[str, str]:
    schemas = spec["components"]["schemas"]
    schema_names = set(schemas)
    for name, schema in schemas.items():
        if (
            not name.isidentifier()
            or keyword.iskeyword(name)
            or name.startswith("_")
            or name in vars(builtins)
            or name in {"Any", "Decimal", "date", "datetime"}
        ):
            raise ValueError("Invalid generated schema name")
        if not set(schema.get("required", [])) <= set(schema.get("properties", {})):
            raise ValueError(f"Required fields are missing from {name} properties")
        property_names = [snake(field) for field in schema.get("properties", {})]
        if len(set(property_names)) != len(property_names) or any(
            not field.isidentifier() or field.startswith("_") for field in property_names
        ):
            raise ValueError(f"Invalid or colliding generated field name in {name}")
        validate_schema(schema, f"components.schemas.{name}", schema_names)
    model_lines = [
        "# Generated by scripts/generate.py; do not edit.",
        "from __future__ import annotations",
        "from dataclasses import dataclass",
        "from datetime import date, datetime",
        "from decimal import Decimal",
        "from typing import Any",
        "",
    ]
    for name, schema in sorted(schemas.items()):
        model_lines += ["@dataclass(frozen=True, slots=True, kw_only=True)", f"class {name}:"]
        properties = schema.get("properties", {})
        if not properties:
            model_lines.append("    pass")
        for key, value in properties.items():
            field_type = annotation(value, spec)
            required = key in schema.get("required", [])
            nullable = value.get("nullable") or "None" in field_type.split(" | ")
            if (not required or nullable) and "None" not in field_type.split(" | "):
                field_type += " | None"
            model_lines.append(f"    {snake(key)}: {field_type}{'' if required else ' = None'}")
        model_lines.append("")
    operations: list[dict[str, Any]] = []
    for path, methods in sorted(spec["paths"].items()):
        for method, operation in sorted(methods.items()):
            if method not in {"get", "post", "put", "patch", "delete"}:
                continue
            if not re.fullmatch(r"/api/v0/[A-Za-z0-9._~/{}/-]+", path):
                raise ValueError("Unsafe or unsupported operation path")
            operation_id = operation["operationId"]
            success_codes = {
                code for code in operation.get("responses", {}) if code.startswith("2")
            }
            if success_codes - {"200"}:
                raise ValueError(
                    f"Success response status requires parser review for {operation_id}"
                )
            operations.append(
                {
                    "operation_id": operation_id,
                    "method": method.upper(),
                    "path": path,
                    "group": operation.get("tags", ["other"])[0],
                    "parameters": operation.get("parameters", []),
                    "request_body": operation.get("requestBody"),
                    "responses": operation.get("responses", {}),
                    "deprecated": operation.get("deprecated", False),
                    "authentication": operation.get("security", spec.get("security", [])),
                    "documentation_url": "https://docs.trading212.com/api",
                    "request_schema": operation.get("requestBody", {})
                    .get("content", {})
                    .get("application/json", {})
                    .get("schema"),
                    "response_schema": operation.get("responses", {})
                    .get("200", {})
                    .get("content", {})
                    .get("application/json", {})
                    .get("schema"),
                    "rate_limit_policy": _rate_policy(operation_id, operation),
                }
            )
            for response in operation.get("responses", {}).values():
                for media in response.get("content", {}).values():
                    if "schema" in media:
                        validate_schema(media["schema"], f"{operation_id}.response", schema_names)
            for media in operation.get("requestBody", {}).get("content", {}).values():
                if "schema" in media:
                    validate_schema(media["schema"], f"{operation_id}.request", schema_names)
    if set(NAMES) != {op["operation_id"] for op in operations} or len(operations) != len(NAMES):
        raise ValueError("Operation mapping must be reviewed for the updated schema")
    if set(policies) != set(NAMES):
        raise ValueError("Operation policy metadata must be reviewed")
    for op in operations:
        opid = op["operation_id"]
        policy = policies[opid]
        if policy.get("retry") not in {"safe_read", "never"} or (
            op["method"] != "GET" and policy.get("retry") != "never"
        ):
            raise ValueError(f"Unsafe retry policy for {opid}")
        expected_risk = "read_only" if op["method"] == "GET" else "write"
        if policy.get("mutation_risk") != expected_risk:
            raise ValueError(f"Mutation risk requires review for {opid}")
        ttl = policy.get("cache_ttl_seconds")
        if ttl is not None and (type(ttl) is not int or ttl <= 0 or op["method"] != "GET"):
            raise ValueError(f"Unsafe cache policy for {opid}")
        op["raw_group"], op["raw_method"] = NAMES[opid]
        op["retry_policy"] = policy["retry"]
        op["cache_policy"] = {"ttl_seconds": ttl} if ttl is not None else None
        op["mutation_risk"] = expected_risk
    operation_lines = [
        "# Generated by scripts/generate.py; do not edit.",
        "from __future__ import annotations",
        "from decimal import Decimal",
        "from typing import Any, TypeAlias",
        "",
        'JSONValue: TypeAlias = "None | bool | int | str | Decimal | list[JSONValue] | dict[str, JSONValue]"',
        "",
    ]
    by_id = {op["operation_id"]: op for op in operations}
    for asynchronous in (False, True):
        prefix = "Async" if asynchronous else ""
        for group in sorted({group for group, _ in NAMES.values()}):
            operation_lines += [
                f"class {prefix}{group.title()}:",
                "    def __init__(self, raw: Any) -> None:",
                "        self._raw = raw",
                "",
            ]
            for opid, (opgroup, method) in NAMES.items():
                if opgroup != group:
                    continue
                op = by_id[opid]
                parameters: list[str] = []
                mappings: list[str] = []
                for param in op["parameters"]:
                    name = snake(param["name"])
                    if not name.isidentifier() or name.startswith("_") or name in {"self", "body"}:
                        raise ValueError(f"Unsafe parameter name for {opid}")
                    required = param.get("required", False)
                    param_type = _parameter_type(param.get("schema", {}))
                    parameters.append(
                        f"{name}: {param_type}" + ("" if required else " | None = None")
                    )
                    mappings.append(f"{param['name']!r}: {name}")
                if op["request_body"]:
                    required = op["request_body"].get("required", False)
                    parameters.append(
                        "body: dict[str, Any]" + ("" if required else " | None = None")
                    )
                    mappings.append("'body': body")
                signature = ", *, " + ", ".join(parameters) if parameters else ""
                arguments = ", **{" + ", ".join(mappings) + "}" if mappings else ""
                description = op["method"] + " " + op["path"] + " (one direct request)."
                operation_lines += [
                    f"    {'async ' if asynchronous else ''}def {method}(self{signature}) -> JSONValue:",
                    f"        {description!r}",
                    f"        return {'await ' if asynchronous else ''}self._raw.call({opid!r}{arguments})",
                    "",
                ]
    parser_lines = [
        "# Generated by scripts/generate.py; do not edit.",
        "from __future__ import annotations",
        "from datetime import date, datetime",
        "from decimal import Decimal",
        "from typing import Any, cast",
        "from . import models",
        "from ..parsing import parse",
        "",
    ]
    for name, schema in sorted(schemas.items()):
        return_type = annotation({"$ref": f"#/components/schemas/{name}"}, spec, prefix="models.")
        parser_lines += [
            f"def parse_{snake(name)}(data: Any, *, operation: str = {name!r}) -> {return_type}:",
            f"    return cast({return_type}, parse(data, {{'$ref': '#/components/schemas/{name}'}}, operation=operation))",
            "",
        ]
    for op in operations:
        opid = op["operation_id"]
        schema = (
            op["responses"]
            .get("200", {})
            .get("content", {})
            .get("application/json", {})
            .get("schema")
        )
        if schema:
            return_type = annotation(schema, spec, prefix="models.")
            parser_lines += [
                f"def parse_operation_{snake(opid)}(data: Any) -> {return_type}:",
                f"    return cast({return_type}, parse(data, {schema!r}, operation={opid!r}))",
                "",
            ]
    parser_lines.append("OPERATION_PARSERS = {")
    for op in operations:
        opid = op["operation_id"]
        schema = (
            op["responses"]
            .get("200", {})
            .get("content", {})
            .get("application/json", {})
            .get("schema")
        )
        if schema:
            parser_lines.append(f"    {opid!r}: parse_operation_{snake(opid)},")
    parser_lines += ["}", ""]
    normalized = json_text(spec)
    manifest = {
        "upstream_sha256": hashlib.sha256(SNAPSHOT.read_bytes()).hexdigest(),
        "overrides_sha256": hashlib.sha256(OVERRIDES.read_bytes()).hexdigest(),
        "normalized_sha256": hashlib.sha256(normalized.encode()).hexdigest(),
        "policies_sha256": hashlib.sha256(POLICIES.read_bytes()).hexdigest(),
        "operations": len(operations),
        "schemas": len(schemas),
    }
    return {
        "models.py": "\n".join(model_lines).rstrip() + "\n",
        "operations.py": "\n".join(operation_lines).rstrip() + "\n",
        "parsers.py": "\n".join(parser_lines).rstrip() + "\n",
        "operations.json": json_text(operations),
        "schemas.json": json_text(schemas),
        "manifest.json": json_text(manifest),
        "__init__.py": '"""Generated API metadata and models."""\n',
    }


def main() -> None:
    spec = normalize(json.loads(SNAPSHOT.read_text()), json.loads(OVERRIDES.read_text()))
    # Validate everything before changing generated files, including a changed operation map.
    outputs = build(spec, json.loads(POLICIES.read_text()))
    OUT.mkdir(parents=True, exist_ok=True)
    (ROOT / "openapi/normalized.json").write_text(json_text(spec))
    for name, content in outputs.items():
        (OUT / name).write_text(content)


if __name__ == "__main__":
    main()
