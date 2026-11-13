"""Durable, process-safe order intent journal; it never sends broker requests."""

from __future__ import annotations

import hashlib
import os
import re
import sqlite3
import stat
from collections.abc import Generator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import Decimal
from enum import Enum
from pathlib import Path
from typing import Any, cast

from ... import codec
from ...exceptions import DuplicateIntentError, IntentConflictError, JournalStateError

_ORDER_OPERATIONS = frozenset(
    {"placeMarketOrder", "placeLimitOrder", "placeStopOrder_1", "placeStopOrder"}
)
_NAMESPACE = re.compile(r"[a-f0-9]{64}\Z")
_SCHEMA_VERSION = 1


class IntentState(str, Enum):
    PREPARED = "PREPARED"
    SUBMITTING = "SUBMITTING"
    CONFIRMED = "CONFIRMED"
    REJECTED = "REJECTED"
    OUTCOME_UNKNOWN = "OUTCOME_UNKNOWN"


@dataclass(frozen=True, slots=True, kw_only=True)
class JournalEntry:
    intent_id: str
    operation: str
    fingerprint: str
    state: IntentState
    created_at: datetime
    updated_at: datetime
    order_id: int | None
    status_code: int | None
    # Intent data can reveal financial information, so never include it in repr.
    payload: str = field(repr=False)


def _validate_identity(namespace: str, intent_id: str) -> None:
    if type(namespace) is not str or not _NAMESPACE.fullmatch(namespace):
        raise ValueError("Journal namespace must be a credential-scoped digest")
    if (
        type(intent_id) is not str
        or not 1 <= len(intent_id) <= 128
        or intent_id != intent_id.strip()
        or any(ord(char) < 32 or ord(char) == 127 for char in intent_id)
    ):
        raise ValueError(
            "Intent ID must be 1–128 characters without surrounding whitespace or controls"
        )


def _canonical(value: Any) -> Any:
    if isinstance(value, dict):
        value = cast(dict[Any, Any], value)
        if not all(isinstance(k, str) for k in value):
            raise TypeError("Order payload keys must be strings")
        obj = cast(dict[str, Any], value)
        return {key: _canonical(obj[key]) for key in sorted(obj)}
    if isinstance(value, (list, tuple)):
        return [_canonical(item) for item in cast(list[Any] | tuple[Any, ...], value)]
    return value


def validate_order_payload(operation: str, payload: object) -> None:
    """Validate known order fields; never accept floats or unknown payload fields."""
    if operation not in _ORDER_OPERATIONS:
        raise ValueError("Unsupported order intent operation")
    fields = {
        "placeMarketOrder": {"ticker", "quantity", "extendedHours"},
        "placeLimitOrder": {"ticker", "quantity", "limitPrice", "timeValidity"},
        "placeStopOrder_1": {"ticker", "quantity", "stopPrice", "timeValidity"},
        "placeStopOrder": {"ticker", "quantity", "limitPrice", "stopPrice", "timeValidity"},
    }
    required = {
        "placeMarketOrder": {"ticker", "quantity"},
        "placeLimitOrder": {"ticker", "quantity", "limitPrice"},
        "placeStopOrder_1": {"ticker", "quantity", "stopPrice"},
        "placeStopOrder": {"ticker", "quantity", "limitPrice", "stopPrice"},
    }
    if not isinstance(payload, Mapping):
        raise TypeError("Order payload must be an object")
    payload = cast(Mapping[str, Any], payload)
    if not required[operation] <= payload.keys() or not payload.keys() <= fields[operation]:
        raise ValueError("Order payload has missing or unsupported fields")
    ticker = payload["ticker"]
    if (
        not isinstance(ticker, str)
        or not ticker
        or ticker != ticker.strip()
        or len(ticker) > 256
        or any(ord(char) < 32 or ord(char) == 127 for char in ticker)
    ):
        raise ValueError("Order ticker is invalid")
    for key in ("quantity", "limitPrice", "stopPrice"):
        if key not in payload:
            continue
        value = payload[key]
        if (
            isinstance(value, bool)
            or not isinstance(value, (int, Decimal))
            or not Decimal(value).is_finite()
        ):
            raise ValueError(f"{key} must be a finite Decimal or integer")
        if (key == "quantity" and value == 0) or (key != "quantity" and value <= 0):
            raise ValueError(f"{key} must be nonzero and prices must be positive")
    if "extendedHours" in payload and type(payload["extendedHours"]) is not bool:
        raise ValueError("extendedHours must be boolean")
    if "timeValidity" in payload and (
        not isinstance(payload["timeValidity"], str)
        or payload["timeValidity"] not in {"DAY", "GOOD_TILL_CANCEL"}
    ):
        raise ValueError("Unsupported order time validity")


