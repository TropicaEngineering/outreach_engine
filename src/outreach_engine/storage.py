from __future__ import annotations

import json
import re
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator
from uuid import uuid4

from outreach_engine.bid_profile import DEFAULT_BID_PROFILE, normalise_bid_profile
from outreach_engine.domain import (
    Decision,
    DecisionAction,
    DocumentStatus,
    Draft,
    Evidence,
    Opportunity,
    ProcessingResult,
    ReviewStatus,
    Signal,
    SignalStatus,
    TenderDocument,
    utc_now,
)


SCHEMA_VERSION = 5

SCHEMA = """
CREATE TABLE IF NOT EXISTS schema_migrations (
    version INTEGER PRIMARY KEY,
    applied_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS signals (
    id TEXT PRIMARY KEY,
    source TEXT NOT NULL,
    external_id TEXT NOT NULL,
    subject TEXT NOT NULL,
    body TEXT NOT NULL,
    sender TEXT NOT NULL,
    recipient TEXT NOT NULL,
    received_at TEXT NOT NULL,
    source_metadata TEXT NOT NULL,
    status TEXT NOT NULL,
    error TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE(source, external_id)
);

CREATE TABLE IF NOT EXISTS opportunities (
    id TEXT PRIMARY KEY,
    signal_id TEXT NOT NULL UNIQUE REFERENCES signals(id) ON DELETE CASCADE,
    title TEXT NOT NULL,
    organization TEXT NOT NULL,
    summary TEXT NOT NULL,
    value_text TEXT NOT NULL,
    deadline TEXT NOT NULL,
    location TEXT NOT NULL,
    url TEXT NOT NULL,
    attributes TEXT NOT NULL,
    evidence TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS decisions (
    id TEXT PRIMARY KEY,
    opportunity_id TEXT NOT NULL UNIQUE REFERENCES opportunities(id) ON DELETE CASCADE,
    action TEXT NOT NULL,
    score INTEGER NOT NULL CHECK(score BETWEEN 0 AND 100),
    label TEXT NOT NULL,
    reason TEXT NOT NULL,
    target_type TEXT NOT NULL,
    playbook TEXT NOT NULL,
    playbook_version TEXT NOT NULL,
    requires_review INTEGER NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS drafts (
    id TEXT PRIMARY KEY,
    opportunity_id TEXT NOT NULL UNIQUE REFERENCES opportunities(id) ON DELETE CASCADE,
    channel TEXT NOT NULL,
    subject TEXT NOT NULL,
    body TEXT NOT NULL,
    metadata TEXT NOT NULL DEFAULT '{}',
    review_status TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS tender_documents (
    id TEXT PRIMARY KEY,
    opportunity_id TEXT NOT NULL REFERENCES opportunities(id) ON DELETE CASCADE,
    source_url TEXT NOT NULL,
    final_url TEXT NOT NULL,
    title TEXT NOT NULL,
    media_type TEXT NOT NULL,
    filename TEXT NOT NULL,
    local_path TEXT NOT NULL,
    text_content TEXT NOT NULL,
    content_hash TEXT NOT NULL,
    size_bytes INTEGER NOT NULL,
    depth INTEGER NOT NULL,
    status TEXT NOT NULL,
    error TEXT NOT NULL,
    document_role TEXT NOT NULL DEFAULT 'unclassified',
    confidence REAL NOT NULL DEFAULT 0,
    is_core INTEGER NOT NULL DEFAULT 0,
    classification_reason TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    UNIQUE(opportunity_id, source_url)
);

CREATE TABLE IF NOT EXISTS processing_runs (
    id TEXT PRIMARY KEY,
    signal_id TEXT NOT NULL REFERENCES signals(id) ON DELETE CASCADE,
    provider TEXT NOT NULL,
    model TEXT NOT NULL,
    playbook TEXT NOT NULL,
    status TEXT NOT NULL,
    error TEXT NOT NULL DEFAULT '',
    started_at TEXT NOT NULL,
    finished_at TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    entity_type TEXT NOT NULL,
    entity_id TEXT NOT NULL,
    event_type TEXT NOT NULL,
    payload TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS application_settings (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_signals_status ON signals(status);
CREATE INDEX IF NOT EXISTS idx_signals_created_at ON signals(created_at DESC);
CREATE INDEX IF NOT EXISTS idx_events_entity ON events(entity_type, entity_id, id);
CREATE INDEX IF NOT EXISTS idx_tender_documents_opportunity
    ON tender_documents(opportunity_id, status);
"""


