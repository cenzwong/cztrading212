"""Service layer components."""

from __future__ import annotations

from typing import TYPE_CHECKING, cast

from .._generated import models

if TYPE_CHECKING:
    from ._facade import AsyncServices, Services


class PositionsService:
    def __init__(self, services: Services) -> None:
        self._services = services

    def list(
        self, *, ticker: str | None = None, refresh: bool = False
    ) -> tuple[models.Position, ...]:
        return cast(
            tuple[models.Position, ...],
            self._services.call("getPositions", ticker=ticker, refresh=refresh),
        )


class AsyncPositionsService:
    def __init__(self, services: AsyncServices) -> None:
        self._services = services

    async def list(
        self, *, ticker: str | None = None, refresh: bool = False
    ) -> tuple[models.Position, ...]:
        return cast(
            tuple[models.Position, ...],
            await self._services.call("getPositions", ticker=ticker, refresh=refresh),
        )
