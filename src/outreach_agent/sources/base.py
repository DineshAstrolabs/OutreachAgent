"""Protocols for each data source. Implementations live alongside."""

from __future__ import annotations

from typing import Protocol

from ..models import Champions, MCData, WebIntelligence


class MCSourceP(Protocol):
    def fetch(self, unified_number: str) -> MCData: ...


class WebIntelligenceSourceP(Protocol):
    """A source that contributes to WebIntelligence. Mutates the passed object.

    Sources are designed to be composable and fault-tolerant: any failure
    should be recorded in `sources_failed` and the pipeline continues.
    """

    name: str

    def enrich(self, company_name: str, web: WebIntelligence) -> None: ...


class ChampionSourceP(Protocol):
    def find(self, company_name: str) -> Champions: ...


class CRMWriterP(Protocol):
    def write(self, result) -> str:  # returns CRM record ID or URL
        ...
