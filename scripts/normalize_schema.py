"""Normalize the committed OpenAPI snapshot with explicit documented overrides."""

from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]


def normalize(spec: dict[str, Any], overrides: dict[str, Any]) -> dict[str, Any]:
    """Return a normalized copy without modifying its input."""
    if not str(spec.get("openapi", "")).startswith("3."):
        raise ValueError("Only OpenAPI 3 snapshots are supported")
    normalized = copy.deepcopy(spec)
    rationale = overrides.get("rationale", {}).get("nullable_fields", {})
    if not rationale.get("source") or not rationale.get("reason"):
        raise ValueError("Nullable overrides require a reason and documented source")
    for schema_name, fields in overrides.get("nullable_fields", {}).items():
        for field in fields:
            try:
                property_schema = normalized["components"]["schemas"][schema_name]["properties"][
                    field
                ]
            except KeyError as exc:
                raise ValueError(f"Override target missing: {schema_name}.{field}") from exc
            property_schema["nullable"] = True
    security = overrides.get("security", {})
    if not security.get("reason") or not security.get("source"):
        raise ValueError("Security override requires a reason and documented source")
    for methods in normalized.get("paths", {}).values():
        for method, operation in methods.items():
            if method not in {"get", "post", "put", "patch", "delete"}:
                continue
            if operation.get("security") != security.get("expected"):
                raise ValueError("Upstream security changed; review the authentication override")
            operation["security"] = copy.deepcopy(security["normalized"])
    return normalized


def main() -> None:
    upstream = json.loads((ROOT / "openapi/upstream.json").read_text())
    overrides = json.loads((ROOT / "openapi/overrides.json").read_text())
    normalized = normalize(upstream, overrides)
    (ROOT / "openapi/normalized.json").write_text(
        json.dumps(normalized, indent=2, sort_keys=True, ensure_ascii=False) + "\n"
    )


if __name__ == "__main__":
    main()
