from __future__ import annotations

import unittest

from outreach_engine.domain import DocumentStatus, Opportunity, TenderDocument
from outreach_engine.retrieval.bid_pack import BidPackClassifier


class BidPackClassifierTests(unittest.TestCase):
    def setUp(self) -> None:
        self.opportunity = Opportunity(
            signal_id="signal-1",
            title="Workflow Case Management Platform",
            organization="Example Council",
            url="https://notices.example/123",
        )
        self.classifier = BidPackClassifier()

    def test_identifies_governing_bid_documents_and_rejects_policy_junk(self) -> None:
        documents = [
            TenderDocument(
                opportunity_id=self.opportunity.id,
                source_url="https://portal.example/files/technical-specification.pdf",
                final_url="https://portal.example/files/technical-specification.pdf",
                filename="Technical Specification.pdf",
                media_type="application/pdf",
            ),
            TenderDocument(
                opportunity_id=self.opportunity.id,
                source_url="https://example.gov/policies/privacy.html",
                final_url="https://example.gov/policies/privacy.html",
                filename="privacy.html",
                title="Privacy policy",
                media_type="text/html",
            ),
        ]

        assessment = self.classifier.assess(self.opportunity, documents)

        self.assertEqual(assessment.status, "found")
        self.assertEqual([doc.document_role for doc in assessment.core_documents], [
            "requirements"
        ])
        self.assertFalse(documents[1].is_core)
        self.assertEqual(documents[1].document_role, "portal_page")

    def test_returns_portal_required_only_for_matching_official_portal_page(self) -> None:
        portal = TenderDocument(
            opportunity_id=self.opportunity.id,
            source_url="https://portal.example/respond/ABC123",
            final_url="https://portal.example/respond/ABC123",
            title="Workflow Case Management Platform tender",
            media_type="text/html",
            text_content="Example Council Workflow Case Management Platform",
        )

        assessment = self.classifier.assess(self.opportunity, [portal])

        self.assertEqual(assessment.status, "portal_required")
        self.assertEqual(assessment.portal_url, portal.final_url)

    def test_does_not_surface_unrelated_portal_or_failed_sources(self) -> None:
        unrelated = TenderDocument(
            opportunity_id=self.opportunity.id,
            source_url="https://portal.example/tender/other",
            final_url="https://portal.example/tender/other",
            title="Grounds maintenance tender",
            media_type="text/html",
        )
        failed = TenderDocument(
            opportunity_id=self.opportunity.id,
            source_url="https://portal.example/respond/ABC123",
            status=DocumentStatus.BLOCKED,
        )

        assessment = self.classifier.assess(self.opportunity, [unrelated, failed])

        self.assertEqual(assessment.status, "not_found")
        self.assertFalse(assessment.portal_url)

    def test_user_uploaded_itt_is_high_confidence(self) -> None:
        uploaded = TenderDocument(
            opportunity_id=self.opportunity.id,
            source_url="user-upload:///Invitation%20to%20Tender.docx",
            final_url="user-upload:///Invitation%20to%20Tender.docx",
            filename="Invitation to Tender.docx",
            media_type=(
                "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
            ),
        )

        assessment = self.classifier.assess(self.opportunity, [uploaded])

        self.assertEqual(assessment.status, "found")
        self.assertTrue(uploaded.is_core)
        self.assertGreaterEqual(uploaded.confidence, 0.95)

    def test_selects_matching_commission_brief_and_rejects_sibling_roles(self) -> None:
        opportunity = Opportunity(
            signal_id="signal-2",
            title="Project Manager – Lymington Sea Water Baths Restoration",
            organization="Lymington and Pennington Town Council",
        )
        matching = TenderDocument(
            opportunity_id=opportunity.id,
            source_url=(
                "https://council.example/Commission-Brief-Project-Manager-"
                "and-Major-Bid-Partner.pdf"
            ),
            filename="Commission_Brief_-_Project_Manager_and_Major_Bid_Partner.pdf",
            media_type="application/pdf",
        )
        sibling = TenderDocument(
            opportunity_id=opportunity.id,
            source_url=(
                "https://council.example/Commission-Brief-Heritage-Consultant.pdf"
            ),
            filename="Commission Brief - Heritage Consultant.pdf",
            media_type="application/pdf",
        )

        assessment = self.classifier.assess(opportunity, [matching, sibling])

        self.assertEqual(assessment.status, "found")
        self.assertEqual(assessment.core_documents, [matching])
        self.assertEqual(matching.document_role, "requirements")
        self.assertTrue(matching.is_core)
        self.assertEqual(sibling.document_role, "supporting")
        self.assertFalse(sibling.is_core)


if __name__ == "__main__":
    unittest.main()
