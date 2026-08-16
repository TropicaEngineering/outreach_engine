from __future__ import annotations

from typing import Protocol

from outreach_engine.domain import Signal


class SignalConnector(Protocol):
    name: str

    def pull(self, limit: int = 50) -> list[Signal]: ...

