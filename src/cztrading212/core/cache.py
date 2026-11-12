"""TTL caches for raw responses. Persistent cache contents are financial data."""

from __future__ import annotations

import math
import os
import sqlite3
import threading
import time
from collections.abc import Callable, Generator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol


def _validate_ttl(ttl: float) -> None:
    if type(ttl) not in (int, float) or not math.isfinite(ttl):
        raise ValueError("Cache TTL must be a finite number")


@dataclass(frozen=True, slots=True, kw_only=True)
class CacheStats:
    active_entries: int
    expired_entries: int


class Cache(Protocol):
    """Cache of serialized JSON responses; TTLs are expressed in seconds."""

    def get(self, key: str) -> str | None: ...
    def put(self, key: str, value: str, ttl: float) -> None: ...
    def invalidate(self, key: str) -> None: ...
    def clear(self) -> None: ...
    def generation(self, namespace: str) -> int: ...
    def invalidate_namespace(self, namespace: str) -> None: ...
    def put_if_generation(
        self, namespace: str, generation: int, key: str, value: str, ttl: float
    ) -> bool: ...


class MemoryCache:
    """Thread-safe, process-local cache; expiry uses a monotonic clock."""

    def __init__(self, *, clock: Callable[[], float] = time.monotonic) -> None:
        self._clock = clock
        self._entries: dict[str, tuple[float, str]] = {}
        self._generations: dict[str, int] = {}
        self._lock = threading.RLock()

    def get(self, key: str) -> str | None:
        with self._lock:
            entry = self._entries.get(key)
            if entry is None:
                return None
            expires, value = entry
            if expires <= self._clock():
                del self._entries[key]
                return None
            return value

    def put(self, key: str, value: str, ttl: float) -> None:
        _validate_ttl(ttl)
        with self._lock:
            if ttl <= 0:
                self._entries.pop(key, None)
            else:
                self._entries[key] = (self._clock() + ttl, value)

    def invalidate(self, key: str) -> None:
        with self._lock:
            self._entries.pop(key, None)

    def generation(self, namespace: str) -> int:
        with self._lock:
            return self._generations.setdefault(namespace, 0)

    def invalidate_namespace(self, namespace: str) -> None:
        with self._lock:
            self._generations[namespace] = self._generations.get(namespace, 0) + 1
            prefix = f"v2:{namespace}:"
            for key in [key for key in self._entries if key.startswith(prefix)]:
                del self._entries[key]

    def put_if_generation(
        self, namespace: str, generation: int, key: str, value: str, ttl: float
    ) -> bool:
        _validate_ttl(ttl)
        with self._lock:
            if self._generations.setdefault(namespace, 0) != generation:
                return False
            self.put(key, value, ttl)
            return True

    def stats(self) -> CacheStats:
        with self._lock:
            now = self._clock()
            active = sum(expires > now for expires, _ in self._entries.values())
            return CacheStats(active_entries=active, expired_entries=len(self._entries) - active)

    def clear(self) -> None:
        with self._lock:
            self._entries.clear()
            for namespace in self._generations:
                self._generations[namespace] += 1


class SQLiteCache:
    """Opt-in persistent cache. The caller owns and protects its filesystem path.

    Connections are short-lived and SQLite coordinates threads/processes. New
    database files are created with owner-only permissions, subject to platform
    support; existing files and parent directories retain their permissions.
    """

    def __init__(self, path: str | Path, *, clock: Callable[[], float] = time.time) -> None:
        self.path = Path(path).expanduser()
        self._clock = clock
        # Do not chmod an existing user-owned file or silently create its parents.
        try:
            descriptor = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        except FileExistsError:
            pass
        else:
            os.close(descriptor)
        with self._connect() as connection:
            connection.execute(
                "CREATE TABLE IF NOT EXISTS cache_entries "
                "(key TEXT PRIMARY KEY, expires REAL NOT NULL, value TEXT NOT NULL)"
            )
            connection.execute(
                "CREATE TABLE IF NOT EXISTS cache_generations "
                "(namespace TEXT PRIMARY KEY, generation INTEGER NOT NULL)"
            )

    @contextmanager
    def _connect(self) -> Generator[sqlite3.Connection]:
        connection = sqlite3.connect(self.path, timeout=10)
        try:
            with connection:
                yield connection
        finally:
            connection.close()

    def get(self, key: str) -> str | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT expires, value FROM cache_entries WHERE key = ?", (key,)
            ).fetchone()
            if row is None:
                return None
            expires, value = row
            if expires <= self._clock():
                # A newer value may have the same expiry, so compare both fields.
                connection.execute(
                    "DELETE FROM cache_entries WHERE key = ? AND expires = ? AND value = ?",
                    (key, expires, value),
                )
                return None
            return str(value)

    def put(self, key: str, value: str, ttl: float) -> None:
        _validate_ttl(ttl)
        if ttl <= 0:
            self.invalidate(key)
            return
        with self._connect() as connection:
            connection.execute(
                "INSERT INTO cache_entries(key, expires, value) VALUES (?, ?, ?) "
                "ON CONFLICT(key) DO UPDATE SET expires=excluded.expires, value=excluded.value",
                (key, self._clock() + ttl, value),
            )

    def invalidate(self, key: str) -> None:
        with self._connect() as connection:
            connection.execute("DELETE FROM cache_entries WHERE key = ?", (key,))

    def generation(self, namespace: str) -> int:
        with self._connect() as connection:
            connection.execute(
                "INSERT OR IGNORE INTO cache_generations(namespace, generation) VALUES (?, 0)",
                (namespace,),
            )
            row = connection.execute(
                "SELECT generation FROM cache_generations WHERE namespace = ?", (namespace,)
            ).fetchone()
            return int(row[0])

    def invalidate_namespace(self, namespace: str) -> None:
        prefix = f"v2:{namespace}:"
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                "INSERT INTO cache_generations(namespace, generation) VALUES (?, 1) ON CONFLICT(namespace) DO UPDATE SET generation = generation + 1",
                (namespace,),
            )
            connection.execute(
                "DELETE FROM cache_entries WHERE substr(key, 1, ?) = ?", (len(prefix), prefix)
            )

    def put_if_generation(
        self, namespace: str, generation: int, key: str, value: str, ttl: float
    ) -> bool:
        _validate_ttl(ttl)
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                "INSERT OR IGNORE INTO cache_generations(namespace, generation) VALUES (?, 0)",
                (namespace,),
            )
            row = connection.execute(
                "SELECT generation FROM cache_generations WHERE namespace = ?", (namespace,)
            ).fetchone()
            current = int(row[0]) if row is not None else 0
            if current != generation:
                return False
            if ttl <= 0:
                connection.execute("DELETE FROM cache_entries WHERE key = ?", (key,))
            else:
                connection.execute(
                    "INSERT INTO cache_entries(key, expires, value) VALUES (?, ?, ?) ON CONFLICT(key) DO UPDATE SET expires=excluded.expires, value=excluded.value",
                    (key, self._clock() + ttl, value),
                )
            return True

    def stats(self) -> CacheStats:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT COUNT(*), COALESCE(SUM(CASE WHEN expires <= ? THEN 1 ELSE 0 END), 0) FROM cache_entries",
                (self._clock(),),
            ).fetchone()
            total, expired = row
            return CacheStats(
                active_entries=int(total) - int(expired), expired_entries=int(expired)
            )

    def clear(self) -> None:
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute("UPDATE cache_generations SET generation = generation + 1")
            connection.execute("DELETE FROM cache_entries")
