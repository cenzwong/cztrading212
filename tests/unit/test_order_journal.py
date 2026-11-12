import json
import sqlite3
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal

import httpx
import pytest

from cztrading212 import Trading212
from cztrading212.core.cache_policy import namespace
from cztrading212.core.safety.journal import IntentState, OrderJournal, fingerprint
from cztrading212.exceptions import DuplicateIntentError, IntentConflictError, JournalStateError

NS = namespace("demo", "example-key", "example-secret")
OTHER_NS = namespace("live", "example-key", "example-secret")
PAYLOAD = {
    "ticker": "AAPL_US_EQ",
    "quantity": Decimal("0.100000000000000001"),
    "extendedHours": False,
}


def test_prepared_claim_and_confirm_are_durable_across_instances(tmp_path):
    path = tmp_path / "journal.sqlite"
    first = OrderJournal(path)
    prepared = first.prepare(NS, "intent-1", "placeMarketOrder", PAYLOAD)
    assert prepared.state is IntentState.PREPARED
    assert "AAPL_US_EQ" not in repr(prepared)
    assert "0.100000000000000001" in prepared.payload
    reopened = OrderJournal(path)
    claimed = reopened.claim(NS, "intent-1", prepared.fingerprint)
    assert claimed.state is IntentState.SUBMITTING
    confirmed = first.finish(NS, "intent-1", state=IntentState.CONFIRMED, order_id=123)
    assert confirmed.order_id == 123 and confirmed.state is IntentState.CONFIRMED
    assert OrderJournal(path).get(NS, "intent-1") == confirmed
    assert path.stat().st_mode & 0o077 == 0
    assert b"example-secret" not in path.read_bytes()


def test_duplicate_conflict_and_demo_live_namespace_isolation(tmp_path):
    journal = OrderJournal(tmp_path / "journal.sqlite")
    first = journal.prepare(NS, "same-id", "placeMarketOrder", PAYLOAD)
    with pytest.raises(DuplicateIntentError):
        journal.prepare(NS, "same-id", "placeMarketOrder", PAYLOAD)
    with pytest.raises(IntentConflictError):
        journal.prepare(NS, "same-id", "placeMarketOrder", {**PAYLOAD, "quantity": Decimal("0.2")})
    with pytest.raises(IntentConflictError):
        journal.claim(NS, "same-id", "0" * 64)
    other = journal.prepare(OTHER_NS, "same-id", "placeMarketOrder", PAYLOAD)
    assert other.state is first.state and journal.get(OTHER_NS, "same-id") == other


def test_terminal_states_never_return_to_prepared_or_submitting(tmp_path):
    journal = OrderJournal(tmp_path / "journal.sqlite")
    for index, (state, fields) in enumerate(
        (
            (IntentState.CONFIRMED, {"order_id": 99}),
            (IntentState.REJECTED, {"status_code": 400}),
            (IntentState.OUTCOME_UNKNOWN, {"status_code": 503}),
        )
    ):
        intent = f"intent-{index}"
        entry = journal.prepare(NS, intent, "placeMarketOrder", PAYLOAD)
        journal.claim(NS, intent, entry.fingerprint)
        finished = journal.finish(NS, intent, state=state, **fields)
        assert finished.state is state
        with pytest.raises(DuplicateIntentError):
            journal.claim(NS, intent, entry.fingerprint)
        with pytest.raises(JournalStateError):
            journal.finish(NS, intent, state=IntentState.OUTCOME_UNKNOWN)


def test_cannot_claim_or_finish_invalid_states(tmp_path):
    journal = OrderJournal(tmp_path / "journal.sqlite")
    with pytest.raises(JournalStateError):
        journal.claim(NS, "missing", "0" * 64)
    entry = journal.prepare(NS, "test", "placeMarketOrder", PAYLOAD)
    with pytest.raises(JournalStateError):
        journal.finish(NS, "test", state=IntentState.OUTCOME_UNKNOWN)
    journal.claim(NS, "test", entry.fingerprint)
    for state, kwargs in (
        (IntentState.CONFIRMED, {}),
        (IntentState.REJECTED, {"status_code": 429}),
        (IntentState.CONFIRMED, {"order_id": True}),
        (IntentState.OUTCOME_UNKNOWN, {"order_id": True}),
    ):
        with pytest.raises(ValueError):
            journal.finish(NS, "test", state=state, **kwargs)
    assert journal.get(NS, "test").state is IntentState.SUBMITTING


def test_simultaneous_claims_allow_exactly_one_local_winner(tmp_path):
    path = tmp_path / "journal.sqlite"
    journal = OrderJournal(path)
    entry = journal.prepare(NS, "race", "placeMarketOrder", PAYLOAD)

    def claim(_):
        try:
            OrderJournal(path).claim(NS, "race", entry.fingerprint)
            return True
        except DuplicateIntentError:
            return False

    with ThreadPoolExecutor(max_workers=12) as pool:
        assert sum(pool.map(claim, range(20))) == 1
    assert journal.get(NS, "race").state is IntentState.SUBMITTING


