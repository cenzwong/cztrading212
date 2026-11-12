"""Render the human-readable operation reference from the generated catalog."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
CATALOG = ROOT / "src/cztrading212/_generated/operations.json"
REFERENCE = ROOT / "docs/api-reference.md"


def schema_name(schema: dict[str, Any] | None) -> str:
    if not schema:
        return "—"
    if "$ref" in schema:
        return schema["$ref"].rsplit("/", 1)[-1]
    if schema.get("type") == "array":
        return f"list[{schema_name(schema.get('items'))}]"
    return schema.get("type", "object")


def render(operations: list[dict[str, Any]]) -> str:
    lines = [
        "# Raw API reference",
        "",
        "This index is generated from the checked-in operation catalog. Run",
        "`python scripts/generate_reference.py` after regenerating the API.",
        "Names are relative to `client.raw`; async clients use the same names.",
        "An asterisk marks a required argument. Bodies use official JSON keys.",
        "Rates are from the snapshot and can change upstream. Deprecated operations",
        "are retained because they appear in that snapshot.",
        "",
        "| Operation ID | Raw method | HTTP endpoint | Arguments | Request body | 200 response | Rate |",
        "| --- | --- | --- | --- | --- | --- | --- |",
    ]
    for op in operations:
        params = [
            f"`{param['name']}`{'*' if param.get('required') else ''}" for param in op["parameters"]
        ]
        if op["request_body"]:
            params.append(f"`body`{'*' if op['request_body'].get('required') else ''}")
        rate = op["rate_limit_policy"]
        operation = f"`{op['operation_id']}`" + (" (deprecated)" if op["deprecated"] else "")
        method = f"`{op['raw_group']}.{op['raw_method']}`"
        endpoint = f"`{op['method']} {op['path']}`"
        body = schema_name(op["request_schema"])
        response = schema_name(op["response_schema"])
        lines.append(
            f"| {operation} | {method} | {endpoint} | {', '.join(params) or '—'} | "
            f"{body} | {response} | {rate['requests']} / {rate['period_seconds']}s |"
        )
    lines += [
        "",
        "The [machine-readable catalog](../src/cztrading212/_generated/operations.json)",
        "contains complete parameter and response metadata. The corresponding",
        "[schema definitions](../src/cztrading212/_generated/schemas.json) and",
        "[generated dataclasses](../src/cztrading212/_generated/models.py) describe",
        "fields; the upstream contract remains the authority for request semantics.",
        "Raw write methods can affect live accounts and bypass service safeguards.",
        "See [order safety](safety.md), [caching](caching.md), and",
        "[rate limiting](reliability.md) before using them.",
        "",
    ]
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="Fail if the reference is stale")
    args = parser.parse_args()
    result = render(json.loads(CATALOG.read_text()))
    if args.check:
        if not REFERENCE.exists() or REFERENCE.read_text() != result:
            raise SystemExit("API reference is stale; run python scripts/generate_reference.py")
    else:
        REFERENCE.write_text(result)


if __name__ == "__main__":
    main()
