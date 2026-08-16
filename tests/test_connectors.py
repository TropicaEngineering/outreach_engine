from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from outreach_engine.connectors.fixture import FixtureConnector
from outreach_engine.connectors.directory import DirectoryConnector
from outreach_engine.connectors.gmail import GmailConnector


class FixtureConnectorTests(unittest.TestCase):
    def test_reads_sanitized_signals(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "signals.json"
            path.write_text(
                json.dumps(
                    [
                        {
                            "external_id": "one",
                            "subject": "An opportunity",
                            "body": "Budget: £20,000",
                        }
                    ]
                ),
                encoding="utf-8",
            )
            signals = FixtureConnector(path).pull()
        self.assertEqual(len(signals), 1)
        self.assertEqual(signals[0].external_id, "one")
        self.assertEqual(signals[0].source, "fixture")

    def test_requires_a_json_array(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "signals.json"
            path.write_text("{}", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "JSON array"):
                FixtureConnector(path).pull()


class DirectoryConnectorTests(unittest.TestCase):
    def test_reads_text_with_legacy_metadata(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            raw = root / "raw"
            metadata = root / "parsed"
            raw.mkdir()
            metadata.mkdir()
            (raw / "message-1.txt").write_text("Buyer: Example Council", encoding="utf-8")
            (metadata / "message-1.json").write_text(
                json.dumps(
                    {
                        "email_subject": "Tender alert",
                        "received_at": "2026-08-14T10:00:00Z",
                        "classification": "direct_bid",
                    }
                ),
                encoding="utf-8",
            )
            signals = DirectoryConnector(raw, metadata).pull()

        self.assertEqual(len(signals), 1)
        self.assertEqual(signals[0].subject, "Tender alert")
        self.assertEqual(signals[0].source_metadata["legacy_classification"], "direct_bid")


class GmailConnectorTests(unittest.TestCase):
    def test_splits_find_a_tender_digest_into_stable_notice_signals(self) -> None:
        body = """You have 2 new notices based on your saved search

First workflow tender: https://www.find-tender.service.gov.uk/Notice/077312-2026?source=email
-----------------------------------------------------------------

Example Council

Notice type: UK4 Tender notice
Submission deadline: 31 August 2026

=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=

Second data platform: https://www.find-tender.service.gov.uk/Notice/077182-2026?source=email
-----------------------------------------------------------------

Example NHS Trust

Notice type: UK2 Preliminary market engagement notice

You can change the language we use in emails by signing in: https://example.test/login
Unsubscribe: https://example.test/unsubscribe/token
"""
        connector = GmailConnector(Path("credentials.json"), Path("token.json"), "INBOX")

        signals = connector._signals_from_message(
            message_id="gmail-message",
            thread_id="thread-1",
            subject="Saved search results",
            body=body,
            sender="Find a Tender",
            recipient="team@example.test",
            received_at="14 August 2026",
        )

        self.assertEqual(len(signals), 2)
        self.assertEqual(signals[0].external_id, "find-tender:077312-2026")
        self.assertEqual(signals[1].external_id, "find-tender:077182-2026")
        self.assertEqual(signals[0].subject, "First workflow tender")
        self.assertNotIn("Second data platform", signals[0].body)
        self.assertNotIn("Unsubscribe", signals[1].body)
        self.assertEqual(signals[1].source_metadata["notice_id"], "077182-2026")


if __name__ == "__main__":
    unittest.main()