class SQLiteRepository:
    def __init__(self, path: Path | str):
        self.path = Path(path)

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(self.path, timeout=10)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA busy_timeout = 10000")
        try:
            yield connection
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def initialize(self) -> None:
        with self.connect() as connection:
            connection.execute("PRAGMA journal_mode = WAL")
            connection.executescript(SCHEMA)
            document_columns = {
                row["name"]
                for row in connection.execute("PRAGMA table_info(tender_documents)")
            }
            migrations = {
                "document_role": (
                    "ALTER TABLE tender_documents ADD COLUMN document_role "
                    "TEXT NOT NULL DEFAULT 'unclassified'"
                ),
                "confidence": (
                    "ALTER TABLE tender_documents ADD COLUMN confidence "
                    "REAL NOT NULL DEFAULT 0"
                ),
                "is_core": (
                    "ALTER TABLE tender_documents ADD COLUMN is_core "
                    "INTEGER NOT NULL DEFAULT 0"
                ),
                "classification_reason": (
                    "ALTER TABLE tender_documents ADD COLUMN classification_reason "
                    "TEXT NOT NULL DEFAULT ''"
                ),
            }
            for column, statement in migrations.items():
                if column not in document_columns:
                    connection.execute(statement)
            draft_columns = {
                row["name"] for row in connection.execute("PRAGMA table_info(drafts)")
            }
            if "metadata" not in draft_columns:
                connection.execute(
                    "ALTER TABLE drafts ADD COLUMN metadata TEXT NOT NULL DEFAULT '{}'"
                )
            connection.execute(
                "INSERT OR IGNORE INTO schema_migrations(version, applied_at) VALUES (?, ?)",
                (SCHEMA_VERSION, utc_now()),
            )

    def get_bid_profile(self) -> dict[str, Any]:
        with self.connect() as connection:
            row = connection.execute(
                "SELECT value FROM application_settings WHERE key = 'bid_profile'"
            ).fetchone()
        if row is None:
            return dict(DEFAULT_BID_PROFILE)
        try:
            return normalise_bid_profile(json.loads(row["value"]))
        except (TypeError, ValueError, json.JSONDecodeError):
            return dict(DEFAULT_BID_PROFILE)

    def save_bid_profile(self, payload: object) -> dict[str, Any]:
        profile = normalise_bid_profile(payload)
        with self.connect() as connection:
            connection.execute(
                """
                INSERT INTO application_settings(key, value, updated_at)
                VALUES ('bid_profile', ?, ?)
                ON CONFLICT(key) DO UPDATE SET
                    value=excluded.value,
                    updated_at=excluded.updated_at
                """,
                (json.dumps(profile), utc_now()),
            )
        return profile

    def insert_signal(self, signal: Signal) -> tuple[Signal, bool]:
        now = utc_now()
        with self.connect() as connection:
            if signal.source == "gmail" and signal.external_id.startswith("find-tender:"):
                existing_canonical = connection.execute(
                    "SELECT * FROM signals WHERE source = ? AND external_id = ?",
                    (signal.source, signal.external_id),
                ).fetchone()
                if existing_canonical is not None:
                    return self._signal_from_row(existing_canonical), True
                notice_id = signal.external_id.removeprefix("find-tender:")
                legacy = connection.execute(
                    """
                    SELECT * FROM signals
                    WHERE source = 'gmail'
                      AND external_id NOT LIKE 'find-tender:%'
                      AND external_id LIKE ?
                    ORDER BY created_at DESC
                    LIMIT 1
                    """,
                    (f"%:{notice_id}",),
                ).fetchone()
                if legacy is not None:
                    connection.execute(
                        "UPDATE signals SET external_id = ?, updated_at = ? WHERE id = ?",
                        (signal.external_id, now, legacy["id"]),
                    )
                    migrated = connection.execute(
                        "SELECT * FROM signals WHERE id = ?", (legacy["id"],)
                    ).fetchone()
                    return self._signal_from_row(migrated), True
            cursor = connection.execute(
                """
                INSERT OR IGNORE INTO signals(
                    id, source, external_id, subject, body, sender, recipient,
                    received_at, source_metadata, status, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    signal.id,
                    signal.source,
                    signal.external_id,
                    signal.subject,
                    signal.body,
                    signal.sender,
                    signal.recipient,
                    signal.received_at,
                    json.dumps(signal.source_metadata),
                    signal.status.value,
                    signal.created_at,
                    now,
                ),
            )
            if cursor.rowcount == 1:
                return signal, False
            row = connection.execute(
                "SELECT * FROM signals WHERE source = ? AND external_id = ?",
                (signal.source, signal.external_id),
            ).fetchone()
            if row is None:
                raise RuntimeError("failed to retrieve duplicate signal")
            return self._signal_from_row(row), True

    def update_signal_status(
        self, signal_id: str, status: SignalStatus, error: str = ""
    ) -> None:
        with self.connect() as connection:
            connection.execute(
                "UPDATE signals SET status = ?, error = ?, updated_at = ? WHERE id = ?",
                (status.value, error[:2000], utc_now(), signal_id),
            )

    def save_opportunity(self, opportunity: Opportunity) -> None:
        with self.connect() as connection:
            existing = connection.execute(
                "SELECT id FROM opportunities WHERE signal_id = ?", (opportunity.signal_id,)
            ).fetchone()
            if existing:
                opportunity.id = existing["id"]
            connection.execute(
                """
                INSERT INTO opportunities(
                    id, signal_id, title, organization, summary, value_text,
                    deadline, location, url, attributes, evidence, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(signal_id) DO UPDATE SET
                    title=excluded.title,
                    organization=excluded.organization,
                    summary=excluded.summary,
                    value_text=excluded.value_text,
                    deadline=excluded.deadline,
                    location=excluded.location,
                    url=excluded.url,
                    attributes=excluded.attributes,
                    evidence=excluded.evidence
                """,
                (
                    opportunity.id,
                    opportunity.signal_id,
                    opportunity.title,
                    opportunity.organization,
                    opportunity.summary,
                    opportunity.value_text,
                    opportunity.deadline,
                    opportunity.location,
                    opportunity.url,
                    json.dumps(opportunity.attributes),
                    json.dumps(
                        [{"field": e.field, "quote": e.quote} for e in opportunity.evidence]
                    ),
                    opportunity.created_at,
                ),
            )

    def save_decision(self, decision: Decision) -> None:
        with self.connect() as connection:
            existing = connection.execute(
                "SELECT id FROM decisions WHERE opportunity_id = ?", (decision.opportunity_id,)
            ).fetchone()
            if existing:
                decision.id = existing["id"]
            connection.execute(
                """
                INSERT INTO decisions(
                    id, opportunity_id, action, score, label, reason, target_type,
                    playbook, playbook_version, requires_review, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(opportunity_id) DO UPDATE SET
                    action=excluded.action,
                    score=excluded.score,
                    label=excluded.label,
                    reason=excluded.reason,
                    target_type=excluded.target_type,
                    playbook=excluded.playbook,
                    playbook_version=excluded.playbook_version,
                    requires_review=excluded.requires_review,
                    created_at=excluded.created_at
                """,
                (
                    decision.id,
                    decision.opportunity_id,
                    decision.action.value,
                    decision.score,
                    decision.label,
                    decision.reason,
                    decision.target_type,
                    decision.playbook,
                    decision.playbook_version,
                    int(decision.requires_review),
                    decision.created_at,
                ),
            )

    def save_draft(self, draft: Draft) -> None:
        with self.connect() as connection:
            existing = connection.execute(
                "SELECT id, created_at FROM drafts WHERE opportunity_id = ?",
                (draft.opportunity_id,),
            ).fetchone()
            if existing:
                draft.id = existing["id"]
                draft.created_at = existing["created_at"]
            connection.execute(
                """
                INSERT INTO drafts(
                    id, opportunity_id, channel, subject, body, metadata,
                    review_status, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(opportunity_id) DO UPDATE SET
                    channel=excluded.channel,
                    subject=excluded.subject,
                    body=excluded.body,
                    metadata=excluded.metadata,
                    review_status=excluded.review_status,
                    updated_at=excluded.updated_at
                """,
                (
                    draft.id,
                    draft.opportunity_id,
                    draft.channel,
                    draft.subject,
                    draft.body,
                    json.dumps(draft.metadata),
                    draft.review_status.value,
                    draft.created_at,
                    draft.updated_at,
                ),
            )

    def delete_document(self, opportunity_id: str, document_id: str) -> bool:
        with self.connect() as connection:
            cursor = connection.execute(
                "DELETE FROM tender_documents WHERE opportunity_id = ? AND id = ?",
                (opportunity_id, document_id),
            )
            return cursor.rowcount == 1

    def replace_documents(
        self, opportunity_id: str, documents: list[TenderDocument]
    ) -> None:
        with self.connect() as connection:
            connection.execute(
                "DELETE FROM tender_documents WHERE opportunity_id = ?", (opportunity_id,)
            )
            connection.executemany(
                """
                INSERT INTO tender_documents(
                    id, opportunity_id, source_url, final_url, title, media_type,
                    filename, local_path, text_content, content_hash, size_bytes,
                    depth, status, error, document_role, confidence, is_core,
                    classification_reason, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    (
                        document.id,
                        opportunity_id,
                        document.source_url,
                        document.final_url,
                        document.title,
                        document.media_type,
                        document.filename,
                        document.local_path,
                        document.text_content,
                        document.content_hash,
                        document.size_bytes,
                        document.depth,
                        document.status.value,
                        document.error,
                        document.document_role,
                        document.confidence,
                        int(document.is_core),
                        document.classification_reason,
                        document.created_at,
                    )
                    for document in documents
                ],
            )

    def delete_draft_for_opportunity(self, opportunity_id: str) -> bool:
        with self.connect() as connection:
            cursor = connection.execute(
                "DELETE FROM drafts WHERE opportunity_id = ?", (opportunity_id,)
            )
            return cursor.rowcount == 1

    def start_run(self, signal_id: str, provider: str, model: str, playbook: str) -> str:
        run_id = str(uuid4())
        with self.connect() as connection:
            connection.execute(
                """
                INSERT INTO processing_runs(
                    id, signal_id, provider, model, playbook, status, started_at
                ) VALUES (?, ?, ?, ?, ?, 'running', ?)
                """,
                (run_id, signal_id, provider, model, playbook, utc_now()),
            )
        return run_id

    def finish_run(self, run_id: str, status: str, error: str = "") -> None:
        with self.connect() as connection:
            connection.execute(
                """
                UPDATE processing_runs SET status = ?, error = ?, finished_at = ? WHERE id = ?
                """,
                (status, error[:2000], utc_now(), run_id),
            )

    def record_event(
        self,
        entity_type: str,
        entity_id: str,
        event_type: str,
        payload: dict[str, Any] | None = None,
    ) -> None:
        with self.connect() as connection:
            connection.execute(
                """
                INSERT INTO events(entity_type, entity_id, event_type, payload, created_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (entity_type, entity_id, event_type, json.dumps(payload or {}), utc_now()),
            )

    def list_opportunities(self, limit: int = 100) -> list[dict[str, Any]]:
        with self.connect() as connection:
            rows = connection.execute(
                """
                SELECT
                    o.id, o.title, o.organization, o.deadline, o.url, o.attributes,
                    o.created_at,
                    d.action, d.score, d.label, d.reason, d.target_type,
                    dr.id AS draft_id, dr.review_status,
                    s.source, s.external_id, s.subject AS signal_subject,
                    (SELECT COUNT(*) FROM tender_documents td
                     WHERE td.opportunity_id = o.id
                       AND td.status = 'retrieved'
                       AND td.is_core = 1) AS document_count,
                    (SELECT COUNT(*) FROM tender_documents td
                     WHERE td.opportunity_id = o.id
                       AND td.status IN ('blocked', 'failed')) AS document_issue_count
                FROM opportunities o
                JOIN signals s ON s.id = o.signal_id
                LEFT JOIN decisions d ON d.opportunity_id = o.id
                LEFT JOIN drafts dr ON dr.opportunity_id = o.id
                WHERE NOT EXISTS (
                    SELECT 1 FROM signals child
                    WHERE s.source = 'gmail'
                      AND child.source = s.source
                      AND (
                          child.external_id LIKE s.external_id || ':%'
                          OR (
                              child.external_id LIKE 'find-tender:%'
                              AND json_extract(
                                  child.source_metadata, '$.parent_message_id'
                              ) = s.external_id
                          )
                          OR (
                              s.external_id NOT LIKE 'find-tender:%'
                              AND
                              s.external_id LIKE '%:______-____'
                              AND child.external_id = 'find-tender:' ||
                                  substr(s.external_id, -11)
                          )
                      )
                )
                ORDER BY o.created_at DESC
                """,
            ).fetchall()
            records = [dict(row) for row in rows]
            for record in records:
                attributes = json.loads(record.pop("attributes"))
                record["bid_pack_status"] = attributes.get(
                    "bid_pack_status", "not_run"
                )
                record["pack_access_status"] = attributes.get(
                    "pack_access_status", "not_checked"
                )
                record["pack_access_type"] = attributes.get(
                    "pack_access_type", "advert"
                )
                record["pack_access_label"] = attributes.get(
                    "pack_access_label", "Open tender advert"
                )
                record["pack_access_url"] = attributes.get(
                    "pack_access_url", record.get("url", "")
                )
            unique: dict[str, dict[str, Any]] = {}
            without_notice_id: list[dict[str, Any]] = []
            for record in records:
                match = re.search(
                    r"\b\d{6}-\d{4}\b",
                    f"{record.get('external_id', '')} {record.get('url', '')}",
                )
                if match is None:
                    without_notice_id.append(record)
                    continue
                notice_id = match.group(0)
                existing = unique.get(notice_id)
                rank = (
                    str(record.get("external_id", "")).startswith("find-tender:"),
                    int(record.get("document_count") or 0) > 0,
                    record.get("created_at", ""),
                )
                existing_rank = (
                    str(existing.get("external_id", "")).startswith("find-tender:"),
                    int(existing.get("document_count") or 0) > 0,
                    existing.get("created_at", ""),
                ) if existing else None
                if existing is None or rank > existing_rank:
                    unique[notice_id] = record
            combined = [*without_notice_id, *unique.values()]
            combined.sort(key=lambda row: row.get("created_at", ""), reverse=True)
            return combined[: max(1, min(limit, 500))]

    def get_opportunity_detail(self, opportunity_id: str) -> dict[str, Any] | None:
        with self.connect() as connection:
            row = connection.execute(
                """
                SELECT
                    o.*, s.source, s.external_id, s.subject AS signal_subject,
                    s.body AS signal_body, s.sender, s.received_at, s.status AS signal_status,
                    d.id AS decision_id, d.action, d.score, d.label, d.reason,
                    d.target_type, d.playbook, d.playbook_version,
                    dr.id AS draft_id, dr.channel, dr.subject AS draft_subject,
                    dr.body AS draft_body, dr.metadata AS draft_metadata,
                    dr.review_status
                FROM opportunities o
                JOIN signals s ON s.id = o.signal_id
                LEFT JOIN decisions d ON d.opportunity_id = o.id
                LEFT JOIN drafts dr ON dr.opportunity_id = o.id
                WHERE o.id = ?
                """,
                (opportunity_id,),
            ).fetchone()
            if row is None:
                return None
            result = dict(row)
            result["attributes"] = json.loads(result["attributes"])
            result["evidence"] = json.loads(result["evidence"])
            result["draft_metadata"] = json.loads(result.get("draft_metadata") or "{}")
            events = connection.execute(
                """
                SELECT event_type, payload, created_at FROM events
                WHERE (entity_type = 'opportunity' AND entity_id = ?)
                   OR (entity_type = 'signal' AND entity_id = ?)
                ORDER BY id
                """,
                (opportunity_id, result["signal_id"]),
            ).fetchall()
            result["events"] = [
                {**dict(event), "payload": json.loads(event["payload"])} for event in events
            ]
            document_rows = connection.execute(
                """
                SELECT id, source_url, final_url, title, media_type, filename,
                       content_hash, size_bytes, depth, status, error, created_at,
                       document_role, confidence, is_core, classification_reason,
                       substr(text_content, 1, 500) AS text_preview
                FROM tender_documents
                WHERE opportunity_id = ?
                ORDER BY status, depth, created_at
                """,
                (opportunity_id,),
            ).fetchall()
            result["documents"] = [dict(document) for document in document_rows]
            return result

    def review_draft(self, draft_id: str, status: ReviewStatus) -> bool:
        if status not in {ReviewStatus.APPROVED, ReviewStatus.REJECTED}:
            raise ValueError("review status must be approved or rejected")
        with self.connect() as connection:
            draft = connection.execute(
                "SELECT opportunity_id FROM drafts WHERE id = ?", (draft_id,)
            ).fetchone()
            if draft is None:
                return False
            cursor = connection.execute(
                "UPDATE drafts SET review_status = ?, updated_at = ? WHERE id = ?",
                (status.value, utc_now(), draft_id),
            )
            if cursor.rowcount:
                self._record_event_in_connection(
                    connection,
                    "opportunity",
                    draft["opportunity_id"],
                    f"draft.{status.value}",
                    {"draft_id": draft_id},
                )
            return cursor.rowcount == 1

    def load_result_for_signal(self, signal_id: str) -> ProcessingResult | None:
        with self.connect() as connection:
            signal_row = connection.execute(
                "SELECT * FROM signals WHERE id = ?", (signal_id,)
            ).fetchone()
            if signal_row is None:
                return None
            opportunity_row = connection.execute(
                "SELECT * FROM opportunities WHERE signal_id = ?", (signal_id,)
            ).fetchone()
            opportunity = self._opportunity_from_row(opportunity_row) if opportunity_row else None
            decision = None
            draft = None
            if opportunity:
                decision_row = connection.execute(
                    "SELECT * FROM decisions WHERE opportunity_id = ?", (opportunity.id,)
                ).fetchone()
                draft_row = connection.execute(
                    "SELECT * FROM drafts WHERE opportunity_id = ?", (opportunity.id,)
                ).fetchone()
                decision = self._decision_from_row(decision_row) if decision_row else None
                draft = self._draft_from_row(draft_row) if draft_row else None
                document_rows = connection.execute(
                    "SELECT * FROM tender_documents WHERE opportunity_id = ?",
                    (opportunity.id,),
                ).fetchall()
                documents = [self._document_from_row(row) for row in document_rows]
            else:
                documents = []
            return ProcessingResult(
                signal=self._signal_from_row(signal_row),
                opportunity=opportunity,
                decision=decision,
                draft=draft,
                documents=documents,
            )

    def load_context_for_opportunity(
        self, opportunity_id: str
    ) -> tuple[Signal, Opportunity, Decision, list[TenderDocument]] | None:
        """Load the persisted inputs required by the manual pack and draft stages."""
        with self.connect() as connection:
            opportunity_row = connection.execute(
                "SELECT * FROM opportunities WHERE id = ?", (opportunity_id,)
            ).fetchone()
            if opportunity_row is None:
                return None
            opportunity = self._opportunity_from_row(opportunity_row)
            signal_row = connection.execute(
                "SELECT * FROM signals WHERE id = ?", (opportunity.signal_id,)
            ).fetchone()
            decision_row = connection.execute(
                "SELECT * FROM decisions WHERE opportunity_id = ?", (opportunity_id,)
            ).fetchone()
            if signal_row is None or decision_row is None:
                return None
            document_rows = connection.execute(
                "SELECT * FROM tender_documents WHERE opportunity_id = ?",
                (opportunity_id,),
            ).fetchall()
            return (
                self._signal_from_row(signal_row),
                opportunity,
                self._decision_from_row(decision_row),
                [self._document_from_row(row) for row in document_rows],
            )

    @staticmethod
    def _record_event_in_connection(
        connection: sqlite3.Connection,
        entity_type: str,
        entity_id: str,
        event_type: str,
        payload: dict[str, Any],
    ) -> None:
        connection.execute(
            """
            INSERT INTO events(entity_type, entity_id, event_type, payload, created_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            (entity_type, entity_id, event_type, json.dumps(payload), utc_now()),
        )

    @staticmethod
    def _signal_from_row(row: sqlite3.Row) -> Signal:
        return Signal(
            id=row["id"],
            source=row["source"],
            external_id=row["external_id"],
            subject=row["subject"],
            body=row["body"],
            sender=row["sender"],
            recipient=row["recipient"],
            received_at=row["received_at"],
            source_metadata=json.loads(row["source_metadata"]),
            status=SignalStatus(row["status"]),
            created_at=row["created_at"],
        )

    @staticmethod
    def _opportunity_from_row(row: sqlite3.Row) -> Opportunity:
        return Opportunity(
            id=row["id"],
            signal_id=row["signal_id"],
            title=row["title"],
            organization=row["organization"],
            summary=row["summary"],
            value_text=row["value_text"],
            deadline=row["deadline"],
            location=row["location"],
            url=row["url"],
            attributes=json.loads(row["attributes"]),
            evidence=[Evidence(**item) for item in json.loads(row["evidence"])],
            created_at=row["created_at"],
        )

    @staticmethod
    def _decision_from_row(row: sqlite3.Row) -> Decision:
        return Decision(
            id=row["id"],
            opportunity_id=row["opportunity_id"],
            action=DecisionAction(row["action"]),
            score=row["score"],
            label=row["label"],
            reason=row["reason"],
            target_type=row["target_type"],
            playbook=row["playbook"],
            playbook_version=row["playbook_version"],
            requires_review=bool(row["requires_review"]),
            created_at=row["created_at"],
        )

    @staticmethod
    def _draft_from_row(row: sqlite3.Row) -> Draft:
        return Draft(
            id=row["id"],
            opportunity_id=row["opportunity_id"],
            channel=row["channel"],
            subject=row["subject"],
            body=row["body"],
            metadata=json.loads(row["metadata"] or "{}"),
            review_status=ReviewStatus(row["review_status"]),
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )

    @staticmethod
    def _document_from_row(row: sqlite3.Row) -> TenderDocument:
        return TenderDocument(
            id=row["id"],
            opportunity_id=row["opportunity_id"],
            source_url=row["source_url"],
            final_url=row["final_url"],
            title=row["title"],
            media_type=row["media_type"],
            filename=row["filename"],
            local_path=row["local_path"],
            text_content=row["text_content"],
            content_hash=row["content_hash"],
            size_bytes=row["size_bytes"],
            depth=row["depth"],
            status=DocumentStatus(row["status"]),
            error=row["error"],
            document_role=row["document_role"],
            confidence=row["confidence"],
            is_core=bool(row["is_core"]),
            classification_reason=row["classification_reason"],
            created_at=row["created_at"],
        )
