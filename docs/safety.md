# Order safety

The application layer can prepare market, limit, stop, and stop-limit drafts
without a network request. It validates documented fields, finite Decimal or
integer quantities and prices, nonzero signed quantities, positive prices,
and supported time-validity values. It does not verify market availability,
buying power, instrument eligibility, price increments, or suitability.

## Guarded submission

The SDK includes an **experimental** journal-backed service. It is disabled by
default. Enable it explicitly with a persistent private journal. Live submission
requires the additional `enable_live_trading=True` flag as well as choosing the
live environment. The example creates a demo order if executed:

```python
from decimal import Decimal
from cztrading212 import OrderJournal, Trading212

with Trading212.from_env(
    environment="demo",
    enable_trading=True,
    order_journal=OrderJournal("/private/path/orders.sqlite"),
) as client:
    draft = client.app.trading.prepare_limit(
        ticker="AAPL_US_EQ",
        quantity=Decimal("-0.1"),
        limit_price=Decimal("150.00"),
        time_validity="DAY",
    )
    # Inspect the draft before explicitly submitting it.
    result = client.services.orders.submit_draft("unique-intent-id", draft)
    print(result.order.id)
```

`submit_market` is a convenience for market orders. `prepare_order` records a
draft without sending it; `submit_prepared` explicitly submits a still-prepared
intent. Sync and async clients support these methods. Preparation or a local
rate-limit/cache failure can leave the intent `PREPARED`, in which case a later
explicit `submit_prepared` can proceed. A duplicate or conflicting intent ID is
rejected. Order submissions use the documented process-local rate policy and
make at most one raw HTTP attempt per claimed intent; they are never retried.

The SQLite journal commits `PREPARED` before atomically claiming `SUBMITTING`.
A usable broker order ID in a validated success response is recorded as
`CONFIRMED`; this means a broker ID was observed, **not** that the order was
filled or profitable. Documented 400, 401, and 403 responses are recorded as
`REJECTED`. Timeouts, connection failures, 408, 429, server failures, malformed
success responses, cancellation after claim, and other ambiguous failures are
recorded as `OUTCOME_UNKNOWN` where possible. If journal persistence itself
fails, the record may remain `SUBMITTING` and the SDK raises an unknown-outcome
error. An ID observed before a persistence failure is included in the exception
and stored as a candidate in the unknown record when possible. This also applies
if response validation succeeds but the configured rate limiter then fails.
Neither state
is automatically resubmitted. Check the broker before taking further action.

Services invalidate the relevant credential/environment cache namespace before
and after a submission attempt. If invalidation fails before the claim, no HTTP
request is made and the intent stays prepared. If it fails after the response,
`CacheInvalidationError` reports the recorded state and any known order ID; an
unknown-outcome error reports whether invalidation succeeded. The affected
client suspends its own caching after such a failure. Other clients or processes
may still have stale data if their shared cache cannot be invalidated; repair or
clear it and inspect the broker before relying on it. A crash or external broker
activity can also leave cached data stale until refresh or TTL expiry.

## Reconciliation and limitations

`client.services.orders.reconcile(intent_id, candidate_order_id=...)` reads a
specific **pending** order when an ID is known or supplied. A 404 does not prove
that an order was never created or filled. Even matching ticker fields do not
prove an unknown order belongs to an intent: the journal state is not changed
by observation, and no fuzzy match triggers a resubmission. Without an ID, the
result remains unresolved and makes no broker request.

The journal contains sensitive order information in plaintext. New files
request owner-only permissions; existing file and parent-directory permissions
remain the caller's responsibility. Do not put credentials in intent IDs or
payloads. Processes coordinate only when they use the same journal file and
credential namespace. Different API keys, journal files, devices, or broker
interfaces can submit an equivalent order without consulting this journal.
There is no broker-side idempotency key or exactly-once guarantee. A record left
`SUBMITTING` after interruption must be treated as potentially submitted.

Raw order calls remain available and can place real orders in live mode. They
bypass the journal, service rate scheduling, and cache invalidation. CLI and MCP
expose no write operations. This service has mocked coverage but has **not** been
verified against a real demo or live account.

## Explicit demo integration tests

Standard tests make no broker requests. Read-only demo integration requires
`TRADING212_RUN_DEMO_READS=1` plus separate `TRADING212_DEMO_API_KEY` and
`TRADING212_DEMO_API_SECRET` environment variables. The demo order test has a
separate `TRADING212_RUN_DEMO_WRITES=I_ACCEPT_DEMO_ORDER` gate and additionally
requires an explicitly chosen `TRADING212_DEMO_TEST_TICKER`, signed
`TRADING212_DEMO_TEST_QUANTITY`, persistent `TRADING212_DEMO_JOURNAL_PATH`, and
`TRADING212_DEMO_INTENT_ID`. It can create a demo market order and does not
cancel it automatically. Reusing the same ID in the same journal is blocked;
inspect the demo account before choosing another ID. Both tests force the demo
environment and do not load `.env` files.
