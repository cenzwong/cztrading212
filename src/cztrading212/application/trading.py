"""Pure order preparation. This module never submits orders."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from ..core.safety.journal import validate_order_payload


@dataclass(frozen=True, slots=True, kw_only=True)
class MarketOrderDraft:
    ticker: str
    quantity: Decimal
    extended_hours: bool

    @property
    def operation_id(self) -> str:
        return "placeMarketOrder"

    def request_body(self) -> dict[str, Any]:
        return {
            "ticker": self.ticker,
            "quantity": self.quantity,
            "extendedHours": self.extended_hours,
        }


class Trading:
    """Build a reviewable order draft without making a network request."""

    def prepare_market(
        self, *, ticker: str, quantity: Decimal | int, extended_hours: bool = False
    ) -> MarketOrderDraft:
        body = {"ticker": ticker, "quantity": quantity, "extendedHours": extended_hours}
        validate_order_payload("placeMarketOrder", body)
        return MarketOrderDraft(
            ticker=ticker, quantity=Decimal(quantity), extended_hours=extended_hours
        )

    def prepare_limit(
        self,
        *,
        ticker: str,
        quantity: Decimal | int,
        limit_price: Decimal | int,
        time_validity: str | None = None,
    ) -> ConditionalOrderDraft:
        return _conditional(
            "placeLimitOrder",
            ticker=ticker,
            quantity=quantity,
            limit_price=limit_price,
            time_validity=time_validity,
        )

    def prepare_stop(
        self,
        *,
        ticker: str,
        quantity: Decimal | int,
        stop_price: Decimal | int,
        time_validity: str | None = None,
    ) -> ConditionalOrderDraft:
        return _conditional(
            "placeStopOrder_1",
            ticker=ticker,
            quantity=quantity,
            stop_price=stop_price,
            time_validity=time_validity,
        )

    def prepare_stop_limit(
        self,
        *,
        ticker: str,
        quantity: Decimal | int,
        stop_price: Decimal | int,
        limit_price: Decimal | int,
        time_validity: str | None = None,
    ) -> ConditionalOrderDraft:
        return _conditional(
            "placeStopOrder",
            ticker=ticker,
            quantity=quantity,
            stop_price=stop_price,
            limit_price=limit_price,
            time_validity=time_validity,
        )


@dataclass(frozen=True, slots=True, kw_only=True)
class ConditionalOrderDraft:
    operation_id: str
    ticker: str
    quantity: Decimal
    limit_price: Decimal | None = None
    stop_price: Decimal | None = None
    time_validity: str | None = None

    def request_body(self) -> dict[str, Any]:
        result: dict[str, Any] = {"ticker": self.ticker, "quantity": self.quantity}
        if self.limit_price is not None:
            result["limitPrice"] = self.limit_price
        if self.stop_price is not None:
            result["stopPrice"] = self.stop_price
        if self.time_validity is not None:
            result["timeValidity"] = self.time_validity
        return result


def _conditional(
    operation: str,
    *,
    ticker: str,
    quantity: Decimal | int,
    limit_price: Decimal | int | None = None,
    stop_price: Decimal | int | None = None,
    time_validity: str | None = None,
) -> ConditionalOrderDraft:
    body: dict[str, Any] = {"ticker": ticker, "quantity": quantity}
    if limit_price is not None:
        body["limitPrice"] = limit_price
    if stop_price is not None:
        body["stopPrice"] = stop_price
    if time_validity is not None:
        body["timeValidity"] = time_validity
    validate_order_payload(operation, body)
    return ConditionalOrderDraft(
        operation_id=operation,
        ticker=ticker,
        quantity=Decimal(quantity),
        limit_price=Decimal(limit_price) if limit_price is not None else None,
        stop_price=Decimal(stop_price) if stop_price is not None else None,
        time_validity=time_validity,
    )
