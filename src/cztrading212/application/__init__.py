"""Application workflows built from services."""

from __future__ import annotations

from typing import TYPE_CHECKING

from .audit import AsyncAudit, Audit
from .cashflow import AsyncCashFlow, CashFlow
from .dividends import AsyncDividends, Dividends
from .history import AsyncHistory, History
from .instruments import AsyncInstruments, Instruments
from .performance import AsyncPerformance, Performance
from .portfolio import AsyncPortfolio, Portfolio
from .rebalancing import AsyncRebalancing, Rebalancing
from .risk import AsyncRisk, Risk
from .trading import Trading

if TYPE_CHECKING:
    from ..services import AsyncServices, Services


class Application:
    def __init__(self, services: Services) -> None:
        self.portfolio = Portfolio(services)
        self.risk = Risk(services)
        self.performance = Performance(services)
        self.dividends = Dividends(services)
        self.cashflow = CashFlow(services)
        self.instruments = Instruments(services)
        self.audit = Audit(services)
        self.rebalancing = Rebalancing(services)
        self.history = History(services)
        self.trading = Trading()


class AsyncApplication:
    def __init__(self, services: AsyncServices) -> None:
        self.portfolio = AsyncPortfolio(services)
        self.risk = AsyncRisk(services)
        self.performance = AsyncPerformance(services)
        self.dividends = AsyncDividends(services)
        self.cashflow = AsyncCashFlow(services)
        self.instruments = AsyncInstruments(services)
        self.audit = AsyncAudit(services)
        self.rebalancing = AsyncRebalancing(services)
        self.history = AsyncHistory(services)
        self.trading = Trading()
