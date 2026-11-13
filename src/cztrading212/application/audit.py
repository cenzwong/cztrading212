"""Bounded, read-only checks over transaction and historical order records."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING, cast

from .._generated.models import (
    HistoricalOrder,
    HistoryTransactionItem,
    PaginatedResponseHistoricalOrder,
    PaginatedResponseHistoryTransactionItem,
)
from .cashflow import CATEGORIES
from .common import required, source
from .history_metadata import HistoryProvenance
from .models import SourceInfo

if TYPE_CHECKING:
    from ..services import AsyncServices, Services


@dataclass(frozen=True, slots=True, kw_only=True)
class AuditFinding:
    severity: str
    certainty: str
    code: str
    message: str
    evidence: tuple[str, ...]


@dataclass(frozen=True, slots=True, kw_only=True)
class AuditReport:
    findings: tuple[AuditFinding, ...]
    transaction_source: HistoryProvenance
    order_source: HistoryProvenance
    assembled_at: datetime
    limitations: tuple[str, ...]


@dataclass(frozen=True, slots=True, kw_only=True)
class ReconciliationResult:
    audit: AuditReport
    fully_reconciled: bool
    explanation: str


class _Audit:
    def __init__(self, limit: int, max_pages: int) -> None:
        self.limit, self.max_pages = limit, max_pages
        self.transactions: list[HistoryTransactionItem] = []
        self.orders: list[HistoricalOrder] = []
        self.transaction_pages: list[SourceInfo] = []
        self.order_pages: list[SourceInfo] = []

    def add_transactions(
        self, page: PaginatedResponseHistoryTransactionItem, info: SourceInfo
    ) -> None:
        self.transaction_pages.append(info)
        self.transactions.extend(required(page.items, "transactions.items"))

    def add_orders(self, page: PaginatedResponseHistoricalOrder, info: SourceInfo) -> None:
        self.order_pages.append(info)
        self.orders.extend(required(page.items, "orders.items"))

    def report(self) -> AuditReport:
        findings: list[AuditFinding] = []
        references: dict[str, list[int]] = defaultdict(list)
        records: dict[HistoryTransactionItem, list[int]] = defaultdict(list)
        for index, item in enumerate(self.transactions):
            location = f"transactions[{index}]"
            if item.reference:
                references[item.reference].append(index)
            records[item].append(index)
            missing = tuple(
                name
                for name, value in (
                    ("reference", item.reference),
                    ("dateTime", item.date_time),
                    ("type", item.type),
                    ("currency", item.currency),
                    ("amount", item.amount),
                )
                if value is None or value == ""
            )
            if missing:
                findings.append(
                    AuditFinding(
                        severity="warning",
                        certainty="confirmed",
                        code="missing_transaction_fields",
                        message="A transaction omits fields needed for analysis.",
                        evidence=(location, *missing),
                    )
                )
            if item.type not in CATEGORIES:
                findings.append(
                    AuditFinding(
                        severity="info",
                        certainty="confirmed",
                        code="unclassified_transaction_type",
                        message="Transaction type is not classified by cash-flow analysis.",
                        evidence=(location, f"type={item.type!r}"),
                    )
                )
        for reference, indices in sorted(references.items()):
            if len(indices) > 1:
                findings.append(
                    AuditFinding(
                        severity="warning",
                        certainty="confirmed",
                        code="duplicate_reference",
                        message="Multiple returned transactions share a reference; this does not prove duplication.",
                        evidence=(
                            f"reference={reference}",
                            *(f"transactions[{i}]" for i in indices),
                        ),
                    )
                )
        for indices in records.values():
            if len(indices) > 1:
                findings.append(
                    AuditFinding(
                        severity="warning",
                        certainty="potential",
                        code="repeated_transaction",
                        message="Identical returned transaction records may be duplicates.",
                        evidence=tuple(f"transactions[{i}]" for i in indices),
                    )
                )
        for index, item in enumerate(self.orders):
            location = f"orders[{index}]"
            if item.order is None:
                findings.append(
                    AuditFinding(
                        severity="warning",
                        certainty="confirmed",
                        code="missing_order",
                        message="A historical order item has no order object.",
                        evidence=(location,),
                    )
                )
                continue
            order = item.order
            if order.id is None:
                findings.append(
                    AuditFinding(
                        severity="warning",
                        certainty="confirmed",
                        code="missing_order_id",
                        message="A historical order omits its ID.",
                        evidence=(location,),
                    )
                )
            nested = order.instrument.ticker if order.instrument else None
            if order.ticker and nested and order.ticker != nested:
                findings.append(
                    AuditFinding(
                        severity="warning",
                        certainty="confirmed",
                        code="conflicting_order_tickers",
                        message="An order's ticker fields disagree.",
                        evidence=(
                            location,
                            f"ticker={order.ticker}",
                            f"instrument.ticker={nested}",
                        ),
                    )
                )

        def provenance(operation: str, pages: list[SourceInfo], count: int) -> HistoryProvenance:
            return HistoryProvenance(
                operation=operation,
                scope="all returned history",
                pages=tuple(pages),
                page_count=len(pages),
                record_count=count,
                limit=self.limit,
                max_pages=self.max_pages,
            )

        return AuditReport(
            findings=tuple(findings),
            transaction_source=provenance(
                "transactions", self.transaction_pages, len(self.transactions)
            ),
            order_source=provenance("orders_1", self.order_pages, len(self.orders)),
            assembled_at=datetime.now(UTC),
            limitations=(
                "Returned histories may omit broker events; page traversal does not prove completeness of account records.",
                "Order and cash records lack a reliable universal link; absence of a match is not evidence of a missing transaction.",
            ),
        )


def _reconcile(report: AuditReport) -> ReconciliationResult:
    return ReconciliationResult(
        audit=report,
        fully_reconciled=False,
        explanation="Available records do not establish a complete account ledger or universal order-to-transaction relationship.",
    )


class Audit:
    def __init__(self, services: Services) -> None:
        self._services = services

    def detect_gaps(
        self, *, limit: int = 50, max_pages: int = 20, refresh: bool = False
    ) -> AuditReport:
        audit = _Audit(limit, max_pages)
        for page in self._services.history.iter_pages(
            "transactions", limit=limit, max_pages=max_pages, refresh=refresh
        ):
            audit.add_transactions(
                cast(PaginatedResponseHistoryTransactionItem, page.data),
                source(page, operation="transactions", scope="history page"),
            )
        for page in self._services.history.iter_pages(
            "orders_1", limit=limit, max_pages=max_pages, refresh=refresh
        ):
            audit.add_orders(
                cast(PaginatedResponseHistoricalOrder, page.data),
                source(page, operation="orders_1", scope="history page"),
            )
        return audit.report()

    def reconcile(
        self, *, limit: int = 50, max_pages: int = 20, refresh: bool = False
    ) -> ReconciliationResult:
        return _reconcile(self.detect_gaps(limit=limit, max_pages=max_pages, refresh=refresh))


class AsyncAudit:
    def __init__(self, services: AsyncServices) -> None:
        self._services = services

    async def detect_gaps(
        self, *, limit: int = 50, max_pages: int = 20, refresh: bool = False
    ) -> AuditReport:
        audit = _Audit(limit, max_pages)
        async for page in self._services.history.iter_pages(
            "transactions", limit=limit, max_pages=max_pages, refresh=refresh
        ):
            audit.add_transactions(
                cast(PaginatedResponseHistoryTransactionItem, page.data),
                source(page, operation="transactions", scope="history page"),
            )
        async for page in self._services.history.iter_pages(
            "orders_1", limit=limit, max_pages=max_pages, refresh=refresh
        ):
            audit.add_orders(
                cast(PaginatedResponseHistoricalOrder, page.data),
                source(page, operation="orders_1", scope="history page"),
            )
        return audit.report()

    async def reconcile(
        self, *, limit: int = 50, max_pages: int = 20, refresh: bool = False
    ) -> ReconciliationResult:
        return _reconcile(await self.detect_gaps(limit=limit, max_pages=max_pages, refresh=refresh))
