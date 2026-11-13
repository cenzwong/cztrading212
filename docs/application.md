# Application workflows

Application workflows compose validated read-only services and inherit their
cache, rate limiting, pagination, and retry policies. Synchronous and async
clients expose corresponding methods under `client.app`. `client.app.trading`
prepares local order drafts without submitting them; an independently enabled
service can submit drafts; see
[order safety](safety.md). All result models are frozen, slotted dataclasses.

```python
with Trading212.from_env() as client:
    report = client.app.portfolio.report()
    print(report.snapshot.total_value, report.snapshot.currency)
    for entry in report.allocation.entries:
        print(entry.ticker, entry.current_value, entry.fraction)

    dividends = client.app.dividends.summary(year=2026, max_pages=20)
    for total in dividends.totals:
        print(total.currency, total.amount, total.count)

    with open("transactions.csv", "w", encoding="utf-8", newline="") as output:
        result = client.app.history.transactions_csv(output, max_pages=20)
```

## Portfolio calculations

The snapshot returns the account's `totalValue` exactly as reported by the
broker, alongside its cash and investment objects and the positions response.
It does not reconstruct cash or infer an account total. Account summary and
positions are separate requests and are not an atomic broker snapshot; their
individual `fetched_at` times and cache status are included. Missing values
needed by a workflow raise `DataUnavailableError`.

Allocation uses each position's `walletImpact.currentValue`, which is expressed
in the wallet-impact currency, and requires that currency to equal the primary
account currency. Instrument trading currencies are not added or converted.
The denominator is the sum of position values only; it excludes cash. Each
entry represents one position, including any repeated tickers. Its `fraction`
is its value divided by that sum, rounded to 34 significant Decimal digits
using half-even rounding. A zero denominator produces `None` fractions.
Summation is exact within the supported exponent and precision range; extreme
values are rejected rather than silently rounded. No live price, return, tax,
or currency conversion is inferred.

## History calculations and exports

Dividend summaries traverse all returned pages up to the caller's bound, and
sum the broker's `amount` field separately for each reported currency. They do
not combine `grossAmountPerShare`, instrument currencies, or `amountInEuro`.
When `year` is provided, `paidOn` must include a timezone and is compared after
conversion to UTC. Rows are counted as returned: no deduplication or tax
interpretation is inferred. All traversed page retrieval times and cache states
are retained. If more pages remain after `max_pages`, the workflow raises
`PaginationError` rather than returning a partial total.

Historical order analysis counts returned history **items**, not unique order
IDs or executions. Missing order status or side appears as a `None` category.
Transaction CSV export writes reference, timestamp, type, currency, and the
exact Decimal amount in upstream order; missing optional fields are blank.
It streams pages to the provided text stream. An error or page-limit exception
can leave a partial CSV in that stream; callers needing an atomic file should
write a temporary file and rename it only after success. CSV text can contain
broker-provided strings; treat it as untrusted when opening it in a spreadsheet.
The async export writes to the supplied stream synchronously.

## Risk and current performance

`client.app.risk.concentration(refresh=False, aggregate_by_ticker=False)` ranks
individual position values in account currency. Duplicate tickers remain separate
unless explicitly aggregated. Zero values contribute zero; a zero total has no
weights, HHI or effective holding count. Missing, negative or mismatched-currency
values are rejected. HHI and ratios use 34 significant decimal digits, rounded
half-even. Top-N figures are fractions of invested positions, excluding cash.

`client.app.risk.exposure()` groups account-currency values by ticker and by
instrument trading currency. The latter is a proxy, not economic FX exposure.
Available-to-trade cash is reported separately, not treated as total cash or
assumed immediately deployable. Instrument type comes from broker metadata;
missing or conflicting type and missing currency are grouped under `None`.
Sector and geography are not inferred. Both methods include the underlying snapshot and its separate account
and position source timestamps; those reads are not atomic.

`client.app.performance.pnl_summary()` reports broker account investment totals
and each position's wallet impact independently. Realised P&L is account-level,
not reconstructed from open holdings. FX impact is displayed separately and
is not added to P&L. Returns divide reported unrealised P&L by a positive
reported cost; unavailable or non-positive costs have no return. Contribution
is position P&L divided by account-reported unrealised P&L when nonzero, and
may not sum to one if the broker totals differ. `winners_losers(limit=5,
rank_by="absolute")` also accepts `percentage` and `contribution`. Winners
and losers are identified by P&L sign even if aggregate P&L is negative.
Positions without wallet data or a wallet currency retain their ticker but
have unavailable monetary metrics. These are
current snapshots, not historical, annualised or time-weighted returns.
The same methods are awaitable on `AsyncTrading212`.

## Dividend income and cash flow

