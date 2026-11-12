import asyncio
import threading
from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal

import httpx
import pytest

from cztrading212 import AsyncTrading212, MemoryCache, RateLimiter, Trading212
from cztrading212.core.cache_policy import namespace
from cztrading212.core.safety.journal import IntentState, OrderJournal
from cztrading212.exceptions import (
    APIError,
    CacheInvalidationError,
    DuplicateIntentError,
    IntentConflictError,
    OrderOutcomeUnknownError,
    RateLimitError,
    TradingDisabledError,
)

NS = namespace("demo", "key", "secret")


def options(path, handle, **extra):
    return {
        "api_key": "key",
        "api_secret": "secret",
        "transport": httpx.MockTransport(handle),
        "rate_limiter": RateLimiter(policies={}),
        "order_journal": OrderJournal(path),
        "enable_trading": True,
        **extra,
    }


def test_submission_disabled_without_explicit_opt_in(tmp_path):
    with (
        Trading212(
            "key",
            "secret",
            transport=httpx.MockTransport(
                lambda request: pytest.fail("network must not be called")
            ),
        ) as client,
        pytest.raises(TradingDisabledError),
    ):
        client.services.orders.submit_market(ticker="ABC", quantity=1, intent_id="one")
    journal = OrderJournal(tmp_path / "journal.sqlite")
    with pytest.raises(ValueError, match="explicit enable_trading"):
        Trading212("key", "secret", order_journal=journal)
    with pytest.raises(ValueError, match="persistent"):
        Trading212("key", "secret", enable_trading=True)
    with pytest.raises(ValueError, match="enable_live_trading"):
        Trading212("key", "secret", environment="live", enable_trading=True, order_journal=journal)
    with pytest.raises(TypeError, match="booleans"):
        Trading212("key", "secret", enable_trading=1, order_journal=journal)


def test_success_has_durable_claim_before_one_request_and_blocks_duplicates(tmp_path):
    path = tmp_path / "journal.sqlite"
    calls = []

    def handle(request):
        calls.append(request)
        assert OrderJournal(path).get(NS, "one").state is IntentState.SUBMITTING
        assert request.url.path.endswith("/orders/market")
        assert b'"quantity":0.100000000000000001' in request.content
        return httpx.Response(200, text='{"id":42,"ticker":"ABC","status":"NEW"}')

    with Trading212(**options(path, handle)) as client:
        result = client.services.orders.submit_market(
            ticker="ABC", quantity=Decimal("0.100000000000000001"), intent_id="one"
        )
        assert result.order.id == 42
        assert client.services.orders.intent("one").state is IntentState.CONFIRMED
        with pytest.raises(DuplicateIntentError):
            client.services.orders.submit_market(
                ticker="ABC", quantity=Decimal("0.100000000000000001"), intent_id="one"
            )
        with pytest.raises(IntentConflictError):
            client.services.orders.submit_market(ticker="ABC", quantity=2, intent_id="one")
    assert len(calls) == 1


@pytest.mark.parametrize(
    "status,expected",
    [
        (400, IntentState.REJECTED),
        (401, IntentState.REJECTED),
        (403, IntentState.REJECTED),
        (408, IntentState.OUTCOME_UNKNOWN),
        (429, IntentState.OUTCOME_UNKNOWN),
        (500, IntentState.OUTCOME_UNKNOWN),
    ],
)
def test_http_failure_never_retries_and_records_conservative_outcome(tmp_path, status, expected):
    path = tmp_path / "journal.sqlite"
    calls = []

    def handle(request):
        calls.append(request)
        return httpx.Response(status)

    with Trading212(**options(path, handle)) as client:
        error_type = APIError if expected is IntentState.REJECTED else OrderOutcomeUnknownError
        with pytest.raises(error_type):
            client.services.orders.submit_market(ticker="ABC", quantity=1, intent_id="one")
        assert client.services.orders.intent("one").state is expected
        with pytest.raises(DuplicateIntentError):
            client.services.orders.submit_prepared("one")
    assert len(calls) == 1


