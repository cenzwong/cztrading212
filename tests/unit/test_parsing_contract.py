import dataclasses
import importlib
import json
import re
import shutil
import subprocess
import sys
from datetime import datetime
from decimal import Decimal
from pathlib import Path

import pytest

from cztrading212 import ResponseValidationError
from cztrading212._generated import models, parsers
from cztrading212.parsing import SCHEMAS, parse

ROOT = Path(__file__).resolve().parents[2]


def snake(name):
    return re.sub(r"\W", "_", re.sub(r"(?<!^)(?=[A-Z])", "_", name).lower())


def example(schema):
    if "$ref" in schema:
        return example(SCHEMAS[schema["$ref"].rsplit("/", 1)[-1]])
    typ = schema.get("type")
    if isinstance(typ, list):
        return example({**schema, "type": next(t for t in typ if t != "null")})
    if typ == "object":
        if "properties" in schema:
            return {name: example(prop) for name, prop in schema["properties"].items()}
        return {"key": example(schema.get("additionalProperties", {}))}
    if typ == "array":
        return [example(schema["items"])]
    if typ == "number":
        return Decimal("0.123456789012345678901")
    if typ == "integer":
        return 7
    if typ == "boolean":
        return True
    if typ == "string":
        if "maxLength" in schema:
            return "X" * schema.get("minLength", 0)
        return {"date-time": "2026-01-02T03:04:05Z", "date": "2026-01-02"}.get(
            schema.get("format"), "future-enum-value"
        )
    return "unconstrained"


@pytest.mark.parametrize("name,schema", sorted(SCHEMAS.items()))
def test_generated_model_fields_and_parsers_match_snapshot(name, schema):
    parser = getattr(parsers, "parse_" + snake(name))
    value = parser(example(schema))
    if schema.get("type") == "object" and schema.get("properties"):
        assert dataclasses.is_dataclass(value)
        assert isinstance(value, getattr(models, name))
        assert {field.name for field in dataclasses.fields(value)} == {
            snake(k) for k in schema["properties"]
        }
        with pytest.raises(ResponseValidationError):
            parser([])
    elif schema.get("type") == "string":
        assert isinstance(value, str)


def test_inline_object_required_optional_unknown_and_null():
    schema = {
        "type": "object",
        "required": ["amount"],
        "properties": {"amount": {"type": "number"}, "nullable": {"type": ["string", "null"]}},
    }
    assert parse(
        {"amount": Decimal("0.00000000000001"), "nullable": None, "newField": True}, schema
    ) == {"amount": Decimal("0.00000000000001"), "nullable": None, "newField": True}
    with pytest.raises(ResponseValidationError, match=r"missing required field at \$\.amount"):
        parse({}, schema, operation="example")
    with pytest.raises(ResponseValidationError, match=r"\$\.amount"):
        parse({"amount": None}, schema)
    with pytest.raises(ResponseValidationError, match=r"\$\.amount"):
        parse({"amount": True}, schema)


def test_snapshot_currency_length_and_unique_issues_are_enforced():
    assert parsers.parse_tradable_instrument({"currencyCode": "GBP"}).currency_code == "GBP"
    for invalid in ("GB", "GBPX"):
        with pytest.raises(ResponseValidationError, match=r"currencyCode"):
            parsers.parse_tradable_instrument({"currencyCode": invalid})
    issue = {"name": "SUSPENDED", "severity": "INFORMATIVE"}
    with pytest.raises(ResponseValidationError, match="unique items"):
        parsers.parse_account_bucket_instrument_result({"issues": [issue, dict(issue)]})
    result = parsers.parse_account_bucket_instrument_result(
        {"issues": [issue, {**issue, "newField": "distinct"}]}
    )
    assert len(result.issues) == 2


def test_unique_items_follow_json_equality():
    schema = {"type": "array", "items": {}, "uniqueItems": True}
    assert parse([True, 1], schema) == (True, 1)
    with pytest.raises(ResponseValidationError, match="unique items"):
        parse([{"a": 1, "b": [2]}, {"b": [Decimal("2.0")], "a": Decimal("1.0")}], schema)


def test_unions_are_validated_and_one_of_rejects_ambiguity():
    assert parse(None, {"anyOf": [{"type": "null"}, {"type": "integer"}]}) is None
    assert parse(5, {"anyOf": [{"type": "integer"}, {"type": "string"}]}) == 5
    with pytest.raises(ResponseValidationError, match="oneOf"):
        parse(5, {"oneOf": [{"type": "integer"}, {"type": "number"}]})
    with pytest.raises(ResponseValidationError, match="unsupported"):
        parse({}, {"allOf": [{"type": "object"}]})


