import copy
import importlib.util
import json
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
module_spec = importlib.util.spec_from_file_location(
    "sync_upstream", ROOT / "scripts/sync_upstream.py"
)
assert module_spec is not None and module_spec.loader is not None
module = importlib.util.module_from_spec(module_spec)
sys.modules[module_spec.name] = module
module_spec.loader.exec_module(module)
compare, render_report = module.compare, module.render_report


def original():
    return json.loads((ROOT / "openapi/upstream.json").read_text())


def test_drift_classifies_fields_nullability_enums_operations_and_mutation_risk():
    old = original()
    new = copy.deepcopy(old)
    schemas = new["components"]["schemas"]
    schemas["MarketRequest"]["properties"]["quantity"]["type"] = "string"
    schemas["MarketRequest"]["required"] = ["quantity"]
    schemas["AccountSummary"]["properties"]["totalValue"]["nullable"] = True
    schemas["Position"]["properties"]["newValue"] = {"type": "number"}
    schemas["Order"]["properties"]["side"]["enum"].remove("SELL")
    new["paths"]["/api/v0/equity/positions"]["get"]["deprecated"] = True
    new["paths"]["/api/v0/equity/positions"]["get"]["description"] += "\n**Rate limit:** 2 req / 1s"
    new["paths"]["/api/v0/new"] = {"post": {"operationId": "newWrite", "responses": {}}}
    changes = compare(old, new)
    assert any(
        c.severity == "breaking"
        and "MarketRequest.quantity" in c.location
        and "Type" in c.description
        for c in changes
    )
    assert any(c.severity == "breaking" and "required" in c.description for c in changes)
    assert any(c.severity == "compatible" and "Nullability" in c.description for c in changes)
    assert any(c.severity == "compatible" and c.location.endswith("newValue") for c in changes)
    assert any(c.severity == "breaking" and "Enum values removed" in c.description for c in changes)
    assert any(c.severity == "high-risk" and "orders/market" in c.location for c in changes)
    assert any(c.severity == "high-risk" and "new" in c.location for c in changes)
    assert any("Deprecation" in c.description for c in changes)
    assert any("rate limit" in c.description for c in changes)
    assert "not proof of compatibility" in render_report(changes)


def test_candidate_sync_writes_only_changed_valid_spec(tmp_path):
    (tmp_path / "scripts").mkdir()
    (tmp_path / "openapi").mkdir()
    shutil.copy(ROOT / "scripts/sync_upstream.py", tmp_path / "scripts/sync_upstream.py")
    old = original()
    snapshot = tmp_path / "openapi/upstream.json"
    snapshot.write_text(json.dumps(old))
    candidate = tmp_path / "candidate.json"
    candidate.write_text(json.dumps(old, indent=2))
    command = [
        sys.executable,
        str(tmp_path / "scripts/sync_upstream.py"),
        "--candidate",
        str(candidate),
        "--write",
    ]
    before = snapshot.read_bytes()
    subprocess.run(command, check=True, capture_output=True)
    assert snapshot.read_bytes() == before
    assert not (tmp_path / "openapi/UPSTREAM_CHANGE_REPORT.md").exists()
    new = copy.deepcopy(old)
    del new["paths"]["/api/v0/equity/positions"]
    candidate.write_text(json.dumps(new))
    subprocess.run(command, check=True, capture_output=True)
    assert json.loads(snapshot.read_text()) == new
    assert "Operation removed" in (tmp_path / "openapi/UPSTREAM_CHANGE_REPORT.md").read_text()
    candidate.write_text('{"openapi":"3.0.1", "paths": []}')
    result = subprocess.run(command, check=False, capture_output=True)
    assert result.returncode != 0
    assert json.loads(snapshot.read_text()) == new
