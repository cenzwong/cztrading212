# Architecture and current status

The design has a raw API, validated services, and read-only application
workflows. The current alpha implements these layers with incomplete parser
coverage and optional read-only adapters. Raw performs one HTTP request per operation, preserves
JSON keys, decodes fractional numbers as `Decimal`, and has no retries or cache.
JSON objects with duplicate keys are rejected because their values are ambiguous;
the same rule applies when decoding cached responses. Network JSON must be valid
UTF-8; malformed bytes raise an error rather than being replaced in the result.
Services compose raw and use generated schemas and dataclasses. Async uses the
same operation metadata, codec, and parser.

The upstream snapshot has 22 operations, including deprecated pie operations.
The catalog in `src/cztrading212/_generated/operations.json` maps their official
operation IDs, methods, paths, schemas, and generated Python methods. The
snapshot declares no required fields in its component schemas; therefore the
parser cannot infer which fields are required. Known field types are checked
when present; unknown object fields and new string enum values are accepted.
The schema also does not declare `nextPagePath` nullable even though the general
pagination documentation describes a null value. An explicit override in `openapi/overrides.json` reflects that documented
behavior. The history service offers bounded page iterators and refuses next-page paths
that point outside the current documented endpoint.

Services offer memory and opt-in SQLite TTL caching, retrieval metadata, and
bounded history pagination, process-local endpoint rate limiting, and bounded
retries for explicitly eligible reads. An experimental opt-in order service
uses a durable journal, single-attempt submission, and conservative outcome
handling; it has not been verified against a demo account. Portfolio, dividend,
and history workflows compose services; optional CLI and MCP adapters expose selected read-only capabilities.
Direct
raw mutation methods are available and can affect real accounts. They do not
provide local deduplication or exactly-once execution. A network error after
submission may indicate an unknown outcome. Check the broker before taking any
further action.

The standard-library generator uses the checked-in snapshot, explicit normalization
overrides, and reviewed operation policies. See [generation and upstream review](generation.md)
for regeneration, parser limitations, and the scheduled review workflow.
Do not manually edit generated files.
