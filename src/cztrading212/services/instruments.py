"""Service layer components."""

from __future__ import annotations

from typing import TYPE_CHECKING, cast

from .._generated import models

if TYPE_CHECKING:
    from ._facade import AsyncServices, Services


class InstrumentsService:
    def __init__(self, services: Services) -> None:
        self._services = services

    def list(self, *, refresh: bool = False) -> tuple[models.TradableInstrument, ...]:
        return cast(
            tuple[models.TradableInstrument, ...],
            self._services.call("instruments", refresh=refresh),
        )

    def exchanges(self, *, refresh: bool = False) -> tuple[models.Exchange, ...]:
        return cast(tuple[models.Exchange, ...], self._services.call("exchanges", refresh=refresh))


class AsyncInstrumentsService:
    def __init__(self, services: AsyncServices) -> None:
        self._services = services

    async def list(self, *, refresh: bool = False) -> tuple[models.TradableInstrument, ...]:
        return cast(
            tuple[models.TradableInstrument, ...],
            await self._services.call("instruments", refresh=refresh),
        )

    async def exchanges(self, *, refresh: bool = False) -> tuple[models.Exchange, ...]:
        return cast(
            tuple[models.Exchange, ...], await self._services.call("exchanges", refresh=refresh)
        )
