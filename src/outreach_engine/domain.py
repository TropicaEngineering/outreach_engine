from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any
from uuid import uuid4


def utc_now() -> str:
    return datetime.now(UTC).isoformat()


class SignalStatus(StrEnum):
    RECEIVED = "received"
    PROCESSING = "processing"
    PROCESSED = "processed"
    FAILED = "failed"


class DecisionAction(StrEnum):
    REVIEW = "review"
    ENGAGE = "engage"
    PARTNER = "partner"
    MONITOR = "monitor"
    IGNORE = "ignore"


class ReviewStatus(StrEnum):
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"
    NOT_REQUIRED = "not_required"


class DocumentStatus(StrEnum):
    RETRIEVED = "retrieved"
    BLOCKED = "blocked"
    FAILED = "failed"


@dataclass(slots=True)
class Signal:
    source: str
    external_id: str
    subject: str
    body: str
    sender: str = ""
    recipient: str = ""
    received_at: str = ""
    source_metadata: dict[str, Any] = field(default_factory=dict)
    id: str = field(default_factory=lambda: str(uuid4()))
    status: SignalStatus = SignalStatus.RECEIVED
    created_at: str = field(default_factory=utc_now)

    def __post_init__(self) -> None:
        if not self.source.strip():
            raise ValueError("signal source is required")
        if not self.external_id.strip():
            raise ValueError("signal external_id is required")
        if not (self.subject.strip() or self.body.strip()):
            raise ValueError("signal requires a subject or body")

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class Evidence:
    field: str
    quote: str

    def __post_init__(self) -> None:
        if not self.field or not self.quote:
            raise ValueError("evidence requires a field and quote")
        self.quote = self.quote[:500]


@dataclass(slots=True)
class Opportunity:
    signal_id: str
    title: str
    organization: str = ""
    summary: str = ""
    value_text: str = ""
    deadline: str = ""
    location: str = ""
    url: str = ""
    attributes: dict[str, Any] = field(default_factory=dict)
    evidence: list[Evidence] = field(default_factory=list)
    id: str = field(default_factory=lambda: str(uuid4()))
    created_at: str = field(default_factory=utc_now)

    def __post_init__(self) -> None:
        self.title = self.title.strip() or "Untitled opportunity"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class Decision:
    opportunity_id: str
    action: DecisionAction
    score: int
    label: str
    reason: str
    target_type: str
    playbook: str
    playbook_version: str
    requires_review: bool = True
    id: str = field(default_factory=lambda: str(uuid4()))
    created_at: str = field(default_factory=utc_now)

    def __post_init__(self) -> None:
        if not 0 <= self.score <= 100:
            raise ValueError("decision score must be between 0 and 100")
        if not self.reason.strip():
            raise ValueError("decision reason is required")

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class Draft:
    opportunity_id: str
    channel: str
    subject: str
    body: str
    metadata: dict[str, Any] = field(default_factory=dict)
    review_status: ReviewStatus = ReviewStatus.PENDING
    id: str = field(default_factory=lambda: str(uuid4()))
    created_at: str = field(default_factory=utc_now)
    updated_at: str = field(default_factory=utc_now)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class TenderDocument:
    opportunity_id: str
    source_url: str
    final_url: str = ""
    title: str = ""
    media_type: str = "application/octet-stream"
    filename: str = ""
    local_path: str = ""
    text_content: str = ""
    content_hash: str = ""
    size_bytes: int = 0
    depth: int = 0
    status: DocumentStatus = DocumentStatus.RETRIEVED
    error: str = ""
    document_role: str = "unclassified"
    confidence: float = 0.0
    is_core: bool = False
    classification_reason: str = ""
    id: str = field(default_factory=lambda: str(uuid4()))
    created_at: str = field(default_factory=utc_now)

    def to_dict(self, include_content: bool = False) -> dict[str, Any]:
        payload = asdict(self)
        if not include_content:
            payload.pop("text_content", None)
            payload.pop("local_path", None)
        return payload


@dataclass(slots=True)
class RetrievalReport:
    documents: list[TenderDocument] = field(default_factory=list)
    seed_urls: list[str] = field(default_factory=list)
    discovered_urls: int = 0
    blocked_urls: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    pack_status: str = "not_run"
    pack_confidence: float = 0.0
    portal_url: str = ""
    missing_roles: list[str] = field(default_factory=list)

    @property
    def status(self) -> str:
        retrieved = [doc for doc in self.documents if doc.status == DocumentStatus.RETRIEVED]
        if retrieved and not (self.blocked_urls or self.errors):
            return "complete"
        if retrieved:
            return "partial"
        if self.blocked_urls or self.errors:
            return "incomplete"
        return "not_run"


@dataclass(slots=True)
class ProcessingResult:
    signal: Signal
    opportunity: Opportunity | None
    decision: Decision | None
    draft: Draft | None
    documents: list[TenderDocument] = field(default_factory=list)
    duplicate: bool = False
    error: str = ""