@pytest.mark.parametrize("payload", ["{}", '{"id":true}', '{"id":5,"ticker":"OTHER"}', "not-json"])
def test_invalid_success_response_is_unknown_and_never_retried(tmp_path, payload):
    calls = []

    def handle(request):
        calls.append(request)
        return httpx.Response(200, text=payload)

    with Trading212(**options(tmp_path / "journal.sqlite", handle)) as client:
        with pytest.raises(OrderOutcomeUnknownError):
            client.services.orders.submit_market(ticker="ABC", quantity=1, intent_id="one")
        assert client.services.orders.intent("one").state is IntentState.OUTCOME_UNKNOWN
        with pytest.raises(DuplicateIntentError):
            client.services.orders.submit_prepared("one")
    assert len(calls) == 1


def test_network_timeout_remains_unknown_and_never_resubmits(tmp_path):
    calls = []

    def handle(request):
        calls.append(request)
        raise httpx.ReadTimeout("timeout", request=request)

    with Trading212(**options(tmp_path / "journal.sqlite", handle)) as client:
        with pytest.raises(OrderOutcomeUnknownError):
            client.services.orders.submit_market(ticker="ABC", quantity=-1, intent_id="sell")
        assert client.services.orders.intent("sell").state is IntentState.OUTCOME_UNKNOWN
    assert len(calls) == 1


def test_undocumented_order_success_status_remains_unknown(tmp_path):
    calls = []

    def handle(request):
        calls.append(request)
        return httpx.Response(202, json={"id": 42, "ticker": "ABC"})

    with Trading212(**options(tmp_path / "journal.sqlite", handle)) as client:
        with pytest.raises(OrderOutcomeUnknownError):
            client.services.orders.submit_market(ticker="ABC", quantity=1, intent_id="one")
        assert client.services.orders.intent("one").state is IntentState.OUTCOME_UNKNOWN
    assert len(calls) == 1


def test_limiter_failure_after_valid_response_preserves_order_id(tmp_path):
    class FailingLimiter(RateLimiter):
        def observe(self, operation, headers, *, limited=False):
            raise RuntimeError("limiter failed")

    calls = []

    def handle(request):
        calls.append(request)
        return httpx.Response(200, json={"id": 42, "ticker": "ABC"})

    with Trading212(
        **options(tmp_path / "journal.sqlite", handle, rate_limiter=FailingLimiter(policies={}))
    ) as client:
        with pytest.raises(OrderOutcomeUnknownError) as error:
            client.services.orders.submit_market(ticker="ABC", quantity=1, intent_id="one")
        assert error.value.observed_order_id == 42
        entry = client.services.orders.intent("one")
        assert entry.state is IntentState.OUTCOME_UNKNOWN and entry.order_id == 42
        with pytest.raises(DuplicateIntentError):
            client.services.orders.submit_prepared("one")
    assert len(calls) == 1


def test_async_limiter_failure_after_valid_response_preserves_order_id(tmp_path):
    class FailingLimiter(RateLimiter):
        def observe(self, operation, headers, *, limited=False):
            raise RuntimeError("limiter failed")

    async def run():
        calls = []

        def handle(request):
            calls.append(request)
            return httpx.Response(200, json={"id": 42, "ticker": "ABC"})

        async with AsyncTrading212(
            **options(tmp_path / "journal.sqlite", handle, rate_limiter=FailingLimiter(policies={}))
        ) as client:
            with pytest.raises(OrderOutcomeUnknownError) as error:
                await client.services.orders.submit_market(
                    ticker="ABC", quantity=1, intent_id="one"
                )
            assert error.value.observed_order_id == 42
            entry = client.services.orders.intent("one")
            assert entry.state is IntentState.OUTCOME_UNKNOWN and entry.order_id == 42
        assert len(calls) == 1

    asyncio.run(run())


def test_rate_wait_failure_leaves_prepared_intent_for_explicit_resume(tmp_path):
    limiter = RateLimiter(policies={"placeMarketOrder": (1, 60)}, clock=lambda: 0)
    limiter.acquire("placeMarketOrder", strategy="fail_fast", max_wait=0)
    calls = []

    def handle(request):
        calls.append(request)
        return httpx.Response(200, json={"id": 9})

    path = tmp_path / "journal.sqlite"
    cfg = options(path, handle, rate_limiter=limiter, rate_limit_strategy="fail_fast")
    with Trading212(**cfg) as client:
        with pytest.raises(RateLimitError):
            client.services.orders.submit_market(ticker="ABC", quantity=1, intent_id="one")
        assert client.services.orders.intent("one").state is IntentState.PREPARED
    cfg["rate_limiter"] = RateLimiter(policies={})
    with Trading212(**cfg) as client:
        assert client.services.orders.submit_prepared("one").order.id == 9
    assert len(calls) == 1


