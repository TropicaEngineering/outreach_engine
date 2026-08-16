from __future__ import annotations

import json
import tempfile
import unittest
import zipfile
from io import BytesIO
from pathlib import Path

from outreach_engine.domain import Opportunity, Signal
from outreach_engine.retrieval.web import (
    FetchResult,
    SafeHttpClient,
    UnsafeUrlError,
    WebTenderRetriever,
)


class FakeHttpClient:
    max_resource_bytes = 1_000_000

    def __init__(self, resources: dict[str, FetchResult]):
        self.resources = resources
        self.requested: list[str] = []

    def fetch(self, url: str) -> FetchResult:
        self.requested.append(url)
        return self.resources[url]


class FakeDiscoverer:
    def __init__(self, urls: list[str]):
        self.urls = urls
        self.calls = 0

    def discover(self, signal, opportunity) -> list[str]:
        self.calls += 1
        return self.urls


def fetched(url: str, media_type: str, body: bytes) -> FetchResult:
    return FetchResult(
        requested_url=url,
        final_url=url,
        status=200,
        media_type=media_type,
        content_disposition="",
        body=body,
    )


class WebTenderRetrieverTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.signal = Signal(
            source="test",
            external_id="tender-1",
            subject="Tender alert",
            body="Full details: https://procurement.example/opportunity/123",
        )
        self.opportunity = Opportunity(
            signal_id=self.signal.id,
            title="Case management platform",
            organization="Example Council",
            url="https://procurement.example/opportunity/123",
        )

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_follows_relevant_document_links_and_persists_source_file(self) -> None:
        page_url = "https://procurement.example/opportunity/123"
        pdf_url = "https://procurement.example/files/specification.pdf"
        unrelated_url = "https://procurement.example/about-us"
        page = (
            '<html><head><title>Contract documents</title></head><body>'
            '<a href="/files/specification.pdf">Download tender specification</a>'
            '<a href="/about-us">About us</a>'
            "Mandatory requirement: provide 24/7 support."
            "</body></html>"
        ).encode()
        client = FakeHttpClient(
            {
                page_url: fetched(page_url, "text/html", page),
                pdf_url: fetched(pdf_url, "application/pdf", b"%PDF-test-content"),
            }
        )
        retriever = WebTenderRetriever(
            Path(self.temp_dir.name), http_client=client, max_pages=5, max_depth=2
        )

        report = retriever.retrieve(self.signal, self.opportunity)

        self.assertEqual(report.status, "complete")
        self.assertEqual(client.requested, [page_url, pdf_url])
        self.assertNotIn(unrelated_url, client.requested)
        self.assertEqual(len(report.documents), 2)
        pdf = next(
            document
            for document in report.documents
            if document.media_type == "application/pdf"
        )
        self.assertTrue(Path(pdf.local_path).is_file())
        self.assertEqual(Path(pdf.local_path).read_bytes(), b"%PDF-test-content")
        html = next(document for document in report.documents if document.media_type == "text/html")
        self.assertIn("Mandatory requirement", html.text_content)

    def test_expands_supported_documents_from_zip_without_path_traversal(self) -> None:
        archive_bytes = BytesIO()
        with zipfile.ZipFile(archive_bytes, "w") as archive:
            archive.writestr("pack/response-template.docx", b"docx-content")
            archive.writestr("../../ignored.exe", b"unsafe-content")
        url = "https://procurement.example/tender-pack.zip"
        self.signal.body = f"Download {url}"
        self.opportunity.url = url
        client = FakeHttpClient(
            {url: fetched(url, "application/zip", archive_bytes.getvalue())}
        )
        retriever = WebTenderRetriever(Path(self.temp_dir.name), http_client=client)

        report = retriever.retrieve(self.signal, self.opportunity)

        filenames = [document.filename for document in report.documents]
        self.assertEqual(filenames, ["response-template.docx"])
        self.assertNotIn("ignored.exe", filenames)

    def test_blocks_local_and_private_network_targets(self) -> None:
        for url in (
            "http://127.0.0.1/admin",
            "http://[::1]/admin",
            "http://169.254.169.254/latest/meta-data",
        ):
            with self.subTest(url=url), self.assertRaises(UnsafeUrlError):
                SafeHttpClient._assert_public_url(url)

    def test_uses_extracted_opportunity_url_not_other_links_in_digest_email(self) -> None:
        primary = "https://procurement.example/opportunity/123"
        other = "https://procurement.example/opportunity/999"
        footer = "https://procurement.example/login"
        self.signal.body = f"First {primary}\nSecond {other}\nAccount {footer}"
        self.opportunity.url = primary
        client = FakeHttpClient(
            {primary: fetched(primary, "text/html", b"<html>Case management platform</html>")}
        )
        retriever = WebTenderRetriever(Path(self.temp_dir.name), http_client=client)

        retriever.retrieve(self.signal, self.opportunity)

        self.assertEqual(client.requested, [primary])

    def test_discards_unrelated_web_search_source(self) -> None:
        primary = "https://procurement.example/opportunity/123"
        unrelated = "https://search-result.example/unrelated-contract"
        client = FakeHttpClient(
            {
                primary: fetched(
                    primary,
                    "text/html",
                    b"<html>Case management platform for Example Council</html>",
                ),
                unrelated: fetched(
                    unrelated,
                    "text/html",
                    b"<html>Grounds maintenance contract for Another Council</html>",
                ),
            }
        )
        retriever = WebTenderRetriever(
            Path(self.temp_dir.name),
            http_client=client,
            discoverer=FakeDiscoverer([unrelated]),
        )

        report = retriever.retrieve(self.signal, self.opportunity)

        self.assertEqual(len(report.documents), 1)
        self.assertEqual(report.documents[0].source_url, primary)

    def test_find_a_tender_notice_adds_public_ocds_endpoint(self) -> None:
        url = "https://www.find-tender.service.gov.uk/Notice/077312-2026?source=email"

        seeds = WebTenderRetriever._known_public_data_urls(url)

        self.assertEqual(
            seeds,
            [
                "https://www.find-tender.service.gov.uk/api/1.0/"
                "ocdsReleasePackages/077312-2026"
            ],
        )

    def test_authoritative_find_tender_data_skips_broad_web_discovery(self) -> None:
        notice = "https://www.find-tender.service.gov.uk/Notice/077312-2026"
        data = (
            "https://www.find-tender.service.gov.uk/api/1.0/"
            "ocdsReleasePackages/077312-2026"
        )
        self.opportunity.url = notice
        discoverer = FakeDiscoverer(["https://example.test/unrelated-policy"])
        client = FakeHttpClient(
            {
                notice: fetched(
                    notice,
                    "text/html",
                    b"<html>Case management platform for Example Council</html>",
                ),
                data: fetched(
                    data,
                    "application/json",
                    b'{"title":"Case management platform"}',
                ),
            }
        )
        retriever = WebTenderRetriever(
            Path(self.temp_dir.name), http_client=client, discoverer=discoverer
        )

        report = retriever.retrieve(self.signal, self.opportunity)

        self.assertEqual(discoverer.calls, 0)
        self.assertEqual(client.requested, [notice, data])
        self.assertEqual(len(report.documents), 2)

    def test_resolves_pack_route_from_exact_find_tender_record(self) -> None:
        notice = "https://www.find-tender.service.gov.uk/Notice/077312-2026"
        data = (
            "https://www.find-tender.service.gov.uk/api/1.0/"
            "ocdsReleasePackages/077312-2026"
        )
        portal = "https://procontract.due-north.com/Advert/ABC123"
        document = "https://www.find-tender.service.gov.uk/Notice/Attachment/A-123"
        self.opportunity.url = notice
        payload = json.dumps(
            {
                "releases": [
                    {
                        "tender": {
                            "submissionMethod": ["electronicSubmission"],
                            "submissionMethodDetails": portal,
                            "tenderPeriod": {"endDate": "2026-09-01T12:00:00Z"},
                            "documents": [
                                {
                                    "documentType": "biddingDocuments",
                                    "url": document,
                                },
                                {
                                    "documentType": "tenderNotice",
                                    "url": notice,
                                },
                            ],
                        }
                    }
                ]
            }
        ).encode()
        client = FakeHttpClient({data: fetched(data, "application/json", payload)})
        retriever = WebTenderRetriever(Path(self.temp_dir.name), http_client=client)

        route = retriever.resolve_access_route(self.signal, self.opportunity)

        self.assertEqual(client.requested, [data])
        self.assertEqual(route.status, "resolved")
        self.assertEqual(route.access_type, "external_portal")
        self.assertEqual(route.label, "ProContract")
        self.assertEqual(route.url, portal)
        self.assertEqual(route.document_urls, (document,))
        self.assertEqual(route.submission_method, "Electronic submission")
        self.assertEqual(route.deadline, "2026-09-01T12:00:00Z")

    def test_resolves_explicit_email_route_without_using_buyer_contact(self) -> None:
        notice = "https://www.find-tender.service.gov.uk/Notice/077312-2026"
        data = (
            "https://www.find-tender.service.gov.uk/api/1.0/"
            "ocdsReleasePackages/077312-2026"
        )
        self.opportunity.url = notice
        payload = json.dumps(
            {
                "releases": [
                    {
                        "tender": {
                            "submissionMethodDetails": (
                                "Email bids@example.gov.uk to request the tender pack."
                            ),
                            "documents": [],
                        },
                        "parties": [
                            {"contactPoint": {"email": "generic@example.gov.uk"}}
                        ],
                    }
                ]
            }
        ).encode()
        client = FakeHttpClient({data: fetched(data, "application/json", payload)})
        retriever = WebTenderRetriever(Path(self.temp_dir.name), http_client=client)

        route = retriever.resolve_access_route(self.signal, self.opportunity)

        self.assertEqual(route.access_type, "email_request")
        self.assertEqual(route.email, "bids@example.gov.uk")
        self.assertNotEqual(route.email, "generic@example.gov.uk")

    def test_prefers_exact_opportunity_links_in_official_description(self) -> None:
        notice = "https://www.find-tender.service.gov.uk/Notice/077312-2026"
        data = (
            "https://www.find-tender.service.gov.uk/api/1.0/"
            "ocdsReleasePackages/077312-2026"
        )
        pack_url = "https://buyer.delta-esourcing.com/tenders/ABC123"
        response_url = "https://buyer.delta-esourcing.com/respond/ABC123"
        self.opportunity.url = notice
        payload = json.dumps(
            {
                "releases": [
                    {
                        "tender": {
                            "submissionMethodDetails": (
                                "https://www.delta-esourcing.com/"
                            ),
                            "description": (
                                "For more information, visit:\n"
                                f"{pack_url}\nTo respond, use:\n{response_url}"
                            ),
                            "documents": [],
                        }
                    }
                ]
            }
        ).encode()
        client = FakeHttpClient({data: fetched(data, "application/json", payload)})
        retriever = WebTenderRetriever(Path(self.temp_dir.name), http_client=client)

        route = retriever.resolve_access_route(self.signal, self.opportunity)

        self.assertEqual(route.label, "Delta eSourcing")
        self.assertEqual(route.url, pack_url)
        self.assertEqual(route.submission_url, response_url)
        self.assertNotEqual(route.url, "https://www.delta-esourcing.com/")

    def test_follows_relevant_portal_url_embedded_in_json(self) -> None:
        notice = "https://procurement.example/notices/123.json"
        portal = "https://portal.example/tenders/workflow-platform/ABC123"
        extension = (
            "https://raw.githubusercontent.com/open-contracting-extensions/"
            "ocds_documentation_extension/master/extension.json"
        )
        self.signal.body = f"Notice: {notice}"
        self.opportunity.url = notice
        payload = json.dumps(
            {
                "title": "Case management platform",
                "documents": [f"{portal}\nTo respond, use the portal"],
                "extensions": [extension],
                "publicationPolicy": (
                    "https://www.gov.uk/government/publications/open-contracting"
                ),
            }
        ).encode()
        client = FakeHttpClient(
            {
                notice: fetched(notice, "application/json", payload),
                portal: fetched(
                    portal,
                    "text/html",
                    b"<html>Case management platform tender documents</html>",
                ),
            }
        )
        retriever = WebTenderRetriever(
            Path(self.temp_dir.name), http_client=client, max_depth=2
        )

        report = retriever.retrieve(self.signal, self.opportunity)

        self.assertEqual(client.requested, [notice, portal])
        self.assertEqual(len(report.documents), 2)

    def test_canonical_url_removes_tracking_parameters(self) -> None:
        tracked = "https://example.test/notice/123?utm_source=email&utm_campaign=digest"

        self.assertEqual(
            WebTenderRetriever._canonical_url(tracked),
            "https://example.test/notice/123",
        )


if __name__ == "__main__":
    unittest.main()
