from __future__ import annotations

from typing import Protocol

from outreach_engine.domain import Opportunity, RetrievalReport, Signal


class TenderRetriever(Protocol):
    def retrieve(self, signal: Signal, opportunity: Opportunity) -> RetrievalReport: ...


class WebDiscoverer(Protocol):
    def discover(self, signal: Signal, opportunity: Opportunity) -> list[str]: ...


class NullRetriever:
    def retrieve(self, signal: Signal, opportunity: Opportunity) -> RetrievalReport:
        return RetrievalReport()
