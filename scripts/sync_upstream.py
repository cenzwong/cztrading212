"""Download, compare, and optionally stage the official upstream API snapshot."""

from __future__ import annotations

import argparse
import json
import re
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
SNAPSHOT = ROOT / "openapi/upstream.json"
REPORT = ROOT / "openapi/UPSTREAM_CHANGE_REPORT.md"
UPSTREAM_URL = "https://docs.trading212.com/_bundle/api.json?download="
MAX_BYTES = 5_000_000
METHODS = {"get", "post", "put", "patch", "delete"}


@dataclass(frozen=True, slots=True)
class Change:
    severity: str
    location: str
    description: str


def _operations(spec: dict[str, Any]) -> dict[tuple[str, str], dict[str, Any]]:
    return {
        (method.upper(), path): op
        for path, methods in spec.get("paths", {}).items()
        for method, op in methods.items()
        if method in METHODS
    }


def _nullable(schema: dict[str, Any]) -> bool:
    typ = schema.get("type")
    return bool(schema.get("nullable")) or (isinstance(typ, list) and "null" in typ)


def _schema_changes(
    old: dict[str, Any], new: dict[str, Any], location: str, changes: list[Change]
) -> None:
    old_type, new_type = old.get("type"), new.get("type")

    def without_null(value: Any) -> Any:
        if isinstance(value, list):
            values = sorted(item for item in value if item != "null")
            return values[0] if len(values) == 1 else values
        return value

    if without_null(old_type) != without_null(new_type) or old.get("$ref") != new.get("$ref"):
        changes.append(Change("breaking", location, "Type or reference changed"))
    if _nullable(old) != _nullable(new):
        changes.append(
            Change(
                "compatible" if _nullable(new) else "breaking",
                location,
                "Nullability added" if _nullable(new) else "Nullability removed",
            )
        )
    old_required = set(old.get("required", []))
    new_required = set(new.get("required", []))
    for name in sorted(new_required - old_required):
        changes.append(Change("breaking", f"{location}.{name}", "Field became required"))
    for name in sorted(old_required - new_required):
        changes.append(Change("review", f"{location}.{name}", "Field is no longer required"))
    old_enum, new_enum = old.get("enum"), new.get("enum")
    if old_enum is not None and new_enum is not None:
        old_values, new_values = set(map(str, old_enum)), set(map(str, new_enum))
        if new_values - old_values:
            changes.append(
                Change(
                    "compatible", location, "Enum values added; clients should tolerate additions"
                )
            )
        if old_values - new_values:
            changes.append(Change("breaking", location, "Enum values removed"))
    elif old_enum != new_enum:
        changes.append(Change("review", location, "Enum restriction changed"))
    old_props, new_props = old.get("properties", {}), new.get("properties", {})
    for name in sorted(old_props.keys() - new_props.keys()):
        changes.append(Change("breaking", f"{location}.{name}", "Field removed"))
    for name in sorted(new_props.keys() - old_props.keys()):
        changes.append(
            Change(
                "breaking" if name in new_required else "compatible",
                f"{location}.{name}",
                "Field added",
            )
        )
    for name in sorted(old_props.keys() & new_props.keys()):
        _schema_changes(old_props[name], new_props[name], f"{location}.{name}", changes)
    for key in ("items", "additionalProperties"):
        before, after = old.get(key), new.get(key)
        if isinstance(before, dict) and isinstance(after, dict):
            _schema_changes(before, after, f"{location}.{key}", changes)
        elif before != after:
            changes.append(Change("review", f"{location}.{key}", "Collection schema changed"))
    ignored = {
        "description",
        "example",
        "examples",
        "title",
        "externalDocs",
        "type",
        "$ref",
        "nullable",
        "required",
        "enum",
        "properties",
        "items",
        "additionalProperties",
    }
    old_other = {key: value for key, value in old.items() if key not in ignored}
    new_other = {key: value for key, value in new.items() if key not in ignored}
    if old_other != new_other:
        changes.append(
            Change("review", location, "Other schema constraints or composition changed")
        )


def _references(value: Any, schemas: dict[str, Any], seen: set[str] | None = None) -> set[str]:
    if seen is None:
        seen = set()
    if isinstance(value, dict):
        reference = value.get("$ref")
        if isinstance(reference, str) and reference.startswith("#/components/schemas/"):
            name = reference.rsplit("/", 1)[-1]
            if name not in seen:
                seen.add(name)
                if name in schemas:
                    _references(schemas[name], schemas, seen)
        for item in value.values():
            _references(item, schemas, seen)
    elif isinstance(value, list):
        for item in value:
            _references(item, schemas, seen)
    return seen


def _rate_description(operation: dict[str, Any]) -> tuple[str, ...]:
    return tuple(re.findall(r"\*\*Rate limit:\*\*[^\n]*", operation.get("description", "")))


