from __future__ import annotations

import json
from pathlib import Path

from outreach_engine.domain import Signal


class FixtureConnector:
    name = "fixture"

    def __init__(self, path: Path | str):
        self.path = Path(path)

    def pull(self, limit: int = 50) -> list[Signal]:
        payload = json.loads(self.path.read_text(encoding="utf-8"))
        if not isinstance(payload, list):
            raise ValueError("fixture file must contain a JSON array")
        signals: list[Signal] = []
        for index, item in enumerate(payload[:limit]):
            if not isinstance(item, dict):
                raise ValueError(f"fixture item {index} must be an object")
            signals.append(
                Signal(
                    source=str(item.get("source", self.name)),
                    external_id=str(item.get("external_id", f"fixture-{index + 1}")),
                    subject=str(item.get("subject", "")),
                    body=str(item.get("body", "")),
                    sender=str(item.get("sender", "")),
                    recipient=str(item.get("recipient", "")),
                    received_at=str(item.get("received_at", "")),
                    source_metadata=dict(item.get("source_metadata", {})),
                )
            )
        return signals

