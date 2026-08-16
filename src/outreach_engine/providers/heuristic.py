from __future__ import annotations

import re
from collections.abc import Sequence
from typing import Any

from outreach_engine.domain import (
    Decision,
    DecisionAction,
    Draft,
    Evidence,
    Opportunity,
    ReviewStatus,
    Signal,
    TenderDocument,
)


FIELD_ALIASES = {
    "title": {"title", "opportunity", "contract"},
    "organization": {"buyer", "organisation", "organization", "company"},
    "value_text": {"value", "budget", "contract value"},
    "deadline": {"deadline", "closing date", "response date"},
    "location": {"location", "region"},
    "url": {"link", "url", "notice url"},
    "notice_type": {"notice type", "notice"},
}


class HeuristicExtractor:
    """Deterministic extractor used for demos, tests, and model fallback workflows."""

    provider_name = "heuristic"
    model_name = "rules-v1"

    def extract(self, signal: Signal) -> Opportunity:
        values: dict[str, str] = {}
        evidence: list[Evidence] = []
        for raw_line in signal.body.splitlines():
            line = raw_line.strip()
            if ":" not in line:
                continue
            raw_key, raw_value = line.split(":", 1)
            key = raw_key.strip().casefold()
            value = raw_value.strip()
            if not value:
                continue
            canonical = next(
                (name for name, aliases in FIELD_ALIASES.items() if key in aliases), None
            )
            if canonical and canonical not in values:
                values[canonical] = value
                evidence.append(Evidence(field=canonical, quote=line))

        notice = values.get("notice_type", "")
        code_match = re.search(r"\bUK(?:1|2|3|4|6|7)\b", notice + " " + signal.body, re.I)
        notice_code = code_match.group(0).upper() if code_match else ""
        summary_lines = [
            line.strip()
            for line in signal.body.splitlines()
            if line.strip() and ":" not in line
        ]
        summary = " ".join(summary_lines)[:1200]
        if not summary:
            summary = signal.body.strip()[:1200]

        return Opportunity(
            signal_id=signal.id,
            title=values.get("title", signal.subject),
            organization=values.get("organization", ""),
            summary=summary,
            value_text=values.get("value_text", ""),
            deadline=values.get("deadline", ""),
            location=values.get("location", ""),
            url=values.get("url", ""),
            attributes={
                "notice_type": notice,
                "notice_type_code": notice_code,
                "source": signal.source,
            },
            evidence=evidence,
        )


class TemplateDrafter:
    provider_name = "template"
    model_name = "templates-v1"

    def draft(
        self,
        opportunity: Opportunity,
        decision: Decision,
        documents: Sequence[TenderDocument] = (),
        *,
        bid_profile: dict[str, Any] | None = None,
    ) -> Draft | None:
        if decision.action in {DecisionAction.IGNORE, DecisionAction.MONITOR}:
            return None

        organization = opportunity.organization or "your team"
        if decision.action == DecisionAction.PARTNER:
            subject = f"Delivery support for {opportunity.title}"
            middle = (
                "The delivery requirements look close to the workflow, integration and "
                "operational systems work we take on. I wanted to ask whether there is "
                "scope for a specialist delivery partner."
            )
        elif decision.action == DecisionAction.ENGAGE:
            subject = f"Early thoughts on {opportunity.title}"
            middle = (
                "The signal suggests the requirements are still taking shape. We have "
                "relevant experience turning operational needs into practical software "
                "and could share a concise delivery perspective."
            )
        else:
            subject = f"Regarding {opportunity.title}"
            middle = (
                "The opportunity appears relevant to our software, workflow and integration "
                "experience. We are reviewing the detail and wanted to establish the right "
                "route for an initial conversation."
            )
        body = (
            f"Hello {organization},\n\n"
            f"I came across {opportunity.title}. {middle}\n\n"
            "Would a short conversation next week be useful?\n\n"
            "Best,\nYour name"
        )
        return Draft(
            opportunity_id=opportunity.id,
            channel="email",
            subject=subject,
            body=body,
            review_status=ReviewStatus.PENDING,
        )
