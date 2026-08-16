from __future__ import annotations

from collections.abc import Sequence
from typing import Any, Protocol

from outreach_engine.domain import Decision, Draft, Opportunity, Signal, TenderDocument


class Extractor(Protocol):
    provider_name: str
    model_name: str

    def extract(self, signal: Signal) -> Opportunity: ...


class Drafter(Protocol):
    provider_name: str
    model_name: str

    def draft(
        self,
        opportunity: Opportunity,
        decision: Decision,
        documents: Sequence[TenderDocument] = (),
        *,
        bid_profile: dict[str, Any] | None = None,
    ) -> Draft | None: ...
