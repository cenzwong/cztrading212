"""Service layer components."""

from __future__ import annotations

from typing import TYPE_CHECKING, cast

from .._generated import models

if TYPE_CHECKING:
    from ._facade import AsyncServices, Services


class AccountService:
    def __init__(self, services: Services) -> None:
        self._services = services

    def summary(self, *, refresh: bool = False) -> models.AccountSummary:
        return cast(
            models.AccountSummary, self._services.call("getAccountSummary", refresh=refresh)
        )


class AsyncAccountService:
    def __init__(self, services: AsyncServices) -> None:
        self._services = services

    async def summary(self, *, refresh: bool = False) -> models.AccountSummary:
        return cast(
            models.AccountSummary, await self._services.call("getAccountSummary", refresh=refresh)
        )