def test_pre_and_post_invalidation_and_cache_failure_semantics(tmp_path):
    class FailingCache(MemoryCache):
        def __init__(self):
            super().__init__()
            self.failures = set()
            self.invalidations = 0

        def invalidate_namespace(self, name):
            self.invalidations += 1
            if self.invalidations in self.failures:
                raise OSError("sensitive filesystem detail")
            super().invalidate_namespace(name)

    cache = FailingCache()
    calls = []

    def handle(request):
        calls.append(request)
        return httpx.Response(200, text='{"id":99}')

    cache.failures = {1}
    with Trading212(**options(tmp_path / "journal.sqlite", handle, cache=cache)) as client:
        with pytest.raises(CacheInvalidationError) as before:
            client.services.orders.submit_market(ticker="ABC", quantity=1, intent_id="one")
        assert before.value.state == "PREPARED" and not calls
        assert client.services.orders.intent("one").state is IntentState.PREPARED
        cache.failures = {3}
        with pytest.raises(CacheInvalidationError) as after:
            client.services.orders.submit_prepared("one")
        assert after.value.state == "CONFIRMED" and after.value.order_id == 99
        assert client.services.orders.intent("one").state is IntentState.CONFIRMED
        with pytest.raises(DuplicateIntentError):
            client.services.orders.submit_prepared("one")
    assert len(calls) == 1


def test_journal_failure_after_broker_response_exposes_id_but_not_safe_retry(tmp_path):
    class FailingJournal(OrderJournal):
        def finish(self, *args, **kwargs):
            if kwargs.get("state") is IntentState.CONFIRMED:
                raise OSError("disk error")
            return super().finish(*args, **kwargs)

    journal = FailingJournal(tmp_path / "journal.sqlite")
    calls = []

    def handle(request):
        calls.append(request)
        return httpx.Response(200, json={"id": 33})

    cfg = options(tmp_path / "other.sqlite", handle, order_journal=journal)
    with Trading212(**cfg) as client:
        with pytest.raises(OrderOutcomeUnknownError) as error:
            client.services.orders.submit_market(ticker="ABC", quantity=1, intent_id="one")
        assert error.value.observed_order_id == 33
        assert client.services.orders.intent("one").state is IntentState.OUTCOME_UNKNOWN
        assert client.services.orders.intent("one").order_id == 33
        with pytest.raises(DuplicateIntentError):
            client.services.orders.submit_prepared("one")
    assert len(calls) == 1


def test_concurrent_clients_share_journal_and_submit_once(tmp_path):
    started, release = threading.Event(), threading.Event()
    calls = []

    def handle(request):
        calls.append(request)
        started.set()
        assert release.wait(5)
        return httpx.Response(200, json={"id": 11})

    path = tmp_path / "journal.sqlite"
    with (
        Trading212(**options(path, handle)) as first,
        Trading212(**options(path, handle)) as second,
        ThreadPoolExecutor(max_workers=1) as pool,
    ):
        pending = pool.submit(
            first.services.orders.submit_market, ticker="ABC", quantity=1, intent_id="same"
        )
        try:
            assert started.wait(5)
            with pytest.raises(DuplicateIntentError):
                second.services.orders.submit_market(ticker="ABC", quantity=1, intent_id="same")
        finally:
            release.set()
        assert pending.result(timeout=5).order.id == 11
    assert len(calls) == 1


def test_async_cancellation_after_send_persists_unknown():
    async def run(path):
        started = asyncio.Event()
        calls = []

        async def handle(request):
            calls.append(request)
            started.set()
            await asyncio.Event().wait()
            return httpx.Response(200, json={"id": 12})

        async with AsyncTrading212(**options(path, handle)) as client:
            task = asyncio.create_task(
                client.services.orders.submit_market(ticker="ABC", quantity=1, intent_id="async")
            )
            await asyncio.wait_for(started.wait(), timeout=5)
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
            assert client.services.orders.intent("async").state is IntentState.OUTCOME_UNKNOWN
            with pytest.raises(DuplicateIntentError):
                await client.services.orders.submit_prepared("async")
        assert len(calls) == 1

    import tempfile

    with tempfile.TemporaryDirectory() as directory:
        asyncio.run(run(directory + "/journal.sqlite"))


