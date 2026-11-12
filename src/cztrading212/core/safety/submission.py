"""Journal-backed order submission with exactly one raw HTTP attempt per intent."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any, cast

from ... import codec
from ..._generated import models, parsers
from ...exceptions import (
    APIError,
    CacheInvalidationError,
    DuplicateIntentError,
    JournalStateError,
    OrderOutcomeUnknownError,
    ResponseValidationError,
)
from ..reliability import ReadExecutor
from .journal import IntentState, JournalEntry, OrderJournal

if TYPE_CHECKING:
    from ...client import AsyncRaw, Raw


@dataclass(frozen=True, slots=True, kw_only=True)
class OrderSubmission:
    intent_id: str
    order: models.Order
    confirmed_at: datetime


@dataclass(frozen=True, slots=True, kw_only=True)
class Reconciliation:
    intent_id: str
    journal_state: IntentState
    candidate_order_id: int | None
    candidate: models.Order | None
    ticker_matches: bool | None
    observed_at: datetime
    note: str


def _prepared(journal: OrderJournal, namespace: str, intent_id: str) -> JournalEntry:
    entry = journal.get(namespace, intent_id)
    if entry is None:
        raise JournalStateError("Intent was not found")
    if entry.state is not IntentState.PREPARED:
        raise DuplicateIntentError("Intent has already been claimed or completed")
    return entry


def _parse_order(entry: JournalEntry, raw: Any, *, status_code: int) -> models.Order:
    if status_code != 200:
        raise ResponseValidationError("Order response has an undocumented success status")
    parsed = parsers.OPERATION_PARSERS[entry.operation](raw)
    if (
        not isinstance(parsed, models.Order)
        or type(parsed.id) is not int
        or not 0 < parsed.id < 2**63
    ):
        raise ValueError("Order response lacks a usable broker ID")
    body = codec.loads(entry.payload)
    ticker = body["ticker"]
    if parsed.ticker is not None and parsed.ticker != ticker:
        raise ValueError("Order response ticker conflicts with request")
    if (
        parsed.instrument is not None
        and parsed.instrument.ticker is not None
        and parsed.instrument.ticker != ticker
    ):
        raise ValueError("Order response instrument conflicts with request")
    return parsed


def reconciliation_target(entry: JournalEntry, candidate_order_id: int | None) -> int | None:
    if candidate_order_id is not None and (
        type(candidate_order_id) is not int or not 0 < candidate_order_id < 2**63
    ):
        raise ValueError("Candidate order ID must be a positive signed 64-bit integer")
    if entry.state is IntentState.CONFIRMED:
        if candidate_order_id is not None and candidate_order_id != entry.order_id:
            raise ValueError("Candidate ID conflicts with the recorded broker ID")
        return entry.order_id
    if (
        entry.state in {IntentState.PREPARED, IntentState.REJECTED}
        and candidate_order_id is not None
    ):
        raise ValueError("A candidate cannot be linked to this journal state")
    return candidate_order_id if candidate_order_id is not None else entry.order_id


def reconciliation_result(
    entry: JournalEntry,
    target: int | None,
    candidate: models.Order | None,
    observed_at: datetime | None = None,
) -> Reconciliation:
    if target is not None and candidate is not None and candidate.id != target:
        raise ResponseValidationError(
            "Reconciliation response has a missing or mismatched order ID"
        )
    ticker = None
    if candidate is not None:
        ticker = candidate.ticker or (candidate.instrument.ticker if candidate.instrument else None)
    matches = ticker == codec.loads(entry.payload)["ticker"] if ticker is not None else None
    if target is None:
        note = "No broker order ID is known; the journal state remains unchanged."
    elif candidate is None:
        note = (
            "Order was not found among pending orders; this does not establish execution outcome."
        )
    else:
        note = (
            "A pending order was observed; matching fields do not prove it belongs to this intent."
        )
    return Reconciliation(
        intent_id=entry.intent_id,
        journal_state=entry.state,
        candidate_order_id=target,
        candidate=candidate,
        ticker_matches=matches,
        observed_at=observed_at or datetime.now(UTC),
        note=note,
    )


class _BaseSubmission:
    def __init__(
        self,
        journal: OrderJournal,
        namespace: str,
        executor: ReadExecutor,
        invalidate: Callable[[], None],
        suspend_cache: Callable[[], None],
    ) -> None:
        self.journal = journal
        self.namespace = namespace
        self.executor = executor
        self._invalidate = invalidate
        self._suspend_cache = suspend_cache

    def entry(self, intent_id: str) -> JournalEntry | None:
        return self.journal.get(self.namespace, intent_id)

    def prepare(self, intent_id: str, operation: str, body: dict[str, Any]) -> JournalEntry:
        return self.journal.prepare(self.namespace, intent_id, operation, body)

    def _invalidate_cache(self) -> bool:
        try:
            self._invalidate()
            return True
        except Exception:  # noqa: BLE001 - cache errors must not conceal order outcome
            self._suspend_cache()
            return False

    def _before_claim(self, entry: JournalEntry) -> JournalEntry:
        # A failure here occurs before the submission claim and before HTTP.
        if not self._invalidate_cache():
            raise CacheInvalidationError(
                intent_id=entry.intent_id, state=IntentState.PREPARED.value
            )
        return self.journal.claim(self.namespace, entry.intent_id, entry.fingerprint)

    def _observe_error(self, entry: JournalEntry, exc: APIError) -> None:
        try:
            self.executor.limiter.observe(
                entry.operation, exc.headers, limited=exc.status_code == 429
            )
        except Exception:  # noqa: BLE001 - limiter metadata must not conceal an order outcome
            return

    def _record_failure(
        self, entry: JournalEntry, exc: BaseException, *, observed_order_id: int | None = None
    ) -> tuple[bool, bool]:
        definitive = isinstance(exc, APIError) and exc.status_code in {400, 401, 403}
        try:
            self.journal.finish(
                self.namespace,
                entry.intent_id,
                state=IntentState.REJECTED if definitive else IntentState.OUTCOME_UNKNOWN,
                order_id=observed_order_id if not definitive else None,
                status_code=exc.status_code if isinstance(exc, APIError) else None,
            )
            recorded = True
        except Exception:  # noqa: BLE001 - an unrecorded outcome must remain ambiguous
            recorded = False
        invalidated = self._invalidate_cache()
        return definitive and recorded, invalidated

    def _unknown(
        self, entry: JournalEntry, exc: BaseException, *, observed_order_id: int | None = None
    ) -> None:
        _, invalidated = self._record_failure(entry, exc, observed_order_id=observed_order_id)
        if not isinstance(exc, Exception):
            raise exc
        raise OrderOutcomeUnknownError(
            intent_id=entry.intent_id,
            status_code=exc.status_code if isinstance(exc, APIError) else None,
            observed_order_id=observed_order_id,
            cache_invalidated=invalidated,
        ) from None

    def _confirmed(self, entry: JournalEntry, order: models.Order) -> OrderSubmission:
        order_id = cast(int, order.id)
        try:
            recorded = self.journal.finish(
                self.namespace, entry.intent_id, state=IntentState.CONFIRMED, order_id=order_id
            )
        except BaseException as exc:  # noqa: BLE001 - persistence failure cannot imply a safe retry
            self._unknown(entry, exc, observed_order_id=order_id)
            raise AssertionError("unreachable") from None
        if not self._invalidate_cache():
            raise CacheInvalidationError(
                intent_id=entry.intent_id, state=recorded.state.value, order_id=order_id
            )
        return OrderSubmission(
            intent_id=entry.intent_id, order=order, confirmed_at=recorded.updated_at
        )

    def _failed(self, entry: JournalEntry, exc: BaseException) -> None:
        definitive, invalidated = self._record_failure(entry, exc)
        if not isinstance(exc, Exception):
            raise exc
        if definitive and not invalidated:
            raise CacheInvalidationError(
                intent_id=entry.intent_id, state=IntentState.REJECTED.value
            ) from None
        if definitive:
            raise exc
        raise OrderOutcomeUnknownError(
            intent_id=entry.intent_id,
            status_code=exc.status_code if isinstance(exc, APIError) else None,
            cache_invalidated=invalidated,
        ) from None


class OrderSubmissionService(_BaseSubmission):
    def __init__(
        self,
        raw: Raw,
        journal: OrderJournal,
        namespace: str,
        executor: ReadExecutor,
        invalidate: Callable[[], None],
        suspend_cache: Callable[[], None],
    ) -> None:
        super().__init__(journal, namespace, executor, invalidate, suspend_cache)
        self.raw = raw

    def submit(self, intent_id: str, operation: str, body: dict[str, Any]) -> OrderSubmission:
        self.prepare(intent_id, operation, body)
        return self.submit_prepared(intent_id)

    def submit_prepared(self, intent_id: str) -> OrderSubmission:
        entry = _prepared(self.journal, self.namespace, intent_id)
        self.executor.limiter.acquire(
            entry.operation, strategy=self.executor.strategy, max_wait=self.executor.max_wait
        )
        entry = self._before_claim(entry)
        observed_order_id: int | None = None
        try:
            response = self.raw.call_with_metadata(entry.operation, body=codec.loads(entry.payload))
            order = _parse_order(entry, response.data, status_code=response.status_code)
            observed_order_id = order.id
            self.executor.limiter.observe(entry.operation, response.headers)
        except BaseException as exc:  # noqa: BLE001 - even cancellation may leave a submitted order
            if isinstance(exc, APIError):
                self._observe_error(entry, exc)
            if observed_order_id is not None:
                self._unknown(entry, exc, observed_order_id=observed_order_id)
            self._failed(entry, exc)
            raise AssertionError("unreachable") from None
        return self._confirmed(entry, order)


class AsyncOrderSubmissionService(_BaseSubmission):
    def __init__(
        self,
        raw: AsyncRaw,
        journal: OrderJournal,
        namespace: str,
        executor: ReadExecutor,
        invalidate: Callable[[], None],
        suspend_cache: Callable[[], None],
    ) -> None:
        super().__init__(journal, namespace, executor, invalidate, suspend_cache)
        self.raw = raw

    async def submit(self, intent_id: str, operation: str, body: dict[str, Any]) -> OrderSubmission:
        self.prepare(intent_id, operation, body)
        return await self.submit_prepared(intent_id)

    async def submit_prepared(self, intent_id: str) -> OrderSubmission:
        entry = _prepared(self.journal, self.namespace, intent_id)
        await self.executor.limiter.acquire_async(
            entry.operation, strategy=self.executor.strategy, max_wait=self.executor.max_wait
        )
        entry = self._before_claim(entry)
        observed_order_id: int | None = None
        try:
            response = await self.raw.call_with_metadata(
                entry.operation, body=codec.loads(entry.payload)
            )
            order = _parse_order(entry, response.data, status_code=response.status_code)
            observed_order_id = order.id
            self.executor.limiter.observe(entry.operation, response.headers)
        except BaseException as exc:  # noqa: BLE001 - even cancellation may leave a submitted order
            if isinstance(exc, APIError):
                self._observe_error(entry, exc)
            if observed_order_id is not None:
                self._unknown(entry, exc, observed_order_id=observed_order_id)
            self._failed(entry, exc)
            raise AssertionError("unreachable") from None
        return self._confirmed(entry, order)
