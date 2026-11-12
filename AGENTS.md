# Repository guidance

cztrading212 is a Python SDK for the Trading 212 Public API. Keep the layers
separate: raw maps one official operation to one direct HTTP request; services
validate and apply read policies; application workflows compose services; CLI
and MCP are adapters. Core models are standard-library dataclasses, not
Pydantic. See `docs/architecture.md` and `docs/generation.md`.

The checked-in OpenAPI snapshot, explicit overrides, and reviewed policy file
are inputs to generation. Never edit `_generated` manually. Run
`python scripts/normalize_schema.py`, `python scripts/generate.py`, and
`python scripts/generate_reference.py` after an input change; examine the
normalized schema, generated files, reference, and manifest.
Unsupported schema changes and new operations require review, not silent
fallbacks. Preserve official raw JSON keys and Decimal precision.

Run `uv sync --locked`, `uv run ruff check .`, `uv run ruff format --check .`,
`uv run pyright`, `uv run pytest`, and `uv build`; verify offline regeneration
when changing generation. Unit tests must use mocked transports, must not load
real credentials, and must never submit real orders. Demo integration tests are
opt-in; write tests require a separate explicit gate. Never automatically retry
or deduplicate raw mutations. Treat unknown order outcomes as ambiguous.

Document public behavior, compatibility assumptions, cache and rate policies,
and meaningful limitations alongside changes. Do not declare a milestone
complete without its acceptance evidence. Preserve unrelated user work, account
information, and local credentials; never print or commit secrets.
