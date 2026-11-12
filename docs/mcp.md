# Model Context Protocol server

From this checkout, install with `pip install '.[mcp]'` and run `cz212-mcp`,
or install both extras with `pip install '.[cli,mcp]'` and run
`cz212 mcp serve --transport stdio`. After the rebuilt package is pushed, the
MCP extra can also be installed with
`pip install 'cztrading212[mcp] @ git+https://github.com/cenzwong/cztrading212.git'`.
Only stdio is supported. Credentials come
from `TRADING212_API_KEY` and `TRADING212_API_SECRET`; the environment defaults
to demo and can be set with `TRADING212_ENVIRONMENT=live`. The server performs
no broker request during startup. It shares a process-local memory cache and
rate limiter across tool calls. The CLI form can opt into SQLite with its global
`--cache-path` option. This local cache does not coordinate other applications.

The eight tools expose account summary, positions, active orders, one page of
dividends or transactions, portfolio snapshot and allocation, and a dividend
summary. Historical page sizes are limited to 50 and summary pagination to
100 pages; an incomplete summary raises an error. Inputs are validated using
MCP-specific schemas. Tools have read-only annotations and no order-submission,
cancellation, arbitrary URL, or raw-request tool. Annotations are hints to the
host and are not an authorization boundary.

Outputs use structured JSON with `ok`, `environment`, and `data`; Decimal values
are strings and dates use ISO-8601. Service data includes `fetched_at` and
`from_cache`; multi-source application results retain their source metadata.
Expected application failures return `ok: false` and a structured `error` object;
clients must check `ok`. Input schema failures are reported by MCP as tool
errors. Error responses avoid credential values and upstream response bodies.
Never treat tool annotations as a substitute for access control.
