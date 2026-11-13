"""Shared provenance for bounded history analytics."""

from __future__ import annotations

from dataclasses import dataclass

from .models import SourceInfo


@dataclass(frozen=True, slots=True, kw_only=True)
class HistoryProvenance:
    operation: str
    scope: str
    pages: tuple[SourceInfo, ...]
    page_count: int
    record_count: int
    limit: int
    max_pages: int
    completeness: str = "complete_traversal"
