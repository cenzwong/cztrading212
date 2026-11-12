# OpenAPI generation and upstream review

`openapi/upstream.json` is the supplied Trading 212 Public API snapshot.
The supplied `api.json` and `api.yaml` were compared during this rebuild and
contain the same data after normalizing YAML mapping keys to strings; the
checked-in snapshot matches the supplied JSON. The YAML file is not an additional
generation input.
`openapi/overrides.json` records evidence-backed corrections, including the
documented nullable final-page path and the Basic-authentication requirement.
The snapshot's operation security objects combine the Basic and legacy API-key
schemes in a single OpenAPI requirement. Both use the Authorization header,
whereas the official guide describes a Basic key-and-secret pair for each
request. The normalization override uses that documented Basic scheme and
requires the original security shape to remain unchanged; a future change
stops generation until the override is reviewed.

`openapi/policies.json` is reviewed policy metadata: each operation records
whether retries are allowed, its cache TTL, and whether it mutates broker state.
The generator rejects cache or retry policies for write operations. Endpoint
rate limits are extracted from the snapshot descriptions. The generated
operation catalog includes the official path, method, arguments, body and
response schemas, authentication, rate and local cache/retry policies, and
mutation risk. Runtime service policies use that catalog.

From the repository root, without network access:

```sh
uv run python scripts/normalize_schema.py
uv run python scripts/generate.py
uv run python scripts/generate_reference.py
uv run pytest
uv run pyright
uv run ruff check .
```

The generator writes `openapi/normalized.json` and the `_generated` models,
raw wrappers, strict parser entry points, catalog, and checksum manifest. The
reference generator renders the human-readable operation index from that catalog;
`--check` verifies it is current. The schema generator
validates the whole input before writing output: operation IDs and policy
coverage must be reviewed, schema references must resolve locally, and unsafe
identifiers or unsupported compositions are rejected. Do not hand-edit
`_generated`. Regeneration and tests check for unexpected differences.

The parser validates required and known fields, types, nested objects, arrays,
nullable values, supported `oneOf`/`anyOf` unions, Decimal numbers, RFC 3339
timestamps, string length bounds, and unique arrays. Unknown response fields
and new enum strings are allowed. The
snapshot declares no component fields required, so their absence cannot be
rejected without an evidence-backed override. Unsupported `allOf`/`not`
compositions and recognized unsupported validation constraints fail generation
rather than silently bypass validation. The generator also requires review if
an operation adds a successful status other than 200, because the service
parser currently selects the 200 response schema. Other schema constraints and
code-generation cases still require review when the
upstream contract changes.

`scripts/sync_upstream.py` downloads only the official bundle and compares it
with the stored snapshot. Use `--candidate PATH` for an offline comparison;
`--write` stages the candidate and an `openapi/UPSTREAM_CHANGE_REPORT.md` when
it differs. The report highlights additions, removals, types, nullability,
required fields, enums, signatures, deprecations, rate descriptions, and write
operation risks. Classifications are conservative review hints; the full diff
must be reviewed and absence of a warning is not proof of compatibility.

The scheduled GitHub workflow attempts regeneration and tests, then proposes a
pull request for human review. If generation or tests fail, the report records
the failure and the proposal requires manual fixes. Repository administrators
must enable workflow pull-request creation and enforce branch protection and
required review; the workflow does not grant itself permission to merge or
publish. The workflow has not been run against this repository's GitHub settings.