def fingerprint(operation: str, payload: Mapping[str, Any]) -> tuple[str, str]:
    """Fingerprint the exact numeric JSON and operation; floats are rejected."""
    validate_order_payload(operation, payload)
    encoded = codec.dumps(_canonical(dict(payload)))
    digest = hashlib.sha256((operation + "\0" + encoded).encode("utf-8")).hexdigest()
    return digest, encoded


class OrderJournal:
    """Opt-in SQLite journal with transactional, one-way submission claims.

    The caller supplies the same credential-scoped namespace used by the client.
    A journal is local evidence only: it cannot prevent orders placed through
    other keys, devices, or broker interfaces.
    """

    def __init__(self, path: str | Path) -> None:
        raw_path = str(path)
        if raw_path == ":memory:" or raw_path.startswith("file:"):
            raise ValueError("A persistent journal file path is required")
        self.path = Path(path).expanduser()
        try:
            descriptor = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        except FileExistsError:
            if not stat.S_ISREG(self.path.lstat().st_mode):
                raise ValueError("Journal path must be a regular file, not a symlink") from None
        else:
            os.close(descriptor)
        with self._connect() as connection:
            connection.execute(
                "CREATE TABLE IF NOT EXISTS journal_metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL)"
            )
            row = connection.execute(
                "SELECT value FROM journal_metadata WHERE key = 'schema_version'"
            ).fetchone()
            if row is not None and row[0] != str(_SCHEMA_VERSION):
                raise JournalStateError("Unsupported order journal schema version")
            connection.execute(
                "INSERT OR IGNORE INTO journal_metadata(key, value) VALUES ('schema_version', ?)",
                (str(_SCHEMA_VERSION),),
            )
            connection.execute("""CREATE TABLE IF NOT EXISTS order_intents (
                namespace TEXT NOT NULL, intent_id TEXT NOT NULL, operation TEXT NOT NULL,
                fingerprint TEXT NOT NULL, payload TEXT NOT NULL, state TEXT NOT NULL,
                created_at TEXT NOT NULL, updated_at TEXT NOT NULL, order_id INTEGER,
                status_code INTEGER, PRIMARY KEY(namespace, intent_id))""")

    @contextmanager
    def _connect(self) -> Generator[sqlite3.Connection]:
        connection = sqlite3.connect(self.path, timeout=10)
        try:
            connection.execute("PRAGMA synchronous = FULL")
            with connection:
                yield connection
        finally:
            connection.close()

    @staticmethod
    def _entry(row: tuple[Any, ...]) -> JournalEntry:
        intent_id, operation, digest, payload, state, created, updated, order_id, status_code = row
        try:
            parsed = codec.loads(payload)
            actual, canonical = fingerprint(operation, parsed)
            if actual != digest or canonical != payload:
                raise ValueError("fingerprint mismatch")
            parsed_state = IntentState(state)
            created_at = datetime.fromisoformat(created)
            updated_at = datetime.fromisoformat(updated)
            if created_at.utcoffset() is None or updated_at.utcoffset() is None:
                raise ValueError("timezone missing")
            if parsed_state is IntentState.CONFIRMED:
                if type(order_id) is not int or not 0 < order_id < 2**63 or status_code is not None:
                    raise ValueError("invalid confirmation")
            elif parsed_state is IntentState.OUTCOME_UNKNOWN:
                if order_id is not None and (type(order_id) is not int or not 0 < order_id < 2**63):
                    raise ValueError("invalid observed order ID")
            elif order_id is not None:
                raise ValueError("unexpected order ID")
            if parsed_state is IntentState.REJECTED and status_code not in {400, 401, 403}:
                raise ValueError("invalid rejection")
            if (
                parsed_state in {IntentState.PREPARED, IntentState.SUBMITTING}
                and status_code is not None
            ):
                raise ValueError("unexpected status")
            if (
                parsed_state is IntentState.OUTCOME_UNKNOWN
                and status_code is not None
                and (type(status_code) is not int or not 100 <= status_code <= 599)
            ):
                raise ValueError("invalid status")
        except (ValueError, TypeError, KeyError, OverflowError) as exc:
            raise JournalStateError("Order journal record is invalid") from exc
        return JournalEntry(
            intent_id=intent_id,
            operation=operation,
            fingerprint=digest,
            payload=payload,
            state=parsed_state,
            created_at=created_at,
            updated_at=updated_at,
            order_id=order_id,
            status_code=status_code,
        )

    @staticmethod
    def _select(
        connection: sqlite3.Connection, namespace: str, intent_id: str
    ) -> JournalEntry | None:
        row = connection.execute(
            "SELECT intent_id, operation, fingerprint, payload, state, created_at, updated_at, order_id, status_code FROM order_intents WHERE namespace = ? AND intent_id = ?",
            (namespace, intent_id),
        ).fetchone()
        return None if row is None else OrderJournal._entry(row)

    def get(self, namespace: str, intent_id: str) -> JournalEntry | None:
        _validate_identity(namespace, intent_id)
        with self._connect() as connection:
            return self._select(connection, namespace, intent_id)

    def prepare(
        self, namespace: str, intent_id: str, operation: str, payload: Mapping[str, Any]
    ) -> JournalEntry:
        """Persist a new intent; reject both duplicates and conflicting reuse."""
        _validate_identity(namespace, intent_id)
        digest, encoded = fingerprint(operation, payload)
        now = datetime.now(UTC).isoformat()
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            existing = self._select(connection, namespace, intent_id)
            if existing is not None:
                if existing.fingerprint != digest:
                    raise IntentConflictError(
                        "Intent ID is already associated with a different order"
                    )
                raise DuplicateIntentError("Intent ID has already been recorded")
            connection.execute(
                "INSERT INTO order_intents(namespace, intent_id, operation, fingerprint, payload, state, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    namespace,
                    intent_id,
                    operation,
                    digest,
                    encoded,
                    IntentState.PREPARED.value,
                    now,
                    now,
                ),
            )
            entry = self._select(connection, namespace, intent_id)
            if entry is None:
                raise JournalStateError("Journal failed to persist prepared intent")
            return entry

    def claim(self, namespace: str, intent_id: str, expected_fingerprint: str) -> JournalEntry:
        """Atomically transition PREPARED to SUBMITTING; at most one caller wins."""
        _validate_identity(namespace, intent_id)
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            existing = self._select(connection, namespace, intent_id)
            if existing is None:
                raise JournalStateError("Intent was not found")
            if existing.fingerprint != expected_fingerprint:
                raise IntentConflictError("Intent fingerprint does not match the prepared order")
            if existing.state is not IntentState.PREPARED:
                raise DuplicateIntentError("Intent has already been claimed or completed")
            now = datetime.now(UTC).isoformat()
            connection.execute(
                "UPDATE order_intents SET state = ?, updated_at = ? WHERE namespace = ? AND intent_id = ? AND state = ?",
                (
                    IntentState.SUBMITTING.value,
                    now,
                    namespace,
                    intent_id,
                    IntentState.PREPARED.value,
                ),
            )
            entry = self._select(connection, namespace, intent_id)
            if entry is None or entry.state is not IntentState.SUBMITTING:
                raise JournalStateError("Journal could not claim intent")
            return entry

    def finish(
        self,
        namespace: str,
        intent_id: str,
        *,
        state: IntentState,
        order_id: int | None = None,
        status_code: int | None = None,
    ) -> JournalEntry:
        """Record a submission outcome; unknown outcomes can never be resubmitted."""
        _validate_identity(namespace, intent_id)
        if type(state) is not IntentState or state not in {
            IntentState.CONFIRMED,
            IntentState.REJECTED,
            IntentState.OUTCOME_UNKNOWN,
        }:
            raise ValueError("Invalid terminal submission state")
        if state is IntentState.CONFIRMED:
            if type(order_id) is not int or not 0 < order_id < 2**63 or status_code is not None:
                raise ValueError("Confirmed submissions require a positive broker order ID")
        elif state is IntentState.OUTCOME_UNKNOWN:
            if order_id is not None and (type(order_id) is not int or not 0 < order_id < 2**63):
                raise ValueError("Observed order ID must be a positive signed 64-bit integer")
        elif order_id is not None:
            raise ValueError("Rejected submissions cannot record an order ID")
        if state is IntentState.REJECTED and status_code not in {400, 401, 403}:
            raise ValueError(
                "Only documented validation and authentication failures are definitive rejections"
            )
        if (
            state is IntentState.OUTCOME_UNKNOWN
            and status_code is not None
            and (type(status_code) is not int or not 100 <= status_code <= 599)
        ):
            raise ValueError("Invalid HTTP status code")
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            existing = self._select(connection, namespace, intent_id)
            if existing is None or existing.state is not IntentState.SUBMITTING:
                raise JournalStateError("Only a submitting intent can receive an outcome")
            now = datetime.now(UTC).isoformat()
            connection.execute(
                "UPDATE order_intents SET state = ?, updated_at = ?, order_id = ?, status_code = ? WHERE namespace = ? AND intent_id = ? AND state = ?",
                (
                    state.value,
                    now,
                    order_id,
                    status_code,
                    namespace,
                    intent_id,
                    IntentState.SUBMITTING.value,
                ),
            )
            entry = self._select(connection, namespace, intent_id)
            if entry is None or entry.state is not state:
                raise JournalStateError("Journal could not persist outcome")
            return entry
