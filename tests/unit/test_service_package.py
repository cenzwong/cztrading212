"""Compatibility checks for the service package's public entry points."""

import inspect

import pytest

from cztrading212 import AsyncTrading212, Trading212
from cztrading212.services import (
    AccountService,
    AsyncAccountService,
    AsyncHistoryService,
    AsyncInstrumentsService,
    AsyncOrdersService,
    AsyncPositionsService,
    AsyncServices,
    HistoryService,
    InstrumentsService,
    OrdersService,
    PositionsService,
    ServiceResult,
    Services,
)


def test_public_exports_and_domain_ownership():
    assert ServiceResult.__module__ == "cztrading212.services._common"
    assert Services.__module__ == AsyncServices.__module__ == "cztrading212.services._facade"
    for sync, asynchronous, domain in (
        (AccountService, AsyncAccountService, "account"),
        (PositionsService, AsyncPositionsService, "positions"),
        (OrdersService, AsyncOrdersService, "orders"),
        (InstrumentsService, AsyncInstrumentsService, "instruments"),
        (HistoryService, AsyncHistoryService, "history"),
    ):
        assert sync.__module__ == asynchronous.__module__ == f"cztrading212.services.{domain}"


@pytest.mark.parametrize(
    "client_type,facade", [(Trading212, Services), (AsyncTrading212, AsyncServices)]
)
def test_client_composes_public_facade(client_type, facade):
    client = client_type(api_key="test-key", api_secret="test-secret")
    assert isinstance(client.services, facade)
    assert all(
        hasattr(client.services, name)
        for name in ("account", "positions", "orders", "instruments", "history")
    )


def test_sync_async_public_method_signatures_match():
    for sync, asynchronous in (
        (Services, AsyncServices),
        (AccountService, AsyncAccountService),
        (PositionsService, AsyncPositionsService),
        (OrdersService, AsyncOrdersService),
        (InstrumentsService, AsyncInstrumentsService),
        (HistoryService, AsyncHistoryService),
    ):
        for name, method in inspect.getmembers(sync, inspect.isfunction):
            if name.startswith("_"):
                continue
            counterpart = getattr(asynchronous, name)
            left = inspect.signature(method)
            right = inspect.signature(counterpart)
            assert [(p.name, p.kind, p.default) for p in left.parameters.values()] == [
                (p.name, p.kind, p.default) for p in right.parameters.values()
            ]