def test_reconciliation_of_unknown_outcome_never_promotes_by_weak_matching(tmp_path):
    calls = []

    def handle(request):
        calls.append(request)
        if request.method == "POST":
            raise httpx.ReadTimeout("timeout", request=request)
        return httpx.Response(200, text='{"id":55,"ticker":"ABC","status":"NEW"}')

    with Trading212(**options(tmp_path / "journal.sqlite", handle)) as client:
        with pytest.raises(OrderOutcomeUnknownError):
            client.services.orders.submit_market(ticker="ABC", quantity=1, intent_id="one")
        unresolved = client.services.orders.reconcile("one")
        assert (
            unresolved.candidate is None and unresolved.journal_state is IntentState.OUTCOME_UNKNOWN
        )
        assert len(calls) == 1
        observed = client.services.orders.reconcile("one", candidate_order_id=55)
        assert observed.ticker_matches is True and observed.candidate.id == 55
        assert "do not prove" in observed.note
        assert client.services.orders.intent("one").state is IntentState.OUTCOME_UNKNOWN
        with pytest.raises(DuplicateIntentError):
            client.services.orders.submit_prepared("one")
    assert [request.method for request in calls] == ["POST", "GET"]


def test_reconciliation_404_does_not_claim_order_was_not_executed(tmp_path):
    calls = []

    def handle(request):
        calls.append(request)
        if request.method == "POST":
            return httpx.Response(200, json={"id": 42})
        return httpx.Response(404)

    with Trading212(**options(tmp_path / "journal.sqlite", handle)) as client:
        client.services.orders.submit_market(ticker="ABC", quantity=1, intent_id="one")
        result = client.services.orders.reconcile("one")
        assert result.candidate is None and result.candidate_order_id == 42
        assert "does not establish" in result.note
        assert client.services.orders.intent("one").state is IntentState.CONFIRMED
    assert [request.method for request in calls] == ["POST", "GET"]


def test_reconciliation_rejects_conflicting_or_malformed_candidate(tmp_path):
    def handle(request):
        if request.method == "POST":
            return httpx.Response(200, json={"id": 42})
        return httpx.Response(200, json={"id": 99, "ticker": "ABC"})

    with Trading212(**options(tmp_path / "journal.sqlite", handle)) as client:
        client.services.orders.submit_market(ticker="ABC", quantity=1, intent_id="one")
        with pytest.raises(ValueError, match="conflicts"):
            client.services.orders.reconcile("one", candidate_order_id=99)
        from cztrading212.exceptions import ResponseValidationError

        with pytest.raises(ResponseValidationError, match="mismatched"):
            client.services.orders.reconcile("one")


def test_success_invalidates_cached_account_and_positions(tmp_path):
    calls = []

    def handle(request):
        calls.append(request)
        if request.method == "POST":
            return httpx.Response(200, json={"id": 42})
        return httpx.Response(200, json={"id": sum(r.method == "GET" for r in calls)})

    with Trading212(**options(tmp_path / "journal.sqlite", handle)) as client:
        assert client.services.account.summary().id == 1
        assert client.services.account.summary().id == 1
        client.services.orders.submit_market(ticker="ABC", quantity=1, intent_id="one")
        assert client.services.account.summary().id == 2
    assert [r.method for r in calls] == ["GET", "POST", "GET"]


def test_conditional_drafts_are_submitted_once_to_documented_endpoint(tmp_path):
    calls = []

    def handle(request):
        calls.append(request)
        return httpx.Response(200, json={"id": 42})

    with Trading212(**options(tmp_path / "journal.sqlite", handle)) as client:
        draft = client.app.trading.prepare_stop_limit(
            ticker="ABC", quantity=-1, stop_price=10, limit_price=9, time_validity="DAY"
        )
        prepared = client.services.orders.prepare_order("intent", draft)
        assert prepared.state is IntentState.PREPARED and not calls
        result = client.services.orders.submit_prepared("intent")
        assert result.order.id == 42
    assert len(calls) == 1 and calls[0].url.path.endswith("/orders/stop_limit")