def test_claim_across_processes_is_atomic(tmp_path):
    path = tmp_path / "journal.sqlite"
    journal = OrderJournal(path)
    entry = journal.prepare(NS, "process-race", "placeMarketOrder", PAYLOAD)
    program = """import sys
from cztrading212.core.safety.journal import OrderJournal
from cztrading212.exceptions import DuplicateIntentError
try:
    OrderJournal(sys.argv[1]).claim(sys.argv[2], "process-race", sys.argv[3])
    print("claimed")
except DuplicateIntentError:
    print("duplicate")
"""
    processes = [
        subprocess.Popen(
            [sys.executable, "-c", program, str(path), NS, entry.fingerprint],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        for _ in range(4)
    ]
    outcomes = [process.communicate(timeout=15) for process in processes]
    assert all(process.returncode == 0 for process in processes), outcomes
    assert sorted(output.strip() for output, _ in outcomes) == [
        "claimed",
        "duplicate",
        "duplicate",
        "duplicate",
    ]


def test_corrupt_or_incompatible_records_fail_closed(tmp_path):
    path = tmp_path / "journal.sqlite"
    journal = OrderJournal(path)
    entry = journal.prepare(NS, "intent", "placeMarketOrder", PAYLOAD)
    with sqlite3.connect(path) as connection:
        connection.execute(
            "UPDATE order_intents SET payload = ?",
            (json.dumps({"ticker": "OTHER", "quantity": 100}),),
        )
    with pytest.raises(JournalStateError, match="invalid"):
        journal.claim(NS, "intent", entry.fingerprint)
    with sqlite3.connect(path) as connection:
        connection.execute("UPDATE journal_metadata SET value = '999' WHERE key = 'schema_version'")
    with pytest.raises(JournalStateError, match="schema version"):
        OrderJournal(path)


@pytest.mark.parametrize(
    "payload",
    [
        {"ticker": "A", "quantity": 0},
        {"ticker": "A", "quantity": 0.1},
        {"ticker": "A", "quantity": True},
        {"ticker": "A", "quantity": Decimal("NaN")},
        {"ticker": "A", "quantity": 1, "secret": "x"},
        {"ticker": " A", "quantity": 1},
        {"ticker": "A", "quantity": 1, "extendedHours": 1},
    ],
)
def test_order_intent_payload_fails_closed(payload):
    with pytest.raises(ValueError):
        fingerprint("placeMarketOrder", payload)


def test_order_draft_is_exact_and_never_calls_broker():
    def unexpected(request):
        raise AssertionError("preparation must not make a network call")

    with Trading212("key", "secret", transport=httpx.MockTransport(unexpected)) as client:
        draft = client.app.trading.prepare_market(
            ticker="AAPL_US_EQ", quantity=Decimal("-0.100000000000000001")
        )
        assert draft.operation_id == "placeMarketOrder"
        assert draft.request_body()["quantity"] == Decimal("-0.100000000000000001")
        with pytest.raises(ValueError):
            client.app.trading.prepare_market(ticker="A", quantity=0.1)


def test_journal_rejects_memory_and_symlink_paths(tmp_path):
    with pytest.raises(ValueError):
        OrderJournal(":memory:")
    target = tmp_path / "target.sqlite"
    OrderJournal(target)
    link = tmp_path / "link.sqlite"
    link.symlink_to(target)
    with pytest.raises(ValueError, match="regular file"):
        OrderJournal(link)


def test_conditional_order_drafts_validate_prices_and_never_call_broker():
    def unexpected(request):
        raise AssertionError("preparation must not make a network call")

    with Trading212("key", "secret", transport=httpx.MockTransport(unexpected)) as client:
        limit = client.app.trading.prepare_limit(
            ticker="A",
            quantity=Decimal("-0.1"),
            limit_price=Decimal("100.0000000000000001"),
            time_validity="DAY",
        )
        stop = client.app.trading.prepare_stop(ticker="A", quantity=1, stop_price=10)
        stop_limit = client.app.trading.prepare_stop_limit(
            ticker="A", quantity=-1, stop_price=10, limit_price=9
        )
        assert limit.request_body() == {
            "ticker": "A",
            "quantity": Decimal("-0.1"),
            "limitPrice": Decimal("100.0000000000000001"),
            "timeValidity": "DAY",
        }
        assert stop.operation_id == "placeStopOrder_1" and stop.request_body()["stopPrice"] == 10
        assert (
            stop_limit.operation_id == "placeStopOrder"
            and stop_limit.request_body()["limitPrice"] == 9
        )
        with pytest.raises(ValueError):
            client.app.trading.prepare_limit(ticker="A", quantity=1, limit_price=0)
        with pytest.raises(ValueError):
            client.app.trading.prepare_stop(
                ticker="A", quantity=1, stop_price=10, time_validity="FOREVER"
            )
