from __future__ import annotations

from typing import Protocol

from outreach_engine.domain import Decision, Opportunity


class Playbook(Protocol):
    name: str
    version: str

    def decide(self, opportunity: Opportunity) -> Decision: ...

