# cztrading212

A Python 3.11+ SDK for the Trading 212 Public API. This is an **early, incomplete
rebuild**, not a stable release. The current implementation supplies a direct
sync/async operation interface and preliminary schema-validated read services.
Services provide read-only typed facades, TTL caching, and bounded history
pagination, endpoint-aware rate limiting, and conservative read retries.
Portfolio, dividend, and history workflows are available, along with optional
read-only CLI and MCP adapters. Journal-backed order submission is experimental
and requires explicit opt-in.

After these changes are pushed, install them from GitHub with
`pip install git+https://github.com/cenzwong/cztrading212.git`. Before then,
install the current checkout with `pip install .` from the repository root.
Set `TRADING212_API_KEY` and `TRADING212_API_SECRET` in your environment.
Demo is the default; choose `environment=Environment.LIVE` explicitly for live.

```python
from cztrading212 import Trading212

with Trading212.from_env() as client:
    account = client.raw.call("getAccountSummary")
    typed_account = client.services.call("getAccountSummary")
    positions = client.raw.call("getPositions", ticker="AAPL_US_EQ")
```

The raw interface accepts official `operationId` values from
[the API reference](docs/api-reference.md) and its
[operation catalog](src/cztrading212/_generated/operations.json). Parameters
use the upstream names; operations accepting a JSON request body take `body=`.
Raw mutation calls can place real orders in the live environment. The raw client
never retries, caches, or deduplicates requests. An interrupted order request may
have been accepted; check the broker before deciding what to do next.

JSON fractional numbers decode to `Decimal`, preserving upstream field names and
nesting. These raw responses cannot necessarily be passed to standard
`json.dumps`; use `cztrading212.codec.dumps` for numeric JSON output. Float input
is rejected by that encoder. Services validate present known schema fields,
ignore unknown object fields, and allow new enum values. The supplied upstream
schemas declare no required fields, so absence of a field cannot be treated as
an error without an explicit, justified schema override. Optional fields are
represented by `None`. Unsupported schema compositions stop generation; see [generation details](docs/generation.md).

Run `python scripts/generate.py` after updating `openapi/upstream.json` to
regenerate the models and operation inventory. The snapshot comes from Trading
212's [API documentation](https://docs.trading212.com/api/). No credentials or
network access are required for unit tests.

## Services, caching, and pagination

Services use a local memory cache by default; raw operations always bypass it.
Use `refresh=True` to request a new value and `call_with_metadata` to inspect
the broker retrieval time and whether the result came from cache. Persistent
SQLite caching is opt-in. See [cache behavior and privacy](docs/caching.md).

```python
with Trading212.from_env() as client:
    account = client.services.account.summary()
    positions = client.services.positions.list(refresh=True)
    for result in client.services.history.iter_pages("dividends", limit=50, max_pages=10):
        for dividend in result.data.items:
            print(dividend)
```

History iteration is bounded and raises `PaginationError` rather than silently
truncating when more pages remain. It rejects unexpected hosts, endpoints,
parameters, and pagination cycles. The async client exposes the same services
and an async page iterator.

See [rate limiting and retry behavior](docs/reliability.md) for wait bounds,
shared limiter instances, and the distinction between services and raw calls.

See [application workflows](docs/application.md) for calculation semantics,
source freshness, currency handling, and streaming export limitations.

Optional adapters: [CLI](docs/cli.md) and [read-only MCP server](docs/mcp.md).

See [OpenAPI generation and upstream review](docs/generation.md) for
normalization overrides, policy ownership, and the scheduled review workflow.

[Order safety](docs/safety.md) describes local drafts, journal limitations,
the opt-in journal-backed submission service, and the risks of raw requests.
The service has not yet been verified against a real demo or live account.

See [release and versioning](docs/release.md) for publication gates and
current release blockers.

See the [engineering handoff status](docs/handoff-status.md) for verification
evidence and outstanding milestone work.