def test_async_success_and_reconciliation(tmp_path):
    async def run():
        calls = []

        async def handle(request):
            calls.append(request)
            return httpx.Response(200, json={"id": 22, "ticker": "ABC"})

        async with AsyncTrading212(**options(tmp_path / "journal.sqlite", handle)) as client:
            result = await client.services.orders.submit_market(
                ticker="ABC", quantity=1, intent_id="one"
            )
            assert result.order.id == 22
            observed = await client.services.orders.reconcile("one")
            assert observed.candidate.id == 22 and observed.ticker_matches is True
        assert [r.method for r in calls] == ["POST", "GET"]

    asyncio.run(run())


def test_cache_is_bypassed_after_post_submission_invalidation_failure(tmp_path):
    class FailingCache(MemoryCache):
        def __init__(self):
            super().__init__()
            self.invalidations = 0

        def invalidate_namespace(self, name):
            self.invalidations += 1
            if self.invalidations == 2:
                raise OSError("cache unavailable")
            return super().invalidate_namespace(name)

    calls = []

    def handle(request):
        calls.append(request)
        if request.method == "POST":
            return httpx.Response(200, json={"id": 17})
        return httpx.Response(200, json={"id": len([r for r in calls if r.method == "GET"])})

    with Trading212(**options(tmp_path / "journal.sqlite", handle, cache=FailingCache())) as client:
        with pytest.raises(CacheInvalidationError) as failure:
            client.services.orders.submit_market(ticker="ABC", quantity=1, intent_id="one")
        assert failure.value.order_id == 17 and failure.value.state == "CONFIRMED"
        assert client.services.account.summary().id == 1
        assert client.services.account.summary().id == 2
    assert [r.method for r in calls] == ["POST", "GET", "GET"]


def test_limiter_observation_failure_does_not_lose_rejection(tmp_path):
    class BrokenObserver(RateLimiter):
        def observe(self, operation, headers, *, limited=False):
            raise OSError("local limiter failure")

    limiter = BrokenObserver(policies={})
    calls = []

    def handle(request):
        calls.append(request)
        return httpx.Response(400)

    with Trading212(**options(tmp_path / "journal.sqlite", handle, rate_limiter=limiter)) as client:
        with pytest.raises(APIError) as failure:
            client.services.orders.submit_market(ticker="ABC", quantity=1, intent_id="one")
        assert failure.value.status_code == 400
        assert client.services.orders.intent("one").state is IntentState.REJECTED
    assert len(calls) == 1


def test_persisted_observed_id_is_available_for_reconciliation(tmp_path):
    class FailingJournal(OrderJournal):
        def finish(self, *args, **kwargs):
            if kwargs.get("state") is IntentState.CONFIRMED:
                raise OSError("disk error")
            return super().finish(*args, **kwargs)

    journal = FailingJournal(tmp_path / "journal.sqlite")

    def handle(request):
        return httpx.Response(200, json={"id": 44, "ticker": "ABC"})

    with Trading212(**options(tmp_path / "other.sqlite", handle, order_journal=journal)) as client:
        with pytest.raises(OrderOutcomeUnknownError):
            client.services.orders.submit_market(ticker="ABC", quantity=1, intent_id="one")
        entry = client.services.orders.intent("one")
        assert entry.state is IntentState.OUTCOME_UNKNOWN and entry.order_id == 44
        result = client.services.orders.reconcile("one")
        assert result.candidate_order_id == 44 and result.candidate.id == 44
        assert client.services.orders.intent("one").state is IntentState.OUTCOME_UNKNOWN


def test_default_rate_limiter_includes_mutation_policies():
    limiter = RateLimiter()
    assert limiter.policies["placeMarketOrder"] == (50, 60)
    assert limiter.policies["placeLimitOrder"] == (1, 2)


def test_async_submission_is_disabled_by_default():
    async def run():
        async with AsyncTrading212(
            "key",
            "secret",
            transport=httpx.MockTransport(lambda request: pytest.fail("unexpected network")),
        ) as client:
            with pytest.raises(TradingDisabledError):
                await client.services.orders.submit_market(
                    ticker="ABC", quantity=1, intent_id="one"
                )

    asyncio.run(run())
