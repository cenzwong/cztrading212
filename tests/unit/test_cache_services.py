import asyncio
import json
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal

import httpx
import pytest

from cztrading212 import (
    AsyncTrading212,
    MemoryCache,
    ResponseValidationError,
    SQLiteCache,
    Trading212,
)
from cztrading212.core.cache_policy import cache_key, namespace


def test_memory_expiration_and_invalidation():
    now = [10.0]
    cache = MemoryCache(clock=lambda: now[0])
    cache.put("x", "value", 5)
    assert cache.get("x") == "value"
    now[0] = 15
    assert cache.get("x") is None
    cache.put("x", "value", 5)
    cache.invalidate("x")
    assert cache.get("x") is None
    cache.put("x", "value", 0)
    assert cache.get("x") is None


def test_sqlite_persistence_precision_and_expiry(tmp_path):
    now = [100.0]
    path = tmp_path / "cache.sqlite"
    first = SQLiteCache(path, clock=lambda: now[0])
    first.put("x", '{"value":0.1234567890123456789}', 10)
    second = SQLiteCache(path, clock=lambda: now[0])
    assert second.get("x") == '{"value":0.1234567890123456789}'
    assert path.stat().st_mode & 0o077 == 0
    now[0] = 110
    assert second.get("x") is None


def test_sqlite_expired_read_does_not_delete_concurrent_same_expiry_replacement(tmp_path):
    from contextlib import contextmanager

    path = tmp_path / "cache.sqlite"
    reader = SQLiteCache(path, clock=lambda: 100)
    writer = SQLiteCache(path, clock=lambda: 99)
    writer.put("key", "old", 1)
    original_connect = reader._connect

    class ConnectionProxy:
        def __init__(self, connection):
            self.connection = connection

        def execute(self, statement, parameters=()):
            cursor = self.connection.execute(statement, parameters)
            if statement.startswith("SELECT expires, value"):

                class CursorProxy:
                    def fetchone(self):
                        row = cursor.fetchone()
                        writer.put("key", "new", 1)
                        return row

                return CursorProxy()
            return cursor

    @contextmanager
    def interleaved_connection():
        with original_connect() as connection:
            yield ConnectionProxy(connection)

    reader._connect = interleaved_connection
    assert reader.get("key") is None
    assert writer.get("key") == "new"


def test_service_cache_refresh_metadata_and_raw_bypass():
    seen = []

    def handle(request):
        seen.append(request)
        return httpx.Response(200, text='{"id":7,"totalValue":0.100000000000000001}')

    with Trading212("key", "secret", transport=httpx.MockTransport(handle)) as client:
        first = client.services.call_with_metadata("getAccountSummary")
        cached = client.services.call_with_metadata("getAccountSummary")
        assert first.data.total_value == Decimal("0.100000000000000001")
        assert not first.from_cache and cached.from_cache
        assert first.fetched_at == cached.fetched_at
        assert client.services.account.summary().id == 7
        client.raw.account.get_account_summary()
        client.services.account.summary(refresh=True)
        client.services.invalidate("getAccountSummary")
        client.services.account.summary()
    assert len(seen) == 4


def test_cache_isolates_environment_account_and_arguments(tmp_path):
    path = tmp_path / "shared.sqlite"
    seen = []

    def handle(request):
        seen.append(request)
        return httpx.Response(200, text='[{"quantity":1.25}]')

    transport = httpx.MockTransport(handle)
    for env, key, ticker in [
        ("demo", "a", None),
        ("demo", "a", None),
        ("live", "a", None),
        ("demo", "b", None),
        ("demo", "a", "AAPL"),
    ]:
        with Trading212(
            key, "secret", environment=env, transport=transport, cache=SQLiteCache(path)
        ) as client:
            assert client.services.positions.list(ticker=ticker)[0].quantity == Decimal("1.25")
    assert len(seen) == 4
    with sqlite3.connect(path) as db:
        rows = db.execute("SELECT key, value FROM cache_entries").fetchall()
    assert len(rows) == 4
    assert all("secret" not in key and "secret" not in value for key, value in rows)


def test_invalid_responses_are_not_cached_and_cached_data_is_revalidated():
    cache = MemoryCache()
    calls = []

    def handle(request):
        calls.append(request)
        return httpx.Response(200, text='{"id":true}')

    key = cache_key(namespace("demo", "key", "secret"), "getAccountSummary", {})
    with Trading212("key", "secret", cache=cache, transport=httpx.MockTransport(handle)) as client:
        for _ in range(2):
            with pytest.raises(ResponseValidationError):
                client.services.account.summary()
        assert len(calls) == 2
        cache.put(
            key, json.dumps({"fetched_at": "2026-01-01T00:00:00+00:00", "data": {"id": "bad"}}), 60
        )
        with pytest.raises(ResponseValidationError, match="invalid cached response"):
            client.services.account.summary()
        assert cache.get(key) is None
        cache.put(key, '{"fetched_at":"2026-01-01T00:00:00+00:00","data":{"id":1,"id":2}}', 60)
        with pytest.raises(ResponseValidationError, match="invalid cached response"):
            client.services.account.summary()
        assert cache.get(key) is None