`client.app.dividends.income_report(limit=50, max_pages=20, refresh=False)`
returns UTC-calendar annual and monthly totals, ticker/month totals, currency
totals and a payment count. `by_ticker()` returns the same ticker/year/month/
currency groups with provenance. These methods require an identifiable ticker,
a timezone-aware payment time, currency and amount; conflicting ticker fields
are rejected. The existing `summary()` contract remains unchanged.
`growth()` compares observed income in adjacent calendar years separately by
currency and by ticker. A year without an observed payment is not assumed to be
a complete zero-income year; zero prior income has no percentage change. These
are changes in cash received, not dividend-per-share growth or forecasts.

`client.app.cashflow.summary()` and `monthly()` group the broker's signed
transaction amounts by exact type and reported currency. `DEPOSIT` and
`WITHDRAW` map to deposits and withdrawals; `FEE`, `INTEREST_ON_FREE_CASH`,
and `LENDING_INTEREST` map to other movements. `TRANSFER` and unknown or absent
types are unclassified because the schema does not establish their economic
meaning. Monthly grouping uses timezone-aware UTC timestamps and rejects missing
times; the non-period summary does not require them. Records, including repeated
references, are counted as returned and never deduplicated.

All new history methods expose page sources, page and record counts, the
requested page bounds and a `complete_traversal` indication. That indicates the
API returned no next page for this request, not that the broker's historical
record is exhaustive. Pagination errors raise instead of returning partial
aggregates. The same methods are awaitable on `AsyncTrading212`.

## Instrument discovery and audit

`client.app.instruments.search(query, limit=50, refresh=False)` searches the
locally retrieved broker instrument metadata. Ticker and ISIN comparisons are
exact and case-insensitive; names use case-insensitive substring matching.
`lookup_isin(isin, limit=50, refresh=False)` matches ISIN only. Results retain
all matching records up to the limit, report the total before limiting and sort
by ticker, ISIN and name, with broker response order as the final tie-breaker.
Neither method is a market-data or executable-price lookup. The metadata source
and retrieval time are included.

`client.app.audit.detect_gaps(limit=50, max_pages=20, refresh=False)` inspects
bounded transaction and historical order pages. It reports evidence-backed
missing fields, duplicate references, potentially repeated records,
unclassified transaction types and conflicting order ticker fields. Findings
have a severity, certainty and evidence. A repeated reference or record does
not establish an erroneous transaction. `reconcile()` includes the same audit
and explicitly reports that the available data cannot establish a complete
account reconciliation. The absence of a corresponding order is never treated as
proof of a missing transaction. Both histories must finish within their own
page bounds or the operation raises; records are never modified or removed.
Both workflows have async equivalents.

## Rebalancing plans

```python
from decimal import Decimal

plan = client.app.rebalancing.plan(
    target_weights={"AAPL_US_EQ": Decimal("0.4"), "VUSA_GB_EQ": Decimal("0.6")},
    minimum_trade_value=Decimal("10"),
    target_tolerance=Decimal("0.01"),
    buy_only=False,
)
for entry in plan.entries:
    print(entry.ticker, entry.action, entry.proposed_adjustment, entry.currency)
```

Identifiers are examples; every held and target ticker must occur in retrieved
broker instrument metadata. The default basis is position values only. Target
weights are non-negative `Decimal` values summing to one within `1e-12`;
held tickers omitted from the targets are explicitly assigned a zero target.
Duplicate positions are summed by ticker for this ticker-level planner. A zero
or negative portfolio value, negative position value, missing wallet value or
mismatched wallet currency is rejected. Valuation uses account-currency wallet
values and does not perform conversion.

To include cash, supply `cash=CashValuation(amount=..., currency=...,
valued_at=..., source=...)` and `cash_target_weight=Decimal(...)`. The latter
participates in the weight sum. `CashValuation` is imported from
`cztrading212.application.rebalancing`; its timestamp must have a timezone and
its source must identify the caller's input. The broker's available-to-trade
field is not used as total cash. The target is applied to positions plus this
explicit total cash input. Reported drift is current minus target weight;
`value_difference` is target value minus current value. Calculations use a
34-digit half-even context and do not quantize to currency minor units.

A recommendation is suppressed when its absolute weight drift is at or below
`target_tolerance`, its absolute value is below `minimum_trade_value`, or it is
a sale in buy-only mode. The unsuppressed difference remains visible. Total
buys and sells include only suggested adjustments. `remaining_cash_requirement`
is unavailable unless `available_cash=CashValuation(...)` is provided; then it
is the larger of the liquidity shortfall `max(0, buys - sells - available_cash)`
and, for a cash-inclusive plan, the shortfall against the cash target after
suggested trades, measured against the initial portfolio valuation. Additional
funding would change that valuation and therefore its target amounts. The estimate assumes sale proceeds settle and supplied cash
can be used; neither assumption is verified. A cash-inclusive plan also reports
its cash-target shortfall separately, even when available cash is unknown. This is a
value plan, not a quantity, order draft, eligibility check, executable quote or
promise of funding. There are no order side effects. Account, position, metadata
and caller cash timestamps can differ. Async clients use the same signature with
`await`.