def compare(old: dict[str, Any], new: dict[str, Any]) -> list[Change]:
    changes: list[Change] = []
    old_ops, new_ops = _operations(old), _operations(new)
    old_schemas = old.get("components", {}).get("schemas", {})
    new_schemas = new.get("components", {}).get("schemas", {})
    for method, path in sorted(old_ops.keys() - new_ops.keys()):
        changes.append(Change("breaking", f"{method} {path}", "Operation removed"))
        if method != "GET":
            changes.append(
                Change(
                    "high-risk",
                    f"{method} {path}",
                    "Mutation operation removed; review safety impact",
                )
            )
    for method, path in sorted(new_ops.keys() - old_ops.keys()):
        changes.append(
            Change("compatible", f"{method} {path}", "Operation added; review naming and policy")
        )
        if method != "GET":
            changes.append(
                Change(
                    "high-risk", f"{method} {path}", "New mutation operation; review safety policy"
                )
            )
    for method, path in sorted(old_ops.keys() & new_ops.keys()):
        before, after = old_ops[(method, path)], new_ops[(method, path)]
        location = f"{method} {path}"
        if before.get("operationId") != after.get("operationId"):
            changes.append(Change("breaking", location, "Operation ID changed"))
        if before.get("parameters", []) != after.get("parameters", []):
            changes.append(Change("review", location, "Operation parameters or signature changed"))
        if before.get("requestBody") != after.get("requestBody"):
            changes.append(Change("review", location, "Request body schema or requirement changed"))
        if before.get("responses") != after.get("responses"):
            changes.append(Change("review", location, "Response definitions changed"))
        if _rate_description(before) != _rate_description(after):
            changes.append(Change("review", location, "Documented rate limit changed"))
        references = _references(before, old_schemas) | _references(after, new_schemas)
        changed_references = any(old_schemas.get(ref) != new_schemas.get(ref) for ref in references)
        if method != "GET" and changed_references:
            changes.append(
                Change(
                    "high-risk",
                    location,
                    "Referenced mutation schema changed; review safety policy",
                )
            )
        if bool(before.get("deprecated")) != bool(after.get("deprecated")):
            changes.append(Change("review", location, "Deprecation status changed"))
        if method != "GET" and before != after:
            changes.append(
                Change("high-risk", location, "Mutation operation changed; review safety policy")
            )
    for name in sorted(old_schemas.keys() - new_schemas.keys()):
        changes.append(Change("breaking", name, "Schema removed"))
    for name in sorted(new_schemas.keys() - old_schemas.keys()):
        changes.append(Change("compatible", name, "Schema added"))
    for name in sorted(old_schemas.keys() & new_schemas.keys()):
        _schema_changes(old_schemas[name], new_schemas[name], name, changes)
    if old != new and not changes:
        changes.append(
            Change("review", "OpenAPI document", "Other metadata or documentation changed")
        )
    return changes


def render_report(changes: list[Change]) -> str:
    lines = [
        "# Upstream API change report",
        "",
        "Generated comparison; classifications are conservative hints, not proof of compatibility. Review the full OpenAPI diff, rate policies, and all mutation changes before merging.",
        "",
    ]
    if not changes:
        lines.append("No changes detected.")
    for change in changes:
        location = change.location.replace("`", "'").replace("\n", " ").replace("\r", " ")
        lines.append(f"- **{change.severity}** `{location}`: {change.description}")
    return "\n".join(lines) + "\n"


def download() -> bytes:
    request = urllib.request.Request(
        UPSTREAM_URL, headers={"User-Agent": "cztrading212-upstream-sync/1"}
    )
    with urllib.request.urlopen(request, timeout=30) as response:
        payload = response.read(MAX_BYTES + 1)
    if len(payload) > MAX_BYTES:
        raise ValueError("Upstream specification exceeds maximum size")
    return payload


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--candidate", type=Path, help="Compare an existing JSON file without downloading"
    )
    parser.add_argument(
        "--write", action="store_true", help="Stage the validated snapshot and change report"
    )
    args = parser.parse_args()
    payload = args.candidate.read_bytes() if args.candidate else download()
    if len(payload) > MAX_BYTES:
        raise ValueError("Upstream specification exceeds maximum size")
    candidate = json.loads(payload)
    if (
        not isinstance(candidate, dict)
        or not str(candidate.get("openapi", "")).startswith("3.")
        or not isinstance(candidate.get("paths"), dict)
        or not isinstance(candidate.get("components", {}).get("schemas"), dict)
    ):
        raise ValueError("Candidate is not a supported OpenAPI document")
    previous = json.loads(SNAPSHOT.read_text())
    changes = compare(previous, candidate)
    report = render_report(changes)
    print(report, end="")
    if args.write and previous != candidate:
        # Snapshot upstream as supplied; normalization and generation are separate steps.
        SNAPSHOT.write_bytes(payload)
        REPORT.write_text(report)


if __name__ == "__main__":
    main()
