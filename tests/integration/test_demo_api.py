"""Explicit opt-in demo checks. The write test may create a demo market order."""

from __future__ import annotations

import os
from decimal import Decimal

import pytest

from cztrading212 import OrderJournal, Trading212

pytestmark = pytest.mark.integration


def _credentials() -> tuple[str, str]:
    key = os.environ.get("TRADING212_DEMO_API_KEY")
    secret = os.environ.get("TRADING212_DEMO_API_SECRET")
    if not key or not secret:
        pytest.skip("Separate demo API key and secret are required")
    return key, secret


def test_demo_read_account_and_positions():
    if os.environ.get("TRADING212_RUN_DEMO_READS") != "1":
        pytest.skip("Demo read integration is opt-in")
    key, secret = _credentials()
    with Trading212(key, secret, environment="demo") as client:
        account = client.services.account.summary(refresh=True)
        positions = client.services.positions.list(refresh=True)
        assert account.currency is not None
        assert isinstance(positions, tuple)


@pytest.mark.demo_write
def test_demo_market_order_requires_separate_explicit_gate():
    if os.environ.get("TRADING212_RUN_DEMO_WRITES") != "I_ACCEPT_DEMO_ORDER":
        pytest.skip("Demo order integration requires separate explicit opt-in")
    key, secret = _credentials()
    ticker = os.environ.get("TRADING212_DEMO_TEST_TICKER")
    quantity = os.environ.get("TRADING212_DEMO_TEST_QUANTITY")
    journal_path = os.environ.get("TRADING212_DEMO_JOURNAL_PATH")
    intent_id = os.environ.get("TRADING212_DEMO_INTENT_ID")
    if not ticker or not quantity or not journal_path or not intent_id:
        pytest.skip(
            "Demo ticker, signed quantity, persistent journal path, and intent ID must be explicitly selected"
        )
    with Trading212(
        key,
        secret,
        environment="demo",
        enable_trading=True,
        order_journal=OrderJournal(journal_path),
    ) as client:
        result = client.services.orders.submit_market(
            ticker=ticker, quantity=Decimal(quantity), intent_id=intent_id
        )
        assert result.order.id is not None
        assert client.services.orders.intent(intent_id).order_id == result.order.id
