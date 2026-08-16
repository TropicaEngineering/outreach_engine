from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from outreach_engine.domain import (
    Decision,
    DecisionAction,
    Opportunity,
    TenderDocument,
)
from outreach_engine.providers.openai_provider import OpenAIProvider


class FakeResponse:
    def __init__(self, payload: dict):
        self.output_text = json.dumps(payload)


class FakeResponses:
    def __init__(self, payload: dict):
        self.payload = payload
        self.requests: list[dict] = []

    def create(self, **kwargs):
        self.requests.append(kwargs)
        return FakeResponse(self.payload)


class FakeClient:
    def __init__(self, payload: dict):
        self.responses = FakeResponses(payload)


class OpenAIProviderTests(unittest.TestCase):
    def test_explicit_budget_gets_deterministic_profile_price(self) -> None:
        payload = {
            "pricing_schedule": {
                "required": True,
                "status": "company_input_required",
                "budget_ceiling": "£28,000 excluding VAT",
                "target_total": "[RATE REQUIRED]",
                "strategy_note": "",
                "line_items": [
                    {
                        "item": "Project management",
                        "quantity": "1",
                        "unit": "lot",
                        "unit_price": "[RATE REQUIRED]",
                        "total": "[RATE REQUIRED]",
                        "basis": "Fixed fee",
                    },
                    {
                        "item": "Comparator study",
                        "quantity": "1",
                        "unit": "lot",
                        "unit_price": "[RATE REQUIRED]",
                        "total": "[RATE REQUIRED]",
                        "basis": "Fixed fee",
                    },
                ],
                "assumptions": [],
            }
        }

        OpenAIProvider._apply_pricing_profile(
            payload, {"target_discount_percent": 10}
        )

        pricing = payload["pricing_schedule"]
        self.assertEqual(pricing["target_total"], "£25,200")
        self.assertEqual(
            [item["total"] for item in pricing["line_items"]],
            ["£12,600", "£12,600"],
        )
        self.assertEqual(pricing["status"], "partially_drafted")

    def test_live_tender_uses_retrieved_files_to_create_bid_working_document(self) -> None:
        payload = {
            "title": "Response pack — Workflow platform",
            "brief": {
                "objective": "Deliver an auditable workflow platform.",
                "recommended_approach": "Configure and phase the implementation.",
                "buyer_priorities": ["Auditability", "Low-risk transition"],
                "requirements_assurance": (
                    "Checked the specification requirements and pricing instructions."
                ),
                "tailored_win_themes": [
                    "Auditable delivery governance",
                    "Low-risk phased transition",
                ],
                "submission_route": "Upload through the buyer portal.",
                "pack_status": "complete",
                "pack_note": "The supplied specification and response template were checked.",
            },
            "deliverables": [
                {
                    "title": "Quality response: workflow management",
                    "deliverable_type": "method_statement",
                    "source": "specification.pdf, p. 4",
                    "status": "partially_drafted",
                    "purpose": "Answer the buyer's quality question.",
                    "draft_content": (
                        "We will configure an auditable workflow. "
                        "[COMPANY INPUT REQUIRED: relevant case study]"
                    ),
                }
            ],
            "pricing_schedule": {
                "required": True,
                "status": "drafted",
                "source": "specification.pdf, p. 8",
                "currency": "GBP",
                "budget_ceiling": "£100,000",
                "target_total": "[RATE REQUIRED]",
                "strategy_note": "Commercial input required.",
                "line_items": [
                    {
                        "item": "Implementation",
                        "quantity": "1",
                        "unit": "project",
                        "unit_price": "[RATE REQUIRED]",
                        "total": "[RATE REQUIRED]",
                        "basis": "Fixed price",
                    }
                ],
                "assumptions": ["Prices exclude VAT."],
            },
            "submission_checklist": [
                {
                    "item": "Quality response",
                    "source": "specification.pdf, p. 4",
                    "status": "company_input_required",
                    "handling": "generated_in_pack",
                    "output": "Quality response: workflow management",
                }
            ],
            "missing_inputs": [
                {
                    "item": "Named workflow implementation case study",
                    "why": "Required to substantiate delivery experience.",
                    "action": "Bid lead to add the approved case study.",
                }
            ],
        }
        provider = OpenAIProvider.__new__(OpenAIProvider)
        provider.model_name = "cheap-model"
        provider.drafting_model_name = "strong-model"
        provider._client = FakeClient(payload)
        opportunity = Opportunity(
            signal_id="signal-1",
            title="Workflow platform",
            organization="Example Council",
            attributes={
                "pack_access_url": "https://portal.example.test/tender/123",
                "bid_pack_missing_roles": ["response_template", "terms"],
                "bid_inputs": {
                    "Relevant project examples": "Council workflow rollout, 2025."
                },
            },
        )
        decision = Decision(
            opportunity_id=opportunity.id,
            action=DecisionAction.REVIEW,
            score=82,
            label="high",
            reason="Live tender with capability fit.",
            target_type="buyer",
            playbook="procurement",
            playbook_version="test",
        )
        with tempfile.TemporaryDirectory() as temp_dir:
            pdf_path = Path(temp_dir) / "specification.pdf"
            pdf_path.write_bytes(b"%PDF-test")
            document = TenderDocument(
                opportunity_id=opportunity.id,
                source_url="https://example.test/specification.pdf",
                final_url="https://example.test/specification.pdf",
                title="Specification",
                media_type="application/pdf",
                filename="specification.pdf",
                local_path=str(pdf_path),
                content_hash="abc",
                size_bytes=9,
            )

            draft = provider.draft(
                opportunity,
                decision,
                [document],
                bid_profile={
                    "tone": "Understated British professional",
                    "target_discount_percent": 10,
                },
            )

        request = provider._client.responses.requests[0]
        self.assertEqual(request["model"], "strong-model")
        content = request["input"][0]["content"]
        file_input = next(item for item in content if item["type"] == "input_file")
        self.assertEqual(file_input["filename"], "specification.pdf")
        self.assertTrue(file_input["file_data"].startswith("data:application/pdf;base64,"))
        self.assertEqual(draft.channel, "bid_document")
        self.assertIn("## Bid response documents", draft.body)
        self.assertIn("## Pricing schedule", draft.body)
        self.assertIn("£90,000", draft.body)
        self.assertNotIn("## Inputs still needed", draft.body)
        self.assertNotIn("## Submission checklist", draft.body)
        self.assertEqual(
            draft.metadata["deliverables"][0]["source"], "specification.pdf, p. 4"
        )
        self.assertEqual(
            draft.metadata["missing_inputs"][0]["item"],
            "Named workflow implementation case study",
        )
        request_text = content[0]["text"]
        self.assertIn("Understated British professional", request_text)
        self.assertIn("https://portal.example.test/tender/123", request_text)
        self.assertIn("Council workflow rollout, 2025.", request_text)
        self.assertNotIn("bid_pack_missing_roles", request_text)


if __name__ == "__main__":
    unittest.main()
