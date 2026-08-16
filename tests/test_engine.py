from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from outreach_engine.domain import (
    Decision,
    DecisionAction,
    Draft,
    RetrievalReport,
    ReviewStatus,
    Signal,
    SignalStatus,
    TenderDocument,
)
from outreach_engine.engine import OutreachEngine
from outreach_engine.playbooks.procurement import ProcurementPlaybook
from outreach_engine.providers.heuristic import HeuristicExtractor, TemplateDrafter
from outreach_engine.retrieval.web import WebTenderRetriever
from outreach_engine.retrieval.web import NoticeAccessRoute
from outreach_engine.storage import SQLiteRepository


def tender_signal(external_id: str = "message-1") -> Signal:
    return Signal(
        source="test",
        external_id=external_id,
        subject="Tender alert",
        body=(
            "Title: Workflow platform\n"
            "Notice type: UK4 Tender notice\n"
            "Buyer: Example Council\n"
            "Value: £100,000\n"
            "Deadline: 1 September 2026\n\n"
            "A workflow automation and reporting platform is required."
        ),
    )


class ExplodingDrafter:
    provider_name = "broken"
    model_name = "broken-v1"

    def draft(
        self, opportunity, decision, documents=(), *, bid_profile=None
    ) -> Draft | None:
        raise TimeoutError("draft provider timed out")


class IgnorePlaybook:
    name = "ignore"
    version = "test"

    def decide(self, opportunity) -> Decision:
        return Decision(
            opportunity_id=opportunity.id,
            action=DecisionAction.IGNORE,
            score=0,
            label="low",
            reason="Suppressed for test.",
            target_type="none",
            playbook=self.name,
            playbook_version=self.version,
            requires_review=False,
        )


class StubRetriever:
    def resolve_access_route(self, signal, opportunity) -> NoticeAccessRoute:
        return NoticeAccessRoute(
            status="resolved",
            access_type="external_portal",
            label="ProContract",
            url="https://procontract.example/tender/123",
            evidence="Respond through ProContract.",
            submission_method="Electronic submission",
            submission_url="https://procontract.example/tender/123",
            deadline="2026-09-02T12:00:00Z",
            notice_id="077312-2026",
        )

    def retrieve(self, signal, opportunity) -> RetrievalReport:
        return RetrievalReport(
            seed_urls=["https://example.test/tender"],
            discovered_urls=1,
            documents=[
                TenderDocument(
                    opportunity_id=opportunity.id,
                    source_url="https://example.test/tender",
                    final_url="https://example.test/tender",
                    title="Tender specification",
                    media_type="text/html",
                    filename="tender.html",
                    text_content="Mandatory requirement: auditable workflow.",
                    content_hash="abc123",
                    size_bytes=48,
                    document_role="requirements",
                    confidence=0.95,
                    is_core=True,
                )
            ],
            pack_status="found",
            pack_confidence=0.95,
        )


class EngineTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.database = Path(self.temp_dir.name) / "test.sqlite3"
        self.repository = SQLiteRepository(self.database)
        self.engine = OutreachEngine(
            self.repository,
            HeuristicExtractor(),
            ProcurementPlaybook(),
            TemplateDrafter(),
        )

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_end_to_end_processing_persists_reviewable_result(self) -> None:
        result = self.engine.process(tender_signal())

        self.assertEqual(result.signal.status, SignalStatus.PROCESSED)
        self.assertEqual(result.opportunity.title, "Workflow platform")
        self.assertEqual(result.decision.action.value, "review")
        self.assertGreaterEqual(result.decision.score, 70)
        self.assertEqual(result.draft.review_status, ReviewStatus.PENDING)

        rows = self.repository.list_opportunities()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["review_status"], "pending")

    def test_bid_profile_is_persisted_and_normalised(self) -> None:
        saved = self.repository.save_bid_profile(
            {
                "company_name": "Northstar Delivery",
                "tone": "British corporate casual",
                "target_discount_percent": 15,
                "experience": "Delivered a council transformation programme.",
            }
        )

        self.assertEqual(saved, self.repository.get_bid_profile())
        self.assertEqual(saved["target_discount_percent"], 15)
        self.assertEqual(saved["company_name"], "Northstar Delivery")

    def test_opportunity_bid_inputs_are_persisted_for_regeneration(self) -> None:
        parsed = self.engine.process(
            tender_signal("bid-inputs"), run_retrieval=False, run_draft=False
        )

        saved = self.engine.save_bid_inputs(
            parsed.opportunity.id,
            {
                "Relevant project examples": "Council workflow project, delivered 2025.",
                "References": "Jane Smith, Example Council.",
            },
        )
        detail = self.repository.get_opportunity_detail(parsed.opportunity.id)

        self.assertEqual(saved, detail["attributes"]["bid_inputs"])
        self.assertIn("Relevant project examples", saved)
        self.assertEqual(len(saved), 2)

    def test_duplicate_signal_is_idempotent(self) -> None:
        first = self.engine.process(tender_signal())
        second = self.engine.process(tender_signal())

        self.assertFalse(first.duplicate)
        self.assertTrue(second.duplicate)
        self.assertEqual(first.opportunity.id, second.opportunity.id)
        self.assertEqual(len(self.repository.list_opportunities()), 1)

    def test_force_reprocesses_completed_signal_without_duplication(self) -> None:
        first = self.engine.process(tender_signal())
        second = self.engine.process(tender_signal(), force=True)

        self.assertTrue(second.duplicate)
        self.assertEqual(first.opportunity.id, second.opportunity.id)
        self.assertEqual(len(self.repository.list_opportunities()), 1)

    def test_reprocessing_removes_draft_when_action_becomes_ignore(self) -> None:
        first = self.engine.process(tender_signal())
        ignore_engine = OutreachEngine(
            self.repository,
            HeuristicExtractor(),
            IgnorePlaybook(),
            TemplateDrafter(),
        )

        second = ignore_engine.process(tender_signal(), force=True)

        self.assertIsNotNone(first.draft)
        self.assertIsNone(second.draft)
        detail = self.repository.get_opportunity_detail(second.opportunity.id)
        self.assertIsNone(detail["draft_id"])

    def test_failed_signal_can_be_retried_without_duplicate_opportunity(self) -> None:
        broken_engine = OutreachEngine(
            self.repository,
            HeuristicExtractor(),
            ProcurementPlaybook(),
            ExplodingDrafter(),
        )
        with self.assertLogs("outreach_engine.engine", level="ERROR"):
            failed = broken_engine.process(tender_signal("retry-me"))
        self.assertTrue(failed.error)
        self.assertEqual(failed.signal.status, SignalStatus.FAILED)

        recovered = self.engine.process(tender_signal("retry-me"))
        self.assertFalse(recovered.error)
        self.assertEqual(recovered.signal.status, SignalStatus.PROCESSED)
        self.assertEqual(len(self.repository.list_opportunities()), 1)

    def test_draft_review_is_audited(self) -> None:
        result = self.engine.process(tender_signal())
        updated = self.repository.review_draft(result.draft.id, ReviewStatus.APPROVED)

        self.assertTrue(updated)
        detail = self.repository.get_opportunity_detail(result.opportunity.id)
        self.assertEqual(detail["review_status"], "approved")
        self.assertIn("draft.approved", [event["event_type"] for event in detail["events"]])

    def test_retrieved_tender_pack_is_persisted_and_audited(self) -> None:
        engine = OutreachEngine(
            self.repository,
            HeuristicExtractor(),
            ProcurementPlaybook(),
            TemplateDrafter(),
            retriever=StubRetriever(),
        )

        result = engine.process(tender_signal("with-pack"))

        self.assertEqual(len(result.documents), 1)
        detail = self.repository.get_opportunity_detail(result.opportunity.id)
        self.assertEqual(detail["attributes"]["retrieval_status"], "complete")
        self.assertEqual(detail["documents"][0]["title"], "Tender specification")
        self.assertIn(
            "tender_pack.retrieved", [event["event_type"] for event in detail["events"]]
        )

    def test_parse_pack_and_draft_can_run_as_independent_stages(self) -> None:
        engine = OutreachEngine(
            self.repository,
            HeuristicExtractor(),
            ProcurementPlaybook(),
            TemplateDrafter(),
            retriever=StubRetriever(),
        )

        parsed = engine.process(
            tender_signal("staged"), run_retrieval=False, run_draft=False
        )
        parsed_detail = self.repository.get_opportunity_detail(parsed.opportunity.id)

        self.assertIsNone(parsed.draft)
        self.assertEqual(parsed_detail["attributes"]["retrieval_status"], "not_run")
        self.assertEqual(parsed_detail["attributes"]["draft_status"], "not_run")

        engine.retrieve_pack(parsed.opportunity.id)
        draft = engine.create_draft(parsed.opportunity.id, require_pack=True)
        completed = self.repository.get_opportunity_detail(parsed.opportunity.id)

        self.assertIsNotNone(draft)
        self.assertEqual(completed["attributes"]["retrieval_status"], "complete")
        self.assertEqual(completed["attributes"]["draft_status"], "ready")
        self.assertEqual(len(completed["documents"]), 1)

    def test_exact_notice_route_is_persisted_without_running_pack_retrieval(self) -> None:
        engine = OutreachEngine(
            self.repository,
            HeuristicExtractor(),
            ProcurementPlaybook(),
            TemplateDrafter(),
            retriever=StubRetriever(),
        )
        parsed = engine.process(
            tender_signal("route-only"), run_retrieval=False, run_draft=False
        )

        engine.resolve_access_route(parsed.opportunity.id)
        detail = self.repository.get_opportunity_detail(parsed.opportunity.id)

        self.assertEqual(detail["attributes"]["pack_access_status"], "resolved")
        self.assertEqual(detail["attributes"]["pack_access_type"], "external_portal")
        self.assertEqual(detail["attributes"]["pack_access_label"], "ProContract")
        self.assertEqual(
            detail["attributes"]["submission_method"], "Electronic submission"
        )
        self.assertEqual(detail["deadline"], "2026-09-02T12:00:00Z")
        self.assertEqual(detail["attributes"]["bid_pack_status"], "not_run")

    def test_manual_draft_failure_preserves_pack_and_has_clean_retry_state(self) -> None:
        engine = OutreachEngine(
            self.repository,
            HeuristicExtractor(),
            ProcurementPlaybook(),
            ExplodingDrafter(),
            retriever=StubRetriever(),
        )
        parsed = engine.process(
            tender_signal("draft-failure"), run_retrieval=False, run_draft=False
        )
        engine.retrieve_pack(parsed.opportunity.id)

        with self.assertLogs("outreach_engine.engine", level="ERROR"):
            with self.assertRaises(TimeoutError):
                engine.create_draft(parsed.opportunity.id, require_pack=True)

        detail = self.repository.get_opportunity_detail(parsed.opportunity.id)
        self.assertEqual(detail["attributes"]["draft_status"], "failed")
        self.assertIn("retry", detail["attributes"]["draft_message"].casefold())
        self.assertNotIn("timeout", detail["attributes"]["draft_message"].casefold())
        self.assertEqual(len(detail["documents"]), 1)

    def test_uploaded_bid_pack_unlocks_manual_drafting(self) -> None:
        engine = OutreachEngine(
            self.repository,
            HeuristicExtractor(),
            ProcurementPlaybook(),
            TemplateDrafter(),
            retriever=WebTenderRetriever(Path(self.temp_dir.name) / "documents"),
        )
        parsed = engine.process(
            tender_signal("upload-pack"), run_retrieval=False, run_draft=False
        )

        with self.assertRaisesRegex(ValueError, "high-confidence bid pack"):
            engine.create_draft(parsed.opportunity.id, require_pack=True)

        engine.upload_bid_pack_file(
            parsed.opportunity.id,
            "Technical Specification.pdf",
            "application/pdf",
            b"%PDF-test-specification",
        )
        draft = engine.create_draft(parsed.opportunity.id, require_pack=True)
        detail = self.repository.get_opportunity_detail(parsed.opportunity.id)

        self.assertIsNotNone(draft)
        self.assertEqual(detail["attributes"]["bid_pack_status"], "found")
        self.assertEqual(detail["attributes"]["bid_pack_core_count"], 1)
        self.assertEqual(detail["documents"][0]["document_role"], "requirements")
        self.assertTrue(detail["documents"][0]["is_core"])

    def test_removing_document_clears_stale_response_and_rechecks_pack(self) -> None:
        engine = OutreachEngine(
            self.repository,
            HeuristicExtractor(),
            ProcurementPlaybook(),
            TemplateDrafter(),
            retriever=WebTenderRetriever(Path(self.temp_dir.name) / "documents"),
        )
        parsed = engine.process(
            tender_signal("remove-pack"), run_retrieval=False, run_draft=False
        )
        engine.upload_bid_pack_file(
            parsed.opportunity.id,
            "Technical Specification.pdf",
            "application/pdf",
            b"%PDF-test-specification",
        )
        engine.create_draft(parsed.opportunity.id, require_pack=True)
        before = self.repository.get_opportunity_detail(parsed.opportunity.id)

        engine.remove_bid_pack_document(
            parsed.opportunity.id, before["documents"][0]["id"]
        )
        after = self.repository.get_opportunity_detail(parsed.opportunity.id)

        self.assertEqual(after["documents"], [])
        self.assertIsNone(after["draft_id"])
        self.assertNotEqual(after["attributes"]["bid_pack_status"], "found")
        self.assertEqual(after["attributes"]["draft_status"], "not_run")

    def test_split_gmail_notice_supersedes_legacy_digest_in_queue(self) -> None:
        parent = tender_signal("gmail-message")
        parent.source = "gmail"
        child = tender_signal("gmail-message:077312-2026")
        child.source = "gmail"
        child.source_metadata = {"parent_message_id": "gmail-message"}
        canonical = tender_signal("find-tender:077312-2026")
        canonical.source = "gmail"

        self.engine.process(parent)
        self.engine.process(child)
        canonical_result = self.engine.process(canonical)

        rows = self.repository.list_opportunities()
        self.assertEqual([row["id"] for row in rows], [canonical_result.opportunity.id])
        self.assertTrue(canonical_result.duplicate)
        self.assertEqual(canonical_result.signal.external_id, "find-tender:077312-2026")

    def test_queue_hides_same_notice_seen_in_multiple_legacy_digests(self) -> None:
        first = tender_signal("legacy-digest-one")
        second = tender_signal("legacy-digest-two")
        for signal in (first, second):
            signal.source = "gmail"
            signal.body += (
                "\nURL: https://www.find-tender.service.gov.uk/Notice/077182-2026"
            )

        self.engine.process(first, run_retrieval=False, run_draft=False)
        self.engine.process(second, run_retrieval=False, run_draft=False)

        rows = self.repository.list_opportunities()
        self.assertEqual(len(rows), 1)
        self.assertIn("077182-2026", rows[0]["url"])


if __name__ == "__main__":
    unittest.main()
