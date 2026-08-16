from __future__ import annotations

import unittest

from outreach_engine.domain import DecisionAction, Opportunity
from outreach_engine.playbooks.procurement import ProcurementPlaybook
from outreach_engine.playbooks.recruitment import RecruitmentPlaybook
from outreach_engine.playbooks.universal import UniversalPlaybook


def opportunity(code: str, text: str = "workflow automation software") -> Opportunity:
    return Opportunity(
        signal_id="signal-1",
        title=text,
        summary=text,
        value_text="£100,000",
        deadline="1 September 2026",
        attributes={"notice_type_code": code},
    )


class PlaybookTests(unittest.TestCase):
    def test_procurement_routes_tender_for_bid_review(self) -> None:
        decision = ProcurementPlaybook().decide(opportunity("UK4"))
        self.assertEqual(decision.action, DecisionAction.REVIEW)
        self.assertEqual(decision.target_type, "buyer")

    def test_procurement_routes_award_to_partner(self) -> None:
        decision = ProcurementPlaybook().decide(opportunity("UK6"))
        self.assertEqual(decision.action, DecisionAction.PARTNER)
        self.assertEqual(decision.target_type, "winner_or_prime")

    def test_procurement_ignores_irrelevant_pipeline_signal(self) -> None:
        decision = ProcurementPlaybook().decide(
            opportunity("UK1", "grounds and tree maintenance")
        )
        self.assertEqual(decision.action, DecisionAction.IGNORE)
        self.assertFalse(decision.requires_review)

    def test_procurement_suppresses_security_notifications(self) -> None:
        decision = ProcurementPlaybook().decide(
            opportunity("", "Your security code for your account")
        )
        self.assertEqual(decision.action, DecisionAction.IGNORE)
        self.assertEqual(decision.score, 0)

    def test_universal_playbook_qualifies_explicit_commercial_intent(self) -> None:
        item = opportunity("", "Partner needed for software integration implementation")
        decision = UniversalPlaybook().decide(item)
        self.assertIn(decision.action, {DecisionAction.ENGAGE, DecisionAction.REVIEW})
        self.assertTrue(decision.requires_review)

    def test_recruitment_playbook_uses_same_decision_contract(self) -> None:
        item = opportunity(
            "", "Python engineer available for a contract developer role with CV"
        )
        decision = RecruitmentPlaybook().decide(item)
        self.assertEqual(decision.action, DecisionAction.ENGAGE)
        self.assertEqual(decision.target_type, "candidate")


if __name__ == "__main__":
    unittest.main()
