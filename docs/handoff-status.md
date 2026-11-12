# Engineering handoff status

This records the rebuild's verification state on 2026-11-12. The package is an
alpha; these results are not evidence of broker integration or a published
release. The original handoff's full scope remains the target.

| Area | Current evidence | Remaining evidence or decision |
| --- | --- | --- |
| Foundation | `uv`/Hatchling package, strict Pyright, Ruff, pytest, architecture and generation guidance. Core wheel installed without Pydantic in a clean environment. GitHub CI passed on the initial draft PR commit for Python 3.11–3.14 and the independent extras. | Verify CI again for any later changes. |
| OpenAPI and raw | The supplied JSON and YAML match; the checked-in snapshot matches the JSON. The [reference](api-reference.md) inventories 22 operations. Mocked contract tests cover generated mappings, Decimal decoding, and deterministic regeneration. | Real broker behavior and upstream changes after this snapshot remain unverified. The snapshot's missing required-field declarations and normalization overrides are described in [generation](generation.md). |
| Services | Mocked tests cover typed parsing, cache backends and invalidation, rate limits, retries, pagination, and synchronous/asynchronous clients. | An opted-in demo read is needed to establish integration with a real account. Cache and limiter scope and limitations are documented in [caching](caching.md) and [reliability](reliability.md). |
| Application | Portfolio, allocation, dividend, order-history, transaction-export, and local order-draft workflows have mocked tests; calculation and currency semantics are documented in [application workflows](application.md). | Real account data and broker-specific edge cases have not been verified. |
| CLI and MCP | Optional extras installed independently from a wheel in clean Python 3.13 environments; CLI startup and eight read-only MCP tools were checked. Adapter tests cover output, input validation, and redaction. | End-to-end use with broker credentials has not been verified. |
| Safe trading | Opt-in journal-backed submission and reconciliation have mocked durability, concurrency, failure, and no-retry tests. See [order safety](safety.md). | The handoff requires demo integration. No demo order has been placed; a write test requires its own explicit gate and chosen demo order. |
| Documentation and release | Examples, guides, API index, upstream-sync workflow, deterministic builds, and a publishing workflow exist. A clean temporary Git repository passed the local release sequence, including tests, regeneration, reproducible builds, Twine, and archive checks. | GitHub workflow execution, branch protection, PyPI Trusted Publishing configuration and validation, package-name ownership, license choice, and a remote Git installation remain unverified. See [release](release.md). |

The latest full local suite ran 234 tests successfully with two gated demo
tests skipped on Python 3.13. An earlier run of the then-current suite passed
on 3.11, 3.12, and 3.14; rerun the complete suite on each supported version
when preparing a release. All tests use mocks except explicitly gated demo
tests. No live requests or orders were made during this rebuild.

The previously tracked `.env` is removed from the rebuilt tree while the local
file remains in place. The repository is public; a pull request containing
that deletion displays the old file in its diff. The owner has reviewed the
file and authorized including its removal in a public draft pull request.
Historical exposure and any affected credentials still need review before a
release; see [release](release.md).
