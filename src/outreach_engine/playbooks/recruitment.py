from __future__ import annotations

from outreach_engine.domain import Decision, DecisionAction, Opportunity


SIGNALS = {
    "available": 12,
    "contract": 10,
    "cv": 15,
    "developer": 10,
    "engineer": 10,
    "experience": 8,
    "python": 10,
    "resume": 15,
    "role": 8,
}


class RecruitmentPlaybook:
    name = "recruitment"
    version = "2026.08.1"

    def decide(self, opportunity: Opportunity) -> Decision:
        text = " ".join(
            [opportunity.title, opportunity.summary, str(opportunity.attributes)]
        ).casefold()
        hits = {term: weight for term, weight in SIGNALS.items() if term in text}
        score = min(100, 15 + sum(hits.values()))
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
            "Candidate intent signals: " + ", ".join(sorted(hits)) + "."
            if hits
            else "No explicit candidate or vacancy intent was found."
        )
        return Decision(
            opportunity_id=opportunity.id,
            action=action,
            score=score,
            label=label,
            reason=reason,
            target_type=(
                "candidate"
                if action in {DecisionAction.ENGAGE, DecisionAction.REVIEW}
                else "none"
            ),
            playbook=self.name,
            playbook_version=self.version,
            requires_review=action in {DecisionAction.ENGAGE, DecisionAction.REVIEW},
        )
