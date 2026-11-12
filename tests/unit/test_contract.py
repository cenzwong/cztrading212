import hashlib
import json
import subprocess
import sys
from pathlib import Path

import httpx
import pytest

from cztrading212 import Trading212
from cztrading212.client import OPERATIONS

ROOT = Path(__file__).resolve().parents[2]


def test_api_reference_matches_operation_catalog():
    subprocess.run(
        [sys.executable, "scripts/generate_reference.py", "--check"], cwd=ROOT, check=True
    )


def test_generation_is_deterministic():
    generated = ROOT / "src/cztrading212/_generated"
    before = {p.name: p.read_bytes() for p in generated.iterdir() if p.is_file()}
    subprocess.run([sys.executable, "scripts/generate.py"], cwd=ROOT, check=True)
    after = {p.name: p.read_bytes() for p in generated.iterdir() if p.is_file()}
    assert before == after
    assert (
        json.loads(after["manifest.json"])["upstream_sha256"]
        == hashlib.sha256((ROOT / "openapi/upstream.json").read_bytes()).hexdigest()
    )


@pytest.mark.parametrize("operation", list(OPERATIONS.values()), ids=list(OPERATIONS))
def test_every_generated_raw_operation_maps_to_spec(operation):
    requests = []

    def handle(request):
        requests.append(request)
        return httpx.Response(200, text="{}")

    arguments = {
        parameter["name"]: "123"
        for parameter in operation["parameters"]
        if parameter.get("required")
    }
    if (operation["request_body"] or {}).get("required"):
        arguments["body"] = {}
    with Trading212("key", "secret", transport=httpx.MockTransport(handle)) as client:
        group = getattr(client.raw, operation["raw_group"])
        getattr(group, operation["raw_method"])(**arguments)
    assert len(requests) == 1
    assert requests[0].method == operation["method"]
    assert requests[0].url.path == operation["path"].replace("{id}", "123")


def test_generated_raw_signatures_and_catalog_policies_match_operations():
    import inspect

    from cztrading212._generated import operations as wrappers
    from cztrading212.core.cache_policy import DEFAULT_TTLS
    from cztrading212.core.reliability import READ_POLICIES, RETRYABLE_READS

    for operation_id, op in OPERATIONS.items():
        wrapper = getattr(wrappers, op["raw_group"].title())
        method = getattr(wrapper, op["raw_method"])
        signature = inspect.signature(method)
        expected = {parameter["name"] for parameter in op["parameters"]}
        if op["request_body"] is not None:
            expected.add("body")
        assert set(signature.parameters) == expected | {"self"}
        assert all(
            parameter.kind is inspect.Parameter.KEYWORD_ONLY
            for name, parameter in signature.parameters.items()
            if name != "self"
        )
        assert op["authentication"] == [{"authWithSecretKey": []}]
        assert op["rate_limit_policy"]["requests"] > 0
        if op["method"] == "GET":
            assert operation_id in READ_POLICIES
        else:
            assert operation_id not in RETRYABLE_READS and operation_id not in DEFAULT_TTLS
            assert op["retry_policy"] == "never" and op["mutation_risk"] == "write"


def test_normalization_keeps_upstream_unchanged_and_records_auth_override():
    upstream = json.loads((ROOT / "openapi/upstream.json").read_text())
    normalized = json.loads((ROOT / "openapi/normalized.json").read_text())
    path = "/api/v0/equity/account/summary"
    assert upstream["paths"][path]["get"]["security"] == [
        {"authWithSecretKey": [], "legacyApiKeyHeader": []}
    ]
    assert normalized["paths"][path]["get"]["security"] == [{"authWithSecretKey": []}]
    overrides = json.loads((ROOT / "openapi/overrides.json").read_text())
    assert overrides["security"]["source"].startswith("https://docs.trading212.com/")
