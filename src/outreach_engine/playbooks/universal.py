from __future__ import annotations

from outreach_engine.domain import Decision, DecisionAction, Opportunity


POSITIVE_SIGNALS = {
    "budget": 12,
    "deadline": 8,
    "proposal": 10,
    "partner": 10,
    "integration": 12,
    "automation": 12,
    "software": 10,
    "project": 6,
    "implementation": 10,
}


class UniversalPlaybook:
    name = "universal"
    version = "2026.08.1"

    def decide(self, opportunity: Opportunity) -> Decision:
        text = " ".join(
            [opportunity.title, opportunity.summary, str(opportunity.attributes)]
        ).casefold()
        hits = {term: weight for term, weight in POSITIVE_SIGNALS.items() if term in text}
        score = min(100, 15 + sum(hits.values()))
        score += 12 if opportunity.value_text else 0
        score += 8 if opportunity.deadline else 0
        score = min(score, 100)

        if score >= 65:
            action = DecisionAction.ENGAGE
        elif score >= 40:
            action = DecisionAction.REVIEW
        elif score >= 25:
            action = DecisionAction.MONITOR
        else:
            action = DecisionAction.IGNORE

        label = "high" if score >= 70 else "medium" if score >= 40 else "low"
        reason = (
            "Qualified from explicit commercial signals: " + ", ".join(sorted(hits)) + "."
            if hits
            else "Insufficient explicit commercial intent for active outreach."
        )
        return Decision(
            opportunity_id=opportunity.id,
            action=action,
            score=score,
            label=label,
            reason=reason,
            target_type=(
                "sender"
                if action in {DecisionAction.ENGAGE, DecisionAction.REVIEW}
                else "none"
            ),
            playbook=self.name,
            playbook_version=self.version,
            requires_review=action in {DecisionAction.ENGAGE, DecisionAction.REVIEW},
        )
