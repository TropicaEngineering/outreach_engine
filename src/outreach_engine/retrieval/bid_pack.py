from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import unquote, urlsplit

from outreach_engine.domain import DocumentStatus, Opportunity, TenderDocument


CORE_ROLES = {
    "tender_instructions",
    "requirements",
    "response_template",
    "pricing",
    "evaluation",
    "terms",
}
PRIMARY_ROLES = {"tender_instructions", "requirements", "response_template"}
ROLE_LABELS = {
    "tender_instructions": "Tender instructions",
    "requirements": "Requirements / specification",
    "response_template": "Response template",
    "pricing": "Pricing schedule",
    "evaluation": "Evaluation criteria",
    "terms": "Terms and conditions",
}
ROLE_PATTERNS = (
    (
        "pricing",
        r"\b(pricing|price schedule|commercial schedule|cost model|schedule of rates)\b",
    ),
    (
        "response_template",
        r"\b(response template|quality questions?|questionnaire|selection questionnaire|sq)\b",
    ),
    (
        "requirements",
        r"\b(technical specification|service specification|statement of requirements|"
        r"scope of work|requirements specification|specification)\b",
    ),
    (
        "tender_instructions",
        r"\b(invitation to tender|instructions? to tenderers?|itt|tender instructions?)\b",
    ),
    (
        "evaluation",
        r"\b(evaluation|award criteria|scoring methodology|quality criteria)\b",
    ),
    (
        "terms",
        r"\b(terms and conditions|conditions of contract|contract terms|form of contract)\b",
    ),
)
PORTAL_TERMS = re.compile(
    r"(respond|tender|procurement|e[-_ ]?sourcing|procontract|in[-_ ]?tend|delta)",
    re.IGNORECASE,
)


@dataclass(slots=True)
class BidPackAssessment:
    status: str
    confidence: float
    portal_url: str = ""
    core_documents: list[TenderDocument] = field(default_factory=list)
    missing_roles: list[str] = field(default_factory=list)


class BidPackClassifier:
    """Conservatively identifies governing bid documents and rejects topic-adjacent pages."""

    def assess(
        self, opportunity: Opportunity, documents: list[TenderDocument]
    ) -> BidPackAssessment:
        portal_candidates: list[tuple[float, str]] = []
        for document in documents:
            self._classify_document(opportunity, document)
            portal = self._portal_candidate(opportunity, document)
            if portal:
                portal_candidates.append(portal)

        core = [
            document
            for document in documents
            if document.status == DocumentStatus.RETRIEVED
            and document.is_core
            and document.confidence >= 0.85
        ]
        roles = {document.document_role for document in core}
        has_primary = bool(roles & PRIMARY_ROLES)
        portal_candidates.sort(reverse=True)
        portal_url = portal_candidates[0][1] if portal_candidates else ""
        missing = [
            role
            for role in ROLE_LABELS
            if role not in roles
        ]
        if core and has_primary:
            return BidPackAssessment(
                status="found",
                confidence=max(document.confidence for document in core),
                portal_url=portal_url,
                core_documents=core,
                missing_roles=missing,
            )
        if portal_url:
            return BidPackAssessment(
                status="portal_required",
                confidence=portal_candidates[0][0],
                portal_url=portal_url,
                missing_roles=list(ROLE_LABELS),
            )
        return BidPackAssessment(
            status="not_found",
            confidence=0.0,
            missing_roles=list(ROLE_LABELS),
        )

    @staticmethod
    def _classify_document(
        opportunity: Opportunity, document: TenderDocument
    ) -> None:
        if document.status != DocumentStatus.RETRIEVED:
            document.document_role = "access_issue"
            document.confidence = 1.0
            document.is_core = False
            document.classification_reason = "The source was not retrieved."
            return
        url = document.final_url or document.source_url
        parsed = urlsplit(url)
        filename = document.filename or Path(unquote(parsed.path)).name
        name_text = " ".join(
            [filename, document.title, unquote(parsed.path)]
        ).casefold()
        uploaded = document.source_url.startswith("user-upload://")

        if "/ocdsreleasepackages/" in parsed.path.casefold():
            document.document_role = "notice_data"
            document.confidence = 1.0
            document.classification_reason = "Official structured notice data."
            return
        if document.media_type == "text/html":
            document.document_role = "portal_page"
            document.confidence = 0.9 if PORTAL_TERMS.search(url) else 0.55
            document.classification_reason = "Public portal or notice page."

        if re.search(
            r"\bcommission[\s_-]+brief(?:[\s_.-]|$)", name_text, re.IGNORECASE
        ):
            title_tokens = {
                token
                for token in re.findall(r"[a-z0-9]+", opportunity.title.casefold())
                if len(token) >= 5
            }
            title_hits = sum(token in name_text for token in title_tokens)
            required_hits = min(2, len(title_tokens))
            if required_hits and title_hits >= required_hits:
                document.document_role = "requirements"
                document.confidence = 0.95
                document.is_core = True
                document.classification_reason = (
                    "Commission brief matches the opportunity title."
                )
            else:
                document.document_role = "supporting"
                document.confidence = 0.2
                document.is_core = False
                document.classification_reason = (
                    "Commission brief appears to belong to a different role or lot."
                )
            return

        for role, pattern in ROLE_PATTERNS:
            if re.search(pattern, name_text, re.IGNORECASE):
                document.document_role = role
                document.confidence = 0.98 if uploaded else 0.92
                document.is_core = True
                document.classification_reason = (
                    f"Filename or document title identifies {ROLE_LABELS[role].lower()}."
                )
                return

        extension = Path(filename).suffix.casefold()
        if uploaded and extension in {".pdf", ".doc", ".docx", ".odt", ".rtf"}:
            document.document_role = "tender_instructions"
            document.confidence = 0.90
            document.is_core = True
            document.classification_reason = (
                "User-supplied bid-pack document; included in the response context."
            )
            return
        if document.document_role == "unclassified":
            document.document_role = "supporting"
            document.confidence = 0.35 if not uploaded else 0.7
            document.classification_reason = (
                "No high-confidence bid-pack role was found in the filename or title."
            )

    @staticmethod
    def _portal_candidate(
        opportunity: Opportunity, document: TenderDocument
    ) -> tuple[float, str] | None:
        if document.status != DocumentStatus.RETRIEVED:
            return None
        url = document.final_url or document.source_url
        parsed = urlsplit(url)
        if parsed.scheme not in {"http", "https"} or document.media_type != "text/html":
            return None
        if parsed.hostname in {
            "find-tender.service.gov.uk",
            "www.find-tender.service.gov.uk",
        }:
            return None
        haystack = f"{url} {document.title} {document.text_content[:4000]}".casefold()
        title_tokens = {
            token
            for token in re.findall(r"[a-z0-9]+", opportunity.title.casefold())
            if len(token) >= 5
        }
        title_hits = sum(token in haystack for token in title_tokens)
        if (
            not title_tokens
            or not PORTAL_TERMS.search(url)
            or title_hits < min(2, len(title_tokens))
        ):
            return None
        return 0.92, url
