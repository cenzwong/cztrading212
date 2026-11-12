# Service caching

Read-only service calls use a thread-safe, process-local memory cache by default.
The cache stores raw JSON and parses it again on a hit. `client.raw` always makes
one direct request and bypasses the cache. No mutation is cached or retried by
services. The experimental opt-in order service invalidates its namespace
before and after submission; raw mutations bypass that policy.

Default TTLs are 15 seconds for account summary, 30 seconds for positions,
10 seconds for active orders, 10 minutes for instrument and exchange metadata,
and 5 minutes for historical orders, dividends, and transactions. They are
local freshness policies, not guarantees of broker freshness or coordination
with other clients. Calls with `refresh=True` bypass the cached response and
replace it after a successful, validated response. `services.invalidate` accepts
an operation ID and the same parameters as `services.call`; it invalidates the
entire credential/environment namespace so that in-flight reads cannot restore
the invalidated entry. This can evict other cached operations in that namespace.
Setting a TTL to zero disables caching for that operation.

```python
from cztrading212 import SQLiteCache, Trading212

# Choose and protect a private location; the parent directory must exist.
cache = SQLiteCache("/path/to/private/trading212-cache.sqlite")
with Trading212.from_env(cache=cache, cache_ttls={"getPositions": 15}) as client:
    positions = client.services.positions.list()
    fresh = client.services.positions.list(refresh=True)
    result = client.services.call_with_metadata("getAccountSummary")
    print(result.fetched_at, result.from_cache, result.data)
    client.services.invalidate("getAccountSummary")
```

SQLite persistence is opt-in. New database files request owner-only permissions;
existing files and parent directories retain their permissions. Database contents
are sensitive account information and are not encrypted by this library. A
cache can be shared between clients, but `cache.clear()` removes **all** entries
in that backend, across accounts and environments. Keys include a credential-
scoped HMAC, environment, operation, arguments, and format version; neither
credential is stored in the cache. Multiple API keys for the same account do
not share cached data. Cache records are validated on every hit. A corrupt or
schema-incompatible record is invalidated and raises `ResponseValidationError`.

The current implementation does not yet coalesce concurrent misses or provide asynchronous SQLite I/O.
Backend statistics count active and expired records. Access to a shared SQLite
backend may briefly block the async event loop. Account changes made outside the
SDK can remain unseen until a TTL expires or the caller refreshes.

`client.services.invalidate_all()` invalidates cached entries for that
credential/environment namespace without clearing other namespaces. Cache
generations prevent a read that began before invalidation from filling the
cache afterward, including across clients sharing the same SQLite file. A read
already in progress may still return its own earlier response; this is not
snapshot isolation. Raw mutation calls do not invoke service invalidation.

The cache key format changed to version 2. Earlier version-1 entries are no
longer read and may remain in an existing SQLite file until it is cleared.
Custom cache backends must implement generation and conditional-write methods
from the `Cache` protocol to preserve invalidation behavior.
