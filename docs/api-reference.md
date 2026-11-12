# Raw API reference

This index is generated from the checked-in operation catalog. Run
`python scripts/generate_reference.py` after regenerating the API.
Names are relative to `client.raw`; async clients use the same names.
An asterisk marks a required argument. Bodies use official JSON keys.
Rates are from the snapshot and can change upstream. Deprecated operations
are retained because they appear in that snapshot.

| Operation ID | Raw method | HTTP endpoint | Arguments | Request body | 200 response | Rate |
| --- | --- | --- | --- | --- | --- | --- |
| `getAccountSummary` | `account.get_account_summary` | `GET /api/v0/equity/account/summary` | — | — | AccountSummary | 1 / 5s |
| `dividends` | `history.get_dividends` | `GET /api/v0/equity/history/dividends` | `cursor`, `ticker`, `limit` | — | PaginatedResponseHistoryDividendItem | 20 / 60s |
| `getReports` | `history.get_reports` | `GET /api/v0/equity/history/exports` | — | — | list[ReportResponse] | 1 / 60s |
| `requestReport` | `history.request_report` | `POST /api/v0/equity/history/exports` | `body`* | PublicReportRequest | EnqueuedReportResponse | 1 / 30s |
| `orders_1` | `history.get_orders` | `GET /api/v0/equity/history/orders` | `cursor`, `ticker`, `limit` | — | PaginatedResponseHistoricalOrder | 20 / 60s |
| `transactions` | `history.get_transactions` | `GET /api/v0/equity/history/transactions` | `cursor`, `time`, `limit` | — | PaginatedResponseHistoryTransactionItem | 20 / 60s |
| `exchanges` | `instruments.get_exchanges` | `GET /api/v0/equity/metadata/exchanges` | — | — | list[Exchange] | 1 / 30s |
| `instruments` | `instruments.get_instruments` | `GET /api/v0/equity/metadata/instruments` | — | — | list[TradableInstrument] | 1 / 50s |
| `orders` | `orders.get_orders` | `GET /api/v0/equity/orders` | — | — | list[Order] | 1 / 5s |
| `placeLimitOrder` | `orders.place_limit_order` | `POST /api/v0/equity/orders/limit` | `body`* | LimitRequest | Order | 1 / 2s |
| `placeMarketOrder` | `orders.place_market_order` | `POST /api/v0/equity/orders/market` | `body`* | MarketRequest | Order | 50 / 60s |
| `placeStopOrder_1` | `orders.place_stop_order` | `POST /api/v0/equity/orders/stop` | `body`* | StopRequest | Order | 1 / 2s |
| `placeStopOrder` | `orders.place_stop_limit_order` | `POST /api/v0/equity/orders/stop_limit` | `body`* | StopLimitRequest | Order | 1 / 2s |
| `cancelOrder` | `orders.cancel_order` | `DELETE /api/v0/equity/orders/{id}` | `id`* | — | — | 50 / 60s |
| `orderById` | `orders.get_order` | `GET /api/v0/equity/orders/{id}` | `id`* | — | Order | 1 / 1s |
| `getAll` (deprecated) | `pies.get_pies` | `GET /api/v0/equity/pies` | — | — | list[AccountBucketResultResponse] | 1 / 30s |
| `create` (deprecated) | `pies.create_pie` | `POST /api/v0/equity/pies` | `body`* | PieRequest | AccountBucketInstrumentsDetailedResponse | 1 / 5s |
| `delete` (deprecated) | `pies.delete_pie` | `DELETE /api/v0/equity/pies/{id}` | `id`* | — | — | 1 / 5s |
| `getDetailed` (deprecated) | `pies.get_pie` | `GET /api/v0/equity/pies/{id}` | `id`* | — | AccountBucketInstrumentsDetailedResponse | 1 / 5s |
| `update` (deprecated) | `pies.update_pie` | `POST /api/v0/equity/pies/{id}` | `id`*, `body`* | PieRequest | AccountBucketInstrumentsDetailedResponse | 1 / 5s |
| `duplicatePie` (deprecated) | `pies.duplicate_pie` | `POST /api/v0/equity/pies/{id}/duplicate` | `id`*, `body`* | DuplicateBucketRequest | AccountBucketInstrumentsDetailedResponse | 1 / 5s |
| `getPositions` | `positions.get_positions` | `GET /api/v0/equity/positions` | `ticker` | — | list[Position] | 1 / 1s |

The [machine-readable catalog](../src/cztrading212/_generated/operations.json)
contains complete parameter and response metadata. The corresponding
[schema definitions](../src/cztrading212/_generated/schemas.json) and
[generated dataclasses](../src/cztrading212/_generated/models.py) describe
fields; the upstream contract remains the authority for request semantics.
Raw write methods can affect live accounts and bypass service safeguards.
See [order safety](safety.md), [caching](caching.md), and
[rate limiting](reliability.md) before using them.
