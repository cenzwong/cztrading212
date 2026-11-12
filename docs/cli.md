# Command-line interface

Install the optional CLI extra:

```sh
# From this checkout:
pip install '.[cli]'
# Or, after the rebuilt package is pushed to GitHub:
pip install 'cztrading212[cli] @ git+https://github.com/cenzwong/cztrading212.git'
```

The tested Typer and Click version ranges keep the CLI extra independent of
Pydantic and MCP. The MCP extra brings its own validation dependencies.

The CLI uses `TRADING212_API_KEY` and `TRADING212_API_SECRET`, defaults to demo,
and never reads `.env` automatically. Select live explicitly with `--env live`
or `TRADING212_ENVIRONMENT=live`. Global options precede the command.

```sh
cz212 --env demo account summary --json
cz212 positions list --refresh --json
cz212 orders list --json
cz212 portfolio snapshot --json
cz212 portfolio allocation --json
cz212 dividends summary --year 2026 --max-pages 20 --json
cz212 transactions list --limit 50 --json
```

Outputs include the environment. Service results also include the broker
retrieval time and cache status; application results include source metadata.
JSON uses snake_case field names, ISO-8601 timestamps, and **strings for Decimal
values** to preserve precision across JSON consumers. The default human-readable
output is indented JSON. Success uses exit code 0; invalid input/configuration
uses 2, broker HTTP errors use 3, rate-limit errors use 4, and other SDK or
local errors use 1. Diagnostics go to stderr. No trading commands are exposed.

Use `--cache-path /path/to/private/cache.sqlite` or `CZ212_CACHE_PATH` to opt
into persistence. The parent directory must exist. `cz212 --cache-path PATH
cache stats --json` counts live and expired records. `cache clear` requires
`--all-accounts` and clears every account and environment in the selected file.
It does not delete the file. Persistent data is sensitive and unencrypted; see
[caching](caching.md).

For stdio MCP, install the MCP extra too and run `cz212 mcp serve`. Global
`--env` and `--cache-path` apply to that server. This command does not expose
write tools.
