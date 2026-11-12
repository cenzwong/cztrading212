# Rate limiting and retries

Read-only service cache misses are subject to process-local endpoint rate
policies copied from the descriptions in the checked-in Trading 212 API
snapshot. The limiter reserves requests using monotonic time and a sliding
window; it also considers the broker's `x-ratelimit-remaining`,
`x-ratelimit-reset`, `x-ratelimit-period`, and `Retry-After` response headers.
Reservations are coordinated between threads and async tasks sharing the same
`RateLimiter` instance. Header observations are conservative when responses
arrive out of order.

The broker applies rate limits per account, including requests made with other
API keys or applications. A local limiter cannot see those requests before the
broker reports them, and separate limiter instances and processes do not share
state. Direct raw requests also bypass the limiter. A caller using multiple SDK
clients in one process may explicitly share a `RateLimiter` instance.

By default services wait at most 30 seconds for an available slot. Configure
`rate_limit_strategy="fail_fast"` to raise `RateLimitError` without waiting, or
set `max_rate_limit_wait` to a different finite, non-negative number. The
exception reports the operation and an estimated retry delay. A broker 429 is
reported as a `RateLimitError` if the retry policy is exhausted or cannot wait.

The optional journal-backed order service also schedules writes under their
documented rate policy and never retries them. Only an explicit allowlist of
documented GET operations may retry network
errors and HTTP 408, 429, 500, 502, 503, and 504. The default is at most two
retries, exponential backoff with jitter, and a 30-second elapsed retry budget.
`Retry-After` is respected when present. `RetryPolicy` configures the count and
wait budget; the budget limits whether another attempt may start, while HTTPX's
separate per-request timeout controls each network attempt. Authentication,
validation, and other errors are not retried. Cache hits do not reserve a rate
limit slot.

```python
from cztrading212 import RateLimiter, RetryPolicy, Trading212

limiter = RateLimiter()
with Trading212.from_env(
    rate_limiter=limiter,
    rate_limit_strategy="fail_fast",
    retry_policy=RetryPolicy(max_retries=1, max_elapsed=10),
) as client:
    positions = client.services.positions.list()
```

Raw operations preserve direct behavior: they make one request, without
caching, scheduling, or retries. `raw.call_with_metadata(...)` provides the
response, HTTP status, and filtered rate-limit headers. Raw write operations can affect real
accounts and have no deduplication; a network failure can leave an unknown
outcome. Inspect the broker before considering another submission.

Typed reads validate responses against the snapshot's documented 200 schema;
an unexpected successful status raises `ResponseValidationError` and is not
cached. A guarded order with an unexpected successful status remains unknown.
