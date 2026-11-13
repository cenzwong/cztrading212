"""Service layer components."""

from __future__ import annotations

from decimal import Decimal
from typing import TYPE_CHECKING, cast

from .._generated import models
from ..application.trading import ConditionalOrderDraft, MarketOrderDraft, Trading
from ..core.safety.journal import JournalEntry
from ..core.safety.submission import (
    AsyncOrderSubmissionService,
    OrderSubmission,
    OrderSubmissionService,
    Reconciliation,
    reconciliation_result,
    reconciliation_target,
)
from ..exceptions import APIError, JournalStateError, TradingDisabledError

if TYPE_CHECKING:
    from ._facade import AsyncServices, Services


class OrdersService:
    def __init__(self, services: Services, submission: OrderSubmissionService | None) -> None:
        self._services = services
        self._submission = submission

    def _enabled(self) -> OrderSubmissionService:
        if self._submission is None:
            raise TradingDisabledError(
                "Order submission requires explicit opt-in and an order journal"
            )
        return self._submission

    def list(self, *, refresh: bool = False) -> tuple[models.Order, ...]:
        return cast(tuple[models.Order, ...], self._services.call("orders", refresh=refresh))

    def prepare_order(
        self, intent_id: str, draft: MarketOrderDraft | ConditionalOrderDraft
    ) -> JournalEntry:
        return self._enabled().prepare(intent_id, draft.operation_id, draft.request_body())

    def submit_draft(
        self, intent_id: str, draft: MarketOrderDraft | ConditionalOrderDraft
    ) -> OrderSubmission:
        return self._enabled().submit(intent_id, draft.operation_id, draft.request_body())

    def submit_market(
        self, *, ticker: str, quantity: Decimal | int, intent_id: str, extended_hours: bool = False
    ) -> OrderSubmission:
        submission = self._enabled()
        draft = Trading().prepare_market(
            ticker=ticker, quantity=quantity, extended_hours=extended_hours
        )
        return submission.submit(intent_id, draft.operation_id, draft.request_body())

    def submit_prepared(self, intent_id: str) -> OrderSubmission:
        return self._enabled().submit_prepared(intent_id)

    def intent(self, intent_id: str) -> JournalEntry | None:
        return self._enabled().entry(intent_id)

    def reconcile(self, intent_id: str, *, candidate_order_id: int | None = None) -> Reconciliation:
        entry = self._enabled().entry(intent_id)
        if entry is None:
            raise JournalStateError("Intent was not found")
        target = reconciliation_target(entry, candidate_order_id)
        if target is None:
            return reconciliation_result(entry, None, None)
        try:
            result = self._services.call_with_metadata("orderById", id=target, refresh=True)
        except APIError as exc:
            if exc.status_code != 404:
                raise
            return reconciliation_result(entry, target, None)
        return reconciliation_result(
            entry, target, cast(models.Order, result.data), result.fetched_at
        )


class AsyncOrdersService:
    def __init__(
        self, services: AsyncServices, submission: AsyncOrderSubmissionService | None
    ) -> None:
        self._services = services
        self._submission = submission

    def _enabled(self) -> AsyncOrderSubmissionService:
        if self._submission is None:
            raise TradingDisabledError(
                "Order submission requires explicit opt-in and an order journal"
            )
        return self._submission

    async def list(self, *, refresh: bool = False) -> tuple[models.Order, ...]:
        return cast(tuple[models.Order, ...], await self._services.call("orders", refresh=refresh))

    def prepare_order(
        self, intent_id: str, draft: MarketOrderDraft | ConditionalOrderDraft
    ) -> JournalEntry:
        return self._enabled().prepare(intent_id, draft.operation_id, draft.request_body())

    async def submit_draft(
        self, intent_id: str, draft: MarketOrderDraft | ConditionalOrderDraft
    ) -> OrderSubmission:
        return await self._enabled().submit(intent_id, draft.operation_id, draft.request_body())

    async def submit_market(
        self, *, ticker: str, quantity: Decimal | int, intent_id: str, extended_hours: bool = False
    ) -> OrderSubmission:
        submission = self._enabled()
        draft = Trading().prepare_market(
            ticker=ticker, quantity=quantity, extended_hours=extended_hours
        )
        return await submission.submit(intent_id, draft.operation_id, draft.request_body())

    async def submit_prepared(self, intent_id: str) -> OrderSubmission:
        return await self._enabled().submit_prepared(intent_id)

    def intent(self, intent_id: str) -> JournalEntry | None:
        return self._enabled().entry(intent_id)

    async def reconcile(
        self, intent_id: str, *, candidate_order_id: int | None = None
    ) -> Reconciliation:
        entry = self._enabled().entry(intent_id)
        if entry is None:
            raise JournalStateError("Intent was not found")
        target = reconciliation_target(entry, candidate_order_id)
        if target is None:
            return reconciliation_result(entry, None, None)
        try:
            result = await self._services.call_with_metadata("orderById", id=target, refresh=True)
        except APIError as exc:
            if exc.status_code != 404:
                raise
            return reconciliation_result(entry, target, None)
        return reconciliation_result(
            entry, target, cast(models.Order, result.data), result.fetched_at
        )
