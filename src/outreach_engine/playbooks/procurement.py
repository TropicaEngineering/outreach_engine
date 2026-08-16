from __future__ import annotations

from outreach_engine.domain import Decision, DecisionAction, Opportunity


FIT_TERMS = {
    "software",
    "automation",
    "workflow",
    "integration",
    "data",
    "reporting",
    "digital",
    "platform",
    "system",
    "crm",
    "api",
}

EXCLUSION_TERMS = {
    "concurrent session",
    "forgotten password",
    "new roles",
    "one login",
    "organization is now available",
    "security code",
    "verification code",
    "welcome to",
}


class ProcurementPlaybook:
    name = "procurement"
    version = "2026.08.1"

    def decide(self, opportunity: Opportunity) -> Decision:
        text = " ".join(
            [opportunity.title, opportunity.summary, str(opportunity.attributes)]
        ).casefold()
        notice_code = str(opportunity.attributes.get("notice_type_code", "")).upper()
        exclusion = next((term for term in EXCLUSION_TERMS if term in text), "")
        if exclusion:
            return Decision(
                opportunity_id=opportunity.id,
                action=DecisionAction.IGNORE,
                score=0,
                label="low",
                reason=f'Operational notification suppressed by rule: "{exclusion}".',
                target_type="none",
                playbook=self.name,
                playbook_version=self.version,
                requires_review=False,
            )
        fit_hits = sorted(term for term in FIT_TERMS if term in text)
        fit_score = min(45, len(fit_hits) * 9)
        value_bonus = 15 if opportunity.value_text else 0
        deadline_bonus = 10 if opportunity.deadline else 0

        if notice_code in {"UK6", "UK7"}:
            action = DecisionAction.PARTNER
            stage_score = 35
            target = "winner_or_prime"
            stage_reason = "The contract is awarded, so the viable route is delivery partnership."
        elif notice_code == "UK4":
            action = DecisionAction.REVIEW
            stage_score = 35
            target = "buyer"
            stage_reason = "This is a live tender and should be reviewed for a direct bid."
        elif notice_code == "UK2":
            action = DecisionAction.ENGAGE
            stage_score = 30
            target = "buyer"
            stage_reason = (
                "This is early market engagement, allowing buyer positioning before tender."
            )
        elif notice_code in {"UK1", "UK3"}:
            action = DecisionAction.MONITOR
            stage_score = 20
            target = "buyer"
            stage_reason = (
                "This is a pipeline or planning signal rather than an active opportunity."
            )
        else:
            action = DecisionAction.REVIEW
            stage_score = 10
            target = "unknown"
            stage_reason = "The procurement stage is unclear and needs human review."

        score = min(100, fit_score + value_bonus + deadline_bonus + stage_score)
        if not fit_hits and score < 50:
            action = DecisionAction.IGNORE
            target = "none"
            stage_reason += " No target capability signals were found."

        label = "high" if score >= 70 else "medium" if score >= 40 else "low"
        fit_reason = (
            f" Relevant capability signals: {', '.join(fit_hits)}."
            if fit_hits
            else ""
        )
        return Decision(
            opportunity_id=opportunity.id,
            action=action,
            score=score,
            label=label,
            reason=stage_reason + fit_reason,
            target_type=target,
            playbook=self.name,
            playbook_version=self.version,
            requires_review=action not in {DecisionAction.IGNORE, DecisionAction.MONITOR},
        )
