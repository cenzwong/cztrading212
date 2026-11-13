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