def test_nullable_array_items_and_union_are_validated():
    schema = {
        "type": "array",
        "items": {"nullable": True, "oneOf": [{"type": "integer"}, {"type": "string"}]},
    }
    assert parse([None, 2, "three"], schema) == (None, 2, "three")
    with pytest.raises(ResponseValidationError):
        parse([False], schema)


def test_generator_preserves_nullable_nested_types():
    sys.path.insert(0, str(ROOT / "scripts"))
    try:
        generator = importlib.import_module("generate")
        spec = {"components": {"schemas": {}}}
        schema = {"type": "array", "items": {"type": "integer", "nullable": True}}
        assert generator.annotation(schema, spec) == "tuple[int | None, ...]"
        union = {"nullable": True, "oneOf": [{"type": "integer"}, {"type": "string"}]}
        assert generator.annotation(union, spec) == "int | str | None"
    finally:
        sys.path.pop(0)


@pytest.mark.parametrize("constraint,value", [("minimum", 0), ("pattern", "^A"), ("maxItems", 3)])
def test_generator_rejects_unimplemented_constraints_before_writing(tmp_path, constraint, value):
    for folder in ("scripts", "openapi", "src/cztrading212/_generated"):
        (tmp_path / folder).mkdir(parents=True)
    for filename in ("generate.py", "normalize_schema.py"):
        shutil.copy(ROOT / "scripts" / filename, tmp_path / "scripts" / filename)
    for filename in ("overrides.json", "policies.json"):
        shutil.copy(ROOT / "openapi" / filename, tmp_path / "openapi" / filename)
    upstream = json.loads((ROOT / "openapi/upstream.json").read_text())
    upstream["components"]["schemas"]["AccountSummary"]["properties"]["currency"][constraint] = (
        value
    )
    (tmp_path / "openapi/upstream.json").write_text(json.dumps(upstream))
    sentinel = tmp_path / "src/cztrading212/_generated/models.py"
    sentinel.write_text("preserve me")
    result = subprocess.run(
        [sys.executable, str(tmp_path / "scripts/generate.py")],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode != 0 and f"Unsupported schema constraint {constraint}" in result.stderr
    assert sentinel.read_text() == "preserve me"


@pytest.mark.parametrize(
    "timestamp", ["2026-01-01", "2026-01-01T00:00:00", "20260101T000000Z", "not-a-date"]
)
def test_datetime_rejects_non_rfc3339(timestamp):
    with pytest.raises(ResponseValidationError, match="date-time"):
        parse(timestamp, {"type": "string", "format": "date-time"})


def test_datetime_accepts_timezone_offsets_and_lowercase_z():
    assert isinstance(
        parse("2026-01-01T01:02:03+01:00", {"type": "string", "format": "date-time"}), datetime
    )
    assert (
        parse("2026-01-01t01:02:03z", {"type": "string", "format": "date-time"})
        .utcoffset()
        .total_seconds()
        == 0
    )


def test_normalized_snapshot_and_generated_manifest_are_consistent():
    snapshot = json.loads((ROOT / "openapi/normalized.json").read_text())
    assert snapshot["components"]["schemas"] == SCHEMAS
    module = importlib.import_module("cztrading212._generated.parsers")
    generated = json.loads((ROOT / "src/cztrading212/_generated/operations.json").read_text())
    expected = {
        op["operation_id"]
        for op in generated
        if op["responses"]
        .get("200", {})
        .get("content", {})
        .get("application/json", {})
        .get("schema")
    }
    assert set(module.OPERATION_PARSERS) == expected


def test_unreviewed_operation_fails_without_touching_generated_files(tmp_path):
    for folder in ("scripts", "openapi", "src/cztrading212/_generated"):
        (tmp_path / folder).mkdir(parents=True)
    for filename in ("generate.py", "normalize_schema.py"):
        shutil.copy(ROOT / "scripts" / filename, tmp_path / "scripts" / filename)
    for filename in ("overrides.json", "policies.json"):
        shutil.copy(ROOT / "openapi" / filename, tmp_path / "openapi" / filename)
    upstream = json.loads((ROOT / "openapi/upstream.json").read_text())
    upstream["paths"]["/api/v0/unreviewed"] = {
        "get": {"operationId": "unreviewed", "responses": {}}
    }
    (tmp_path / "openapi/upstream.json").write_text(json.dumps(upstream))
    sentinel = tmp_path / "src/cztrading212/_generated/models.py"
    sentinel.write_text("preserve me")
    result = subprocess.run(
        [sys.executable, str(tmp_path / "scripts/generate.py")],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode != 0 and "reviewed" in result.stderr
    assert sentinel.read_text() == "preserve me"
    assert not (tmp_path / "openapi/normalized.json").exists()


def test_new_success_status_requires_review_without_touching_generated_files(tmp_path):
    for folder in ("scripts", "openapi", "src/cztrading212/_generated"):
        (tmp_path / folder).mkdir(parents=True)
    for filename in ("generate.py", "normalize_schema.py"):
        shutil.copy(ROOT / "scripts" / filename, tmp_path / "scripts" / filename)
    for filename in ("overrides.json", "policies.json"):
        shutil.copy(ROOT / "openapi" / filename, tmp_path / "openapi" / filename)
    upstream = json.loads((ROOT / "openapi/upstream.json").read_text())
    upstream["paths"]["/api/v0/equity/account/summary"]["get"]["responses"]["201"] = {
        "description": "New success response"
    }
    (tmp_path / "openapi/upstream.json").write_text(json.dumps(upstream))
    sentinel = tmp_path / "src/cztrading212/_generated/models.py"
    sentinel.write_text("preserve me")
    result = subprocess.run(
        [sys.executable, str(tmp_path / "scripts/generate.py")],
        capture_output=True,
        text=True,
        check=False,
    )
    assert (
        result.returncode != 0 and "Success response status requires parser review" in result.stderr
    )
    assert sentinel.read_text() == "preserve me"


def test_required_fields_and_unsafe_generated_names_are_checked_before_writes(tmp_path):
    import ast

    for folder in ("scripts", "openapi", "src/cztrading212/_generated"):
        (tmp_path / folder).mkdir(parents=True)
    for filename in ("generate.py", "normalize_schema.py"):
        shutil.copy(ROOT / "scripts" / filename, tmp_path / "scripts" / filename)
    for filename in ("overrides.json", "policies.json"):
        shutil.copy(ROOT / "openapi" / filename, tmp_path / "openapi" / filename)
    upstream = json.loads((ROOT / "openapi/upstream.json").read_text())
    upstream["components"]["schemas"]["AccountSummary"]["required"] = ["currency"]
    path = tmp_path / "openapi/upstream.json"
    path.write_text(json.dumps(upstream))
    command = [sys.executable, str(tmp_path / "scripts/generate.py")]
    subprocess.run(command, check=True, capture_output=True)
    generated = tmp_path / "src/cztrading212/_generated/models.py"
    source = generated.read_text()
    node = next(
        node
        for node in ast.parse(source).body
        if isinstance(node, ast.ClassDef) and node.name == "AccountSummary"
    )
    currency_field = next(
        node
        for node in node.body
        if isinstance(node, ast.AnnAssign)
        and isinstance(node.target, ast.Name)
        and node.target.id == "currency"
    )
    assert currency_field.value is None
    upstream["components"]["schemas"]["Evil:\nprint('unsafe')"] = {"type": "object"}
    path.write_text(json.dumps(upstream))
    result = subprocess.run(command, capture_output=True, text=True, check=False)
    assert result.returncode != 0 and "Invalid generated schema name" in result.stderr
    assert generated.read_text() == source


def test_null_union_branch_does_not_match_non_null_values():
    schema = {"oneOf": [{"type": "null"}, {"type": "integer"}]}
    assert parse(5, schema) == 5
    with pytest.raises(ResponseValidationError):
        parse("wrong", schema)


def test_datetime_rejects_out_of_range_timezone_components():
    with pytest.raises(ResponseValidationError, match="date-time"):
        parse("2026-01-01T00:00:00+01:60", {"type": "string", "format": "date-time"})


def test_generator_rejects_unsafe_mutation_policy_without_overwriting(tmp_path):
    for folder in ("scripts", "openapi", "src/cztrading212/_generated"):
        (tmp_path / folder).mkdir(parents=True)
    for filename in ("generate.py", "normalize_schema.py"):
        shutil.copy(ROOT / "scripts" / filename, tmp_path / "scripts" / filename)
    for filename in ("upstream.json", "overrides.json", "policies.json"):
        shutil.copy(ROOT / "openapi" / filename, tmp_path / "openapi" / filename)
    policies_path = tmp_path / "openapi/policies.json"
    policies = json.loads(policies_path.read_text())
    policies["placeMarketOrder"]["retry"] = "safe_read"
    policies_path.write_text(json.dumps(policies))
    sentinel = tmp_path / "src/cztrading212/_generated/models.py"
    sentinel.write_text("preserve me")
    result = subprocess.run(
        [sys.executable, str(tmp_path / "scripts/generate.py")],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode != 0 and "Unsafe retry policy" in result.stderr
    assert sentinel.read_text() == "preserve me"
