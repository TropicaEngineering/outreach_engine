from __future__ import annotations

import json
from pathlib import Path

from outreach_engine.domain import Signal


class DirectoryConnector:
    """Import text signals and optional legacy JSON metadata from a directory."""

    name = "directory"

    def __init__(
        self,
        path: Path | str,
        metadata_path: Path | str | None = None,
        source: str = "directory",
    ):
        self.path = Path(path)
        self.metadata_path = Path(metadata_path) if metadata_path else None
        self.source = source

    def pull(self, limit: int = 50) -> list[Signal]:
        if not self.path.is_dir():
            raise NotADirectoryError(f"signal directory not found: {self.path}")
        signals: list[Signal] = []
        for text_path in sorted(self.path.glob("*.txt"))[:limit]:
            metadata = self._metadata(text_path.stem)
            signals.append(
                Signal(
                    source=self.source,
                    external_id=text_path.stem,
                    subject=str(metadata.get("email_subject", text_path.stem)),
                    body=text_path.read_text(encoding="utf-8"),
                    received_at=str(metadata.get("received_at", "")),
                    source_metadata={
                        "imported_from": str(text_path),
                        "legacy_classification": metadata.get("classification", ""),
                    },
                )
            )
        return signals

    def _metadata(self, stem: str) -> dict[str, object]:
        if not self.metadata_path:
            return {}
        path = self.metadata_path / f"{stem}.json"
        if not path.exists():
            return {}
        payload = json.loads(path.read_text(encoding="utf-8"))
        return payload if isinstance(payload, dict) else {}