def test_async_services_cache_and_refresh():
    async def run():
        seen = []

        def handle(request):
            seen.append(request)
            return httpx.Response(200, text='[{"quantity":0.125}]')

        async with AsyncTrading212(
            "key", "secret", transport=httpx.MockTransport(handle)
        ) as client:
            assert (await client.services.positions.list())[0].quantity == Decimal("0.125")
            assert (await client.services.call_with_metadata("getPositions")).from_cache
            await client.services.positions.list(refresh=True)
        assert len(seen) == 2

    asyncio.run(run())


def test_sqlite_concurrent_access(tmp_path):
    cache = SQLiteCache(tmp_path / "concurrent.sqlite")

    def work(i):
        cache.put(str(i), str(i), 60)
        return cache.get(str(i))

    with ThreadPoolExecutor(max_workers=8) as pool:
        assert list(pool.map(work, range(30))) == [str(i) for i in range(30)]


@pytest.mark.parametrize("ttl", [float("nan"), float("inf"), -1, True])
def test_invalid_service_ttl_rejected(ttl):
    with pytest.raises(ValueError, match="TTL"):
        Trading212("key", "secret", cache_ttls={"orders": ttl})


@pytest.mark.parametrize("backend", ["memory", "sqlite"])
@pytest.mark.parametrize("specific", [False, True])
def test_namespace_invalidation_rejects_inflight_stale_cache_fill(tmp_path, backend, specific):
    import threading

    started, proceed = threading.Event(), threading.Event()
    seen = []

    def handle(request):
        seen.append(request)
        number = len(seen)
        if number == 1:
            started.set()
            assert proceed.wait(timeout=5)
        return httpx.Response(200, text='{"id":' + str(number) + "}")

    if backend == "memory":
        first = second = MemoryCache()
    else:
        path = tmp_path / "shared.sqlite"
        first, second = SQLiteCache(path), SQLiteCache(path)
    with (
        Trading212("key", "secret", cache=first, transport=httpx.MockTransport(handle)) as reader,
        Trading212(
            "key", "secret", cache=second, transport=httpx.MockTransport(handle)
        ) as invalidator,
        ThreadPoolExecutor(max_workers=1) as pool,
    ):
        future = pool.submit(reader.services.account.summary)
        try:
            assert started.wait(timeout=5)
            if specific:
                invalidator.services.invalidate("getAccountSummary")
            else:
                invalidator.services.invalidate_all()
        finally:
            proceed.set()
        assert future.result(timeout=5).id == 1
        assert reader.services.account.summary().id == 2
    assert len(seen) == 2


@pytest.mark.parametrize("backend", ["memory", "sqlite"])
def test_cache_generation_scopes_invalidation_and_clear(tmp_path, backend):
    cache = MemoryCache() if backend == "memory" else SQLiteCache(tmp_path / "cache.sqlite")
    first, second = "a" * 64, "b" * 64
    first_generation = cache.generation(first)
    second_generation = cache.generation(second)
    first_key = cache_key(first, "orders", {}, first_generation)
    second_key = cache_key(second, "orders", {}, second_generation)
    assert cache.put_if_generation(first, first_generation, first_key, "first", 60)
    assert cache.put_if_generation(second, second_generation, second_key, "second", 60)
    cache.invalidate_namespace(first)
    assert cache.get(first_key) is None and cache.get(second_key) == "second"
    assert not cache.put_if_generation(first, first_generation, first_key, "stale", 60)
    cache.clear()
    assert not cache.put_if_generation(second, second_generation, second_key, "stale", 60)


@pytest.mark.parametrize("specific", [False, True])
def test_async_invalidation_does_not_cache_an_inflight_response(specific):
    async def run():
        started, proceed = asyncio.Event(), asyncio.Event()
        calls = []

        async def handle(request):
            calls.append(request)
            number = len(calls)
            if number == 1:
                started.set()
                await proceed.wait()
            return httpx.Response(200, text='{"id":' + str(number) + "}")

        async with AsyncTrading212(
            "key", "secret", transport=httpx.MockTransport(handle)
        ) as client:
            pending = asyncio.create_task(client.services.account.summary())
            await asyncio.wait_for(started.wait(), timeout=5)
            if specific:
                client.services.invalidate("getAccountSummary")
            else:
                client.services.invalidate_all()
            proceed.set()
            assert (await pending).id == 1
            assert (await client.services.account.summary()).id == 2
        assert len(calls) == 2

    asyncio.run(run())
