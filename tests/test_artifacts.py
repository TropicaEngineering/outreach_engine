from __future__ import annotations

import unittest

from outreach_engine.artifacts import plan_submission_pack


class SubmissionArtifactPlanTests(unittest.TestCase):
    def test_builds_word_and_separate_pricing_file_from_existing_markdown(self) -> None:
        detail = {
            "title": "Council transformation support",
            "organization": "Example Council",
            "draft_subject": "Council transformation support response",
            "draft_body": (
                "# Response\n\n## Bid response documents\n\n"
                "### Method statement\n\nA buyer-facing answer.\n\n"
                "## Pricing schedule\n**Total price:** £9,000\n\n"
                "| Cost item | Quantity | Unit | Unit price | Total | Basis |\n"
                "| --- | ---: | --- | ---: | ---: | --- |\n"
                "| Delivery | 1 | fixed fee | £9,000 | £9,000 | Planning price |\n\n"
                "**Pricing assumptions**\n- Excludes VAT\n"
            ),
            "draft_metadata": {
                "deliverables": [
                    {
                        "title": "Method statement",
                        "deliverable_type": "method_statement",
                        "status": "drafted",
                        "purpose": "Quality response",
                    }
                ],
                "pricing": {
                    "required": True,
                    "target_total": "£9,000",
                    "budget_ceiling": "£10,000",
                },
                "submission_checklist": [],
            },
        }

        plan = plan_submission_pack(detail, {"company_name": "Example Ltd"})

        self.assertEqual([item["kind"] for item in plan["files"]], ["docx", "xlsx"])
        self.assertEqual(plan["files"][1]["pricing"]["line_items"][0]["item"], "Delivery")
        self.assertEqual(plan["files"][1]["pricing"]["assumptions"], ["Excludes VAT"])

    def test_only_generates_a_separate_attachment_when_content_exists(self) -> None:
        detail = {
            "title": "Tender",
            "organization": "Council",
            "draft_subject": "Tender response",
            "draft_body": (
                "## Bid response documents\n\n"
                "### Main response\n\nMain content.\n\n"
                "### Mobilisation plan\n\nPlan content.\n"
            ),
            "draft_metadata": {
                "deliverables": [
                    {"title": "Main response", "status": "drafted"},
                    {"title": "Mobilisation plan", "status": "drafted"},
                ],
                "pricing": {"required": False},
                "submission_checklist": [
                    {
                        "item": "Mobilisation plan",
                        "handling": "separate_attachment",
                        "status": "ready",
                        "output": "Mobilisation plan.docx",
                    },
                    {
                        "item": "Lead CV",
                        "handling": "separate_attachment",
                        "status": "company_input_required",
                        "output": "Attach lead CV",
                    },
                    {
                        "item": "Council declaration",
                        "handling": "manual_form",
                        "status": "to_check",
                        "output": "Complete buyer form",
                    },
                ],
            },
        }

        plan = plan_submission_pack(detail, {"company_name": "Example Ltd"})

        self.assertEqual([item["kind"] for item in plan["files"]], ["docx", "docx"])
        self.assertEqual(plan["files"][1]["sections"][0]["title"], "Mobilisation plan")
        self.assertEqual([item["item"] for item in plan["actions"]], ["Lead CV", "Council declaration"])


if __name__ == "__main__":
    unittest.main()
