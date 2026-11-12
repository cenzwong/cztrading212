"""Application workflows built from services."""

from __future__ import annotations

from typing import TYPE_CHECKING

from .dividends import AsyncDividends, Dividends
from .history import AsyncHistory, History
from .portfolio import AsyncPortfolio, Portfolio
from .trading import Trading

if TYPE_CHECKING:
    from ..services import AsyncServices, Services


class Application:
    def __init__(self, services: Services) -> None:
        self.portfolio = Portfolio(services)
        self.dividends = Dividends(services)
        self.history = History(services)
        self.trading = Trading()


class AsyncApplication:
    def __init__(self, services: AsyncServices) -> None:
        self.portfolio = AsyncPortfolio(services)
        self.dividends = AsyncDividends(services)
        self.history = AsyncHistory(services)
        self.trading = Trading()
