from __future__ import annotations

import hashlib
import html
import ipaddress
import json
import logging
import mimetypes
import re
import socket
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from collections import deque
from dataclasses import dataclass
from html.parser import HTMLParser
from io import BytesIO
from pathlib import Path

from outreach_engine.domain import (
    DocumentStatus,
    Opportunity,
    RetrievalReport,
    Signal,
    TenderDocument,
)
from outreach_engine.retrieval.base import WebDiscoverer
from outreach_engine.retrieval.bid_pack import BidPackClassifier


LOGGER = logging.getLogger(__name__)

URL_RE = re.compile(r"https?://[^\s<>\"']+", re.IGNORECASE)
EMAIL_RE = re.compile(r"\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b", re.IGNORECASE)
DOCUMENT_EXTENSIONS = {
    ".csv",
    ".doc",
    ".docx",
    ".html",
    ".htm",
    ".json",
    ".odt",
    ".pdf",
    ".ppt",
    ".pptx",
    ".rtf",
    ".tsv",
    ".txt",
    ".xls",
    ".xlsx",
    ".xml",
    ".zip",
}
TENDER_LINK_TERMS = {
    "attachment",
    "bid",
    "contract",
    "criteria",
    "document",
    "download",
    "evaluation",
    "invitation",
    "itt",
    "lot",
    "notice",
    "pricing",
    "procurement",
    "questionnaire",
    "requirement",
    "respond",
    "rfp",
    "schedule",
    "specification",
    "submission",
    "tender",
}
TEXT_MEDIA_TYPES = {
    "application/json",
    "application/xml",
    "text/csv",
    "text/html",
    "text/plain",
    "text/tab-separated-values",
    "text/xml",
}
SEED_EXCLUSION_TERMS = {
    "/dashboard",
    "/login",
    "/manage/subscriptions/",
    "/unsubscribe/",
    "footer-account",
    "footer-service",
    "footer-unsubscribe",
}
MATCH_STOPWORDS = {
    "and",
    "contract",
    "for",
    "from",
    "into",
    "notice",
    "of",
    "pre",
    "procurement",
    "the",
    "tender",
    "with",
}


class RetrievalError(RuntimeError):
    pass


class UnsafeUrlError(RetrievalError):
    pass


class AccessBlockedError(RetrievalError):
    pass


@dataclass(frozen=True, slots=True)
class FetchResult:
    requested_url: str
    final_url: str
    status: int
    media_type: str
    content_disposition: str
    body: bytes


@dataclass(frozen=True, slots=True)
class NoticeAccessRoute:
    """Small, persisted handoff from a published notice to its tender documents."""

    status: str
    access_type: str
    label: str
    url: str = ""
    email: str = ""
    evidence: str = ""
    document_urls: tuple[str, ...] = ()
    submission_method: str = ""
    submission_url: str = ""
    deadline: str = ""
    notice_id: str = ""
    error: str = ""


class _NoRedirectHandler(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: ANN001
        return None


class SafeHttpClient:
    """Small HTTP client with redirect and SSRF checks on every hop."""

    def __init__(self, timeout_seconds: float = 15, max_resource_bytes: int = 10_000_000):
        self.timeout_seconds = timeout_seconds
        self.max_resource_bytes = max_resource_bytes
        self._opener = urllib.request.build_opener(_NoRedirectHandler())

    def fetch(self, url: str) -> FetchResult:
        requested_url = url
        for _ in range(6):
            self._assert_public_url(url)
            request = urllib.request.Request(
                url,
                headers={
                    "Accept": (
                        "text/html,application/pdf,application/json,application/xml,"
                        "application/zip,*/*;q=0.5"
                    ),
                    "User-Agent": "SignalRouteTenderRetriever/0.2 (+local human review)",
                },
            )
            try:
                response = self._opener.open(request, timeout=self.timeout_seconds)
            except urllib.error.HTTPError as exc:
                if exc.code in {301, 302, 303, 307, 308}:
                    location = exc.headers.get("Location", "")
                    if not location:
                        raise RetrievalError(f"redirect without location: {url}") from exc
                    url = urllib.parse.urljoin(url, location)
                    continue
                if exc.code in {401, 403}:
                    raise AccessBlockedError(
                        f"portal access required (HTTP {exc.code}): {url}"
                    ) from exc
                raise RetrievalError(f"HTTP {exc.code}: {url}") from exc
            except urllib.error.URLError as exc:
                raise RetrievalError(f"request failed for {url}: {exc.reason}") from exc

            with response:
                length = response.headers.get("Content-Length")
                if length and int(length) > self.max_resource_bytes:
                    raise RetrievalError(f"resource exceeds size limit: {url}")
                body = response.read(self.max_resource_bytes + 1)
                if len(body) > self.max_resource_bytes:
                    raise RetrievalError(f"resource exceeds size limit: {url}")
                media_type = response.headers.get_content_type().lower()
                return FetchResult(
                    requested_url=requested_url,
                    final_url=response.geturl(),
                    status=getattr(response, "status", 200),
                    media_type=media_type,
                    content_disposition=response.headers.get("Content-Disposition", ""),
                    body=body,
                )
        raise RetrievalError(f"too many redirects: {requested_url}")

    @staticmethod
    def _assert_public_url(url: str) -> None:
        parsed = urllib.parse.urlsplit(url)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            raise UnsafeUrlError(f"unsupported URL: {url}")
        if parsed.username or parsed.password:
            raise UnsafeUrlError(f"credentialed URL blocked: {url}")
        try:
            port = parsed.port or (443 if parsed.scheme == "https" else 80)
        except ValueError as exc:
            raise UnsafeUrlError(f"invalid port: {url}") from exc
        if port not in {80, 443}:
            raise UnsafeUrlError(f"non-standard port blocked: {url}")
        try:
            addresses = socket.getaddrinfo(parsed.hostname, port, type=socket.SOCK_STREAM)
        except socket.gaierror as exc:
            raise RetrievalError(f"DNS lookup failed: {parsed.hostname}") from exc
        for address in addresses:
            ip = ipaddress.ip_address(address[4][0])
            if not ip.is_global:
                raise UnsafeUrlError(f"non-public address blocked: {parsed.hostname}")


class _TenderHTMLParser(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.title = ""
        self.text_parts: list[str] = []
        self.links: list[tuple[str, str]] = []
        self._skip_depth = 0
        self._in_title = False
        self._current_href = ""
        self._anchor_text: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in {"script", "style", "noscript", "svg"}:
            self._skip_depth += 1
            return
        if self._skip_depth:
            return
        if tag == "title":
            self._in_title = True
        if tag == "a":
            values = dict(attrs)
            self._current_href = values.get("href") or ""
            self._anchor_text = []

    def handle_endtag(self, tag: str) -> None:
        if tag in {"script", "style", "noscript", "svg"} and self._skip_depth:
            self._skip_depth -= 1
            return
        if self._skip_depth:
            return
        if tag == "title":
            self._in_title = False
        if tag == "a" and self._current_href:
            self.links.append((self._current_href, " ".join(self._anchor_text).strip()))
            self._current_href = ""
            self._anchor_text = []

    def handle_data(self, data: str) -> None:
        if self._skip_depth:
            return
        value = " ".join(data.split())
        if not value:
            return
        if self._in_title:
            self.title = f"{self.title} {value}".strip()
        if self._current_href:
            self._anchor_text.append(value)
        self.text_parts.append(value)


class WebTenderRetriever:
    """Bounded, source-agnostic crawl rooted in links from an inbound tender signal."""

    def __init__(
        self,
        document_root: Path,
        *,
        discoverer: WebDiscoverer | None = None,
        http_client: SafeHttpClient | None = None,
        max_pages: int = 12,
        max_depth: int = 2,
        max_total_bytes: int = 30_000_000,
        max_text_chars: int = 80_000,
        classifier: BidPackClassifier | None = None,
    ):
        self.document_root = document_root
        self.discoverer = discoverer
        self.http_client = http_client or SafeHttpClient()
        self.max_pages = max(1, min(max_pages, 50))
        self.max_depth = max(0, min(max_depth, 4))
        self.max_total_bytes = max(100_000, max_total_bytes)
        self.max_text_chars = max(2_000, max_text_chars)
        self.classifier = classifier or BidPackClassifier()

    def resolve_access_route(
        self, signal: Signal, opportunity: Opportunity
    ) -> NoticeAccessRoute:
        """Resolve only the explicit document/submission route in the official notice."""
        notice_url = opportunity.url or str(signal.source_metadata.get("notice_url", ""))
        api_urls = self._known_public_data_urls(notice_url)
        notice_id = self._notice_id(notice_url or signal.external_id)
        if not api_urls:
            return self._route_from_text(signal.body, notice_url, notice_id)
        try:
            fetched = self.http_client.fetch(api_urls[0])
            payload = json.loads(fetched.body.decode("utf-8"))
            releases = payload.get("releases") or []
            release = releases[0] if releases else {}
            tender = release.get("tender") or {}
            details = str(tender.get("submissionMethodDetails") or "").strip()
            description = str(tender.get("description") or "").strip()
            methods = tender.get("submissionMethod") or []
            if isinstance(methods, str):
                methods = [methods]
            submission_method = ", ".join(
                self._humanize_method(str(method)) for method in methods if method
            )
            details_urls = self._deduplicate_urls(
                [match.group(0) for match in URL_RE.finditer(details)]
            )
            description_urls = self._deduplicate_urls(
                [match.group(0) for match in URL_RE.finditer(description)]
            )
            submission_urls = [
                url
                for url in description_urls
                if any(
                    term in urllib.parse.urlsplit(url).path.casefold()
                    for term in {"respond", "submit", "submission"}
                )
            ]
            pack_urls = [url for url in description_urls if url not in submission_urls]
            route_url = (
                (pack_urls or submission_urls or details_urls or [""])[0]
            )
            explicit_emails = EMAIL_RE.findall(f"{details}\n{description}")
            document_urls = self._official_document_urls(tender.get("documents") or [])
            deadline = str((tender.get("tenderPeriod") or {}).get("endDate") or "")

            if route_url:
                route_evidence = (
                    self._evidence_around_url(description, route_url)
                    if route_url in description_urls
                    else details[:500]
                )
                return NoticeAccessRoute(
                    status="resolved",
                    access_type="external_portal",
                    label=self._route_label(route_url),
                    url=route_url,
                    evidence=route_evidence,
                    document_urls=tuple(document_urls),
                    submission_method=(
                        submission_method
                        or ("Electronic submission" if submission_urls else "Online instructions")
                    ),
                    submission_url=(submission_urls or details_urls or [route_url])[0],
                    deadline=deadline,
                    notice_id=notice_id,
                )
            if explicit_emails:
                email = explicit_emails[0]
                return NoticeAccessRoute(
                    status="resolved",
                    access_type="email_request",
                    label="Request documents by email",
                    email=email,
                    evidence=details[:500],
                    document_urls=tuple(document_urls),
                    submission_method=submission_method or "Email",
                    deadline=deadline,
                    notice_id=notice_id,
                )
            if document_urls:
                return NoticeAccessRoute(
                    status="resolved",
                    access_type="direct_download",
                    label=(
                        "Download bid documents"
                        if len(document_urls) > 1
                        else "Download bid document"
                    ),
                    url=document_urls[0],
                    evidence=(
                        f"Official notice lists {len(document_urls)} bidding document"
                        f"{'s' if len(document_urls) != 1 else ''}."
                    ),
                    document_urls=tuple(document_urls),
                    submission_method=submission_method,
                    deadline=deadline,
                    notice_id=notice_id,
                )
            return NoticeAccessRoute(
                status="advert_only",
                access_type="advert",
                label="Open tender advert",
                url=notice_url,
                evidence="The official record does not state a separate document route.",
                submission_method=submission_method,
                deadline=deadline,
                notice_id=notice_id,
            )
        except Exception as exc:
            LOGGER.warning("notice route resolution failed notice_id=%s: %s", notice_id, exc)
            return NoticeAccessRoute(
                status="unavailable",
                access_type="advert",
                label="Open tender advert",
                url=notice_url,
                evidence="The official advert remains available while enrichment retries.",
                notice_id=notice_id,
                error=f"{type(exc).__name__}: {exc}"[:500],
            )

    def retrieve(self, signal: Signal, opportunity: Opportunity) -> RetrievalReport:
        direct_seeds = self._seed_urls(signal, opportunity)
        seeds = list(direct_seeds)
        report = RetrievalReport(seed_urls=list(direct_seeds))
        has_authoritative_data = any(
            "/api/1.0/ocdsReleasePackages/" in url for url in direct_seeds
        )
        if self.discoverer and not has_authoritative_data:
            try:
                seeds.extend(self.discoverer.discover(signal, opportunity))
            except Exception as exc:
                report.errors.append(f"web discovery failed: {type(exc).__name__}: {exc}")
        seeds = self._deduplicate_urls(seeds)
        report.seed_urls = list(seeds)
        trusted_urls = {self._canonical_url(url) for url in direct_seeds}
        queue = deque(
            (url, 0, self._canonical_url(url) in trusted_urls) for url in seeds
        )
        seen: set[str] = set()
        hashes: set[str] = set()
        total_bytes = 0

        while queue and len(seen) < self.max_pages and total_bytes < self.max_total_bytes:
            url, depth, trusted = queue.popleft()
            canonical = self._canonical_url(url)
            if canonical in seen:
                continue
            seen.add(canonical)
            try:
                fetched = self.http_client.fetch(url)
            except (UnsafeUrlError, AccessBlockedError) as exc:
                if trusted or self._matches_opportunity(opportunity, url):
                    report.blocked_urls.append(url)
                    report.documents.append(
                        self._failed_document(
                            opportunity.id, url, depth, DocumentStatus.BLOCKED, exc
                        )
                    )
                continue
            except Exception as exc:
                if trusted or self._matches_opportunity(opportunity, url):
                    report.errors.append(f"{url}: {exc}")
                    report.documents.append(
                        self._failed_document(
                            opportunity.id, url, depth, DocumentStatus.FAILED, exc
                        )
                    )
                continue

            total_bytes += len(fetched.body)
            if total_bytes > self.max_total_bytes:
                report.errors.append("crawl stopped at total download limit")
                break
            digest = hashlib.sha256(fetched.body).hexdigest()
            if digest in hashes:
                continue
            hashes.add(digest)

            if self._is_html(fetched):
                document, links = self._html_document(opportunity.id, fetched, depth, digest)
                if not trusted and not self._matches_opportunity(
                    opportunity,
                    fetched.final_url,
                    f"{document.title}\n{document.text_content}",
                ):
                    continue
                report.documents.append(document)
                if len(document.text_content) < 200 and b"<script" in fetched.body.lower():
                    report.errors.append(
                        f"{fetched.final_url}: page likely requires JavaScript rendering"
                    )
                if depth < self.max_depth:
                    queue.extend(
                        (candidate, depth + 1, True)
                        for candidate in self._rank_links(fetched.final_url, links)
                        if self._canonical_url(candidate) not in seen
                    )
                continue

            if self._is_zip(fetched):
                if not trusted and not self._matches_opportunity(
                    opportunity, fetched.final_url
                ):
                    continue
                report.documents.extend(
                    self._archive_documents(opportunity.id, fetched, depth, hashes)
                )
                continue
            if self._is_supported_file(fetched):
                if not trusted and not self._matches_opportunity(
                    opportunity, fetched.final_url
                ):
                    continue
                document = self._file_document(opportunity.id, fetched, depth, digest)
                report.documents.append(document)
                if document.text_content and depth < self.max_depth:
                    embedded = [
                        candidate
                        for candidate in self._embedded_tender_links(
                            document.text_content, opportunity
                        )
                        if self._canonical_url(candidate) not in seen
                    ]
                    for candidate in reversed(embedded):
                        queue.appendleft((candidate, depth + 1, True))

        report.discovered_urls = len(seen) + len(queue)
        assessment = self.classifier.assess(opportunity, report.documents)
        report.pack_status = assessment.status
        report.pack_confidence = assessment.confidence
        report.portal_url = assessment.portal_url
        report.missing_roles = assessment.missing_roles
        return report

    def store_upload(
        self,
        opportunity: Opportunity,
        filename: str,
        media_type: str,
        body: bytes,
    ) -> RetrievalReport:
        """Safely persist user-supplied bid-pack files and classify their roles."""
        if not body:
            raise ValueError("The uploaded file is empty.")
        if len(body) > self.http_client.max_resource_bytes:
            raise ValueError("The uploaded file exceeds the configured size limit.")
        extension = Path(filename).suffix.casefold()
        allowed = DOCUMENT_EXTENSIONS - {".html", ".htm", ".json", ".xml"}
        if extension not in allowed:
            raise ValueError("Upload PDF, Word, Excel, PowerPoint, text, or ZIP files.")
        safe_name = self._sanitize_filename(filename, extension)
        upload_url = f"user-upload:///{urllib.parse.quote(safe_name)}"
        resolved_type = media_type or mimetypes.guess_type(safe_name)[0]
        fetched = FetchResult(
            requested_url=upload_url,
            final_url=upload_url,
            status=200,
            media_type=resolved_type or "application/octet-stream",
            content_disposition=f'attachment; filename="{safe_name}"',
            body=body,
        )
        digest = hashlib.sha256(body).hexdigest()
        if extension == ".zip":
            documents = self._archive_documents(
                opportunity.id, fetched, 0, {digest}
            )
        else:
            documents = [self._file_document(opportunity.id, fetched, 0, digest)]
        assessment = self.classifier.assess(opportunity, documents)
        return RetrievalReport(
            documents=documents,
            pack_status=assessment.status,
            pack_confidence=assessment.confidence,
            portal_url=assessment.portal_url,
            missing_roles=assessment.missing_roles,
        )

    def _seed_urls(self, signal: Signal, opportunity: Opportunity) -> list[str]:
        values: list[str] = []
        if opportunity.url:
            values.append(opportunity.url)
            values.extend(self._known_public_data_urls(opportunity.url))
        else:
            values.extend(
                match.group(0) for match in URL_RE.finditer(html.unescape(signal.body))
            )
            for value in signal.source_metadata.values():
                if isinstance(value, str):
                    values.extend(
                        match.group(0)
                        for match in URL_RE.finditer(html.unescape(value))
                    )
        return [
            url
            for url in self._deduplicate_urls(values)
            if not any(term in url.casefold() for term in SEED_EXCLUSION_TERMS)
        ]

    @staticmethod
    def _known_public_data_urls(url: str) -> list[str]:
        parsed = urllib.parse.urlsplit(url)
        if parsed.hostname not in {
            "find-tender.service.gov.uk",
            "www.find-tender.service.gov.uk",
        }:
            return []
        match = re.search(r"/Notice/(\d{6}-\d{4})", parsed.path, re.IGNORECASE)
        if not match:
            return []
        return [
            "https://www.find-tender.service.gov.uk/api/1.0/"
            f"ocdsReleasePackages/{match.group(1)}"
        ]

    @staticmethod
    def _notice_id(value: str) -> str:
        match = re.search(r"\b\d{6}-\d{4}\b", value)
        return match.group(0) if match else ""

    @staticmethod
    def _official_document_urls(documents: object) -> list[str]:
        if not isinstance(documents, list):
            return []
        urls: list[str] = []
        for document in documents:
            if not isinstance(document, dict):
                continue
            document_type = str(document.get("documentType") or "").casefold()
            url = str(document.get("url") or "").strip()
            if document_type in {
                "biddingdocuments",
                "contractdraft",
                "technicalspecifications",
                "evaluationcriteria",
            } and url.startswith(("http://", "https://")):
                urls.append(url)
        return WebTenderRetriever._deduplicate_urls(urls)

    @staticmethod
    def _humanize_method(value: str) -> str:
        words = re.sub(r"(?<!^)(?=[A-Z])", " ", value).replace("_", " ")
        return words.strip().capitalize()

    @staticmethod
    def _evidence_around_url(text: str, url: str) -> str:
        index = text.find(url)
        if index < 0:
            return "The official notice links directly to this tender route."
        start = max(0, text.rfind("\n", 0, index) + 1)
        end = text.find("\n", index + len(url))
        if end < 0:
            end = min(len(text), index + len(url) + 120)
        evidence = " ".join(text[start:end].split())
        return evidence[:500] or "The official notice links directly to this tender route."

    @staticmethod
    def _route_label(url: str) -> str:
        hostname = (urllib.parse.urlsplit(url).hostname or "").casefold()
        known = (
            ("procontract", "ProContract"),
            ("due-north", "ProContract"),
            ("etenderwales.bravosolution", "eTenderWales"),
            ("bravosolution", "BravoSolution"),
            ("delta-esourcing", "Delta eSourcing"),
            ("in-tend", "In-tend"),
            ("atamis", "Atamis"),
            ("jaggaer", "Jaggaer"),
            ("sell2wales", "Sell2Wales"),
        )
        for token, label in known:
            if token in hostname:
                return label
        clean = hostname.removeprefix("www.")
        return clean or "Tender website"

    @staticmethod
    def _route_from_text(
        text: str, advert_url: str, notice_id: str
    ) -> NoticeAccessRoute:
        urls = WebTenderRetriever._deduplicate_urls(
            [match.group(0) for match in URL_RE.finditer(html.unescape(text))]
        )
        emails = EMAIL_RE.findall(html.unescape(text))
        route_urls = [
            url
            for url in urls
            if any(term in url.casefold() for term in TENDER_LINK_TERMS)
            and WebTenderRetriever._canonical_url(url)
            != WebTenderRetriever._canonical_url(advert_url)
        ]
        if route_urls:
            return NoticeAccessRoute(
                status="resolved",
                access_type="external_portal",
                label=WebTenderRetriever._route_label(route_urls[0]),
                url=route_urls[0],
                evidence="Explicit tender link in the inbound alert.",
                submission_url=route_urls[0],
                notice_id=notice_id,
            )
        lowered = text.casefold()
        if emails and any(term in lowered for term in {"submit", "submission", "tender"}):
            return NoticeAccessRoute(
                status="resolved",
                access_type="email_request",
                label="Tender contact email",
                email=emails[0],
                evidence="Explicit tender email in the inbound alert.",
                notice_id=notice_id,
            )
        return NoticeAccessRoute(
            status="advert_only" if advert_url else "not_stated",
            access_type="advert",
            label="Open tender advert" if advert_url else "Route not stated",
            url=advert_url,
            evidence="No separate document route was stated in the alert.",
            notice_id=notice_id,
        )

    @staticmethod
    def _matches_opportunity(
        opportunity: Opportunity, url: str, content: str = ""
    ) -> bool:
        reference_match = re.search(
            r"\b\d{6}-\d{4}\b", f"{opportunity.url} {opportunity.summary}"
        )
        haystack = f"{urllib.parse.unquote(url)} {content}".casefold()
        if reference_match and reference_match.group(0).casefold() in haystack:
            return True
        title_tokens = {
            token
            for token in re.findall(r"[a-z0-9]+", opportunity.title.casefold())
            if len(token) >= 4 and token not in MATCH_STOPWORDS
        }
        organization_tokens = {
            token
            for token in re.findall(r"[a-z0-9]+", opportunity.organization.casefold())
            if len(token) >= 4 and token not in MATCH_STOPWORDS
        }
        title_hits = sum(token in haystack for token in title_tokens)
        organization_hits = sum(token in haystack for token in organization_tokens)
        required_title_hits = min(3, max(2, len(title_tokens)))
        return title_hits >= required_title_hits and organization_hits >= 1

    @staticmethod
    def _deduplicate_urls(urls: list[str]) -> list[str]:
        result: list[str] = []
        seen: set[str] = set()
        for raw_url in urls:
            url = html.unescape(raw_url.strip()).rstrip(".,;:!?)\"]}'")
            if not url.lower().startswith(("http://", "https://")):
                continue
            canonical = WebTenderRetriever._canonical_url(url)
            if canonical not in seen:
                seen.add(canonical)
                result.append(url)
        return result

    @staticmethod
    def _canonical_url(url: str) -> str:
        parsed = urllib.parse.urlsplit(url)
        path = parsed.path or "/"
        query = urllib.parse.urlencode(
            [
                (key, value)
                for key, value in urllib.parse.parse_qsl(
                    parsed.query, keep_blank_values=True
                )
                if not key.casefold().startswith("utm_")
            ]
        )
        return urllib.parse.urlunsplit(
            (parsed.scheme.lower(), parsed.netloc.lower(), path, query, "")
        )

    @staticmethod
    def _is_html(fetched: FetchResult) -> bool:
        return fetched.media_type in {"text/html", "application/xhtml+xml"}

    @staticmethod
    def _is_zip(fetched: FetchResult) -> bool:
        return fetched.media_type in {"application/zip", "application/x-zip-compressed"} or (
            urllib.parse.urlsplit(fetched.final_url).path.lower().endswith(".zip")
        )

    @staticmethod
    def _is_supported_file(fetched: FetchResult) -> bool:
        extension = Path(urllib.parse.urlsplit(fetched.final_url).path).suffix.lower()
        return extension in DOCUMENT_EXTENSIONS or fetched.media_type in TEXT_MEDIA_TYPES or (
            fetched.media_type
            in {
                "application/msword",
                "application/pdf",
                "application/rtf",
                "application/vnd.ms-excel",
                "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            }
        )

    def _html_document(
        self, opportunity_id: str, fetched: FetchResult, depth: int, digest: str
    ) -> tuple[TenderDocument, list[tuple[str, str]]]:
        raw_text = fetched.body.decode("utf-8", errors="replace")
        parser = _TenderHTMLParser()
        parser.feed(raw_text)
        text = "\n".join(parser.text_parts)[: self.max_text_chars]
        return (
            TenderDocument(
                opportunity_id=opportunity_id,
                source_url=fetched.requested_url,
                final_url=fetched.final_url,
                title=parser.title or fetched.final_url,
                media_type=fetched.media_type,
                filename=self._safe_filename(fetched, ".html"),
                text_content=text,
                content_hash=digest,
                size_bytes=len(fetched.body),
                depth=depth,
            ),
            parser.links,
        )

    def _file_document(
        self, opportunity_id: str, fetched: FetchResult, depth: int, digest: str
    ) -> TenderDocument:
        filename = self._safe_filename(fetched)
        path = self._write_file(opportunity_id, filename, digest, fetched.body)
        text_content = ""
        if fetched.media_type in TEXT_MEDIA_TYPES or Path(filename).suffix.lower() in {
            ".csv",
            ".json",
            ".tsv",
            ".txt",
            ".xml",
        }:
            text_content = fetched.body.decode("utf-8", errors="replace")[: self.max_text_chars]
        return TenderDocument(
            opportunity_id=opportunity_id,
            source_url=fetched.requested_url,
            final_url=fetched.final_url,
            title=filename,
            media_type=fetched.media_type,
            filename=filename,
            local_path=str(path),
            text_content=text_content,
            content_hash=digest,
            size_bytes=len(fetched.body),
            depth=depth,
        )

    def _archive_documents(
        self,
        opportunity_id: str,
        fetched: FetchResult,
        depth: int,
        hashes: set[str],
    ) -> list[TenderDocument]:
        documents: list[TenderDocument] = []
        try:
            archive = zipfile.ZipFile(BytesIO(fetched.body))
        except zipfile.BadZipFile as exc:
            return [
                self._failed_document(
                    opportunity_id, fetched.final_url, depth, DocumentStatus.FAILED, exc
                )
            ]
        total_uncompressed = 0
        with archive:
            for info in archive.infolist()[:50]:
                if info.is_dir() or info.file_size > self.http_client.max_resource_bytes:
                    continue
                extension = Path(info.filename).suffix.lower()
                if extension not in DOCUMENT_EXTENSIONS - {".zip", ".html", ".htm"}:
                    continue
                total_uncompressed += info.file_size
                if total_uncompressed > self.max_total_bytes:
                    break
                body = archive.read(info)
                digest = hashlib.sha256(body).hexdigest()
                if digest in hashes:
                    continue
                hashes.add(digest)
                filename = self._sanitize_filename(Path(info.filename).name, extension)
                media_type = mimetypes.guess_type(filename)[0] or "application/octet-stream"
                synthetic_url = f"{fetched.final_url}#archive={urllib.parse.quote(info.filename)}"
                member = FetchResult(
                    requested_url=synthetic_url,
                    final_url=synthetic_url,
                    status=200,
                    media_type=media_type,
                    content_disposition=f'attachment; filename="{filename}"',
                    body=body,
                )
                documents.append(self._file_document(opportunity_id, member, depth, digest))
        return documents

    def _rank_links(self, page_url: str, links: list[tuple[str, str]]) -> list[str]:
        page_host = urllib.parse.urlsplit(page_url).hostname
        ranked: list[tuple[int, str]] = []
        for raw_href, anchor_text in links:
            candidate = urllib.parse.urljoin(page_url, html.unescape(raw_href))
            parsed = urllib.parse.urlsplit(candidate)
            if parsed.scheme not in {"http", "https"}:
                continue
            extension = Path(parsed.path).suffix.lower()
            haystack = f"{parsed.path} {parsed.query} {anchor_text}".casefold()
            keyword_score = sum(term in haystack for term in TENDER_LINK_TERMS)
            same_host = parsed.hostname == page_host
            is_document = extension in DOCUMENT_EXTENSIONS
            score = (12 if is_document else 0) + keyword_score * 2
            if same_host:
                score += 2
            if is_document or keyword_score:
                ranked.append((score, candidate))
        ranked.sort(key=lambda item: (-item[0], len(item[1])))
        return self._deduplicate_urls([url for _, url in ranked])[:40]

    def _embedded_tender_links(
        self, text: str, opportunity: Opportunity
    ) -> list[str]:
        ranked: list[tuple[int, str]] = []
        for match in URL_RE.finditer(html.unescape(text)):
            candidate = re.split(
                r"\\(?:n|r|t|u[0-9a-fA-F]{4})", match.group(0), maxsplit=1
            )[0].rstrip(".,;:!?)\"]}'\\")
            parsed = urllib.parse.urlsplit(candidate)
            haystack = f"{parsed.hostname} {parsed.path} {parsed.query}".casefold()
            if (
                "open-contracting-extensions" in haystack
                or parsed.path.casefold().endswith("/extension.json")
                or parsed.hostname == "standard.open-contracting.org"
                or parsed.path.casefold() == "/government/publications/open-contracting"
            ):
                continue
            keyword_score = sum(term in haystack for term in TENDER_LINK_TERMS)
            relevance_score = 3 if self._matches_opportunity(opportunity, candidate) else 0
            if keyword_score or relevance_score:
                ranked.append((keyword_score * 2 + relevance_score, candidate))
        ranked.sort(key=lambda item: (-item[0], len(item[1])))
        return self._deduplicate_urls([url for _, url in ranked])[:20]

    def _safe_filename(self, fetched: FetchResult, fallback_extension: str = ".bin") -> str:
        header_match = re.search(
            r"filename\*?=(?:UTF-8''|\")?([^\";]+)",
            fetched.content_disposition,
            re.IGNORECASE,
        )
        if header_match:
            name = urllib.parse.unquote(header_match.group(1).strip().strip('"'))
        else:
            name = urllib.parse.unquote(
                Path(urllib.parse.urlsplit(fetched.final_url).path).name
            )
        guessed_extension = mimetypes.guess_extension(fetched.media_type) or fallback_extension
        return self._sanitize_filename(name, guessed_extension)

    @staticmethod
    def _sanitize_filename(name: str, fallback_extension: str) -> str:
        name = re.sub(r"[^A-Za-z0-9._ -]+", "_", name).strip(" ._")[:120]
        if not name:
            name = f"tender-document{fallback_extension}"
        existing_extension = Path(name).suffix.lower()
        if existing_extension and existing_extension not in DOCUMENT_EXTENSIONS:
            name = f"{Path(name).stem}{fallback_extension}"
        elif not existing_extension and fallback_extension:
            name = f"{name}{fallback_extension}"
        return name

    def _write_file(
        self, opportunity_id: str, filename: str, digest: str, body: bytes
    ) -> Path:
        destination = self.document_root / opportunity_id
        destination.mkdir(parents=True, exist_ok=True)
        path = destination / f"{digest[:12]}-{filename}"
        path.write_bytes(body)
        return path

    @staticmethod
    def _failed_document(
        opportunity_id: str,
        url: str,
        depth: int,
        status: DocumentStatus,
        error: Exception,
    ) -> TenderDocument:
        return TenderDocument(
            opportunity_id=opportunity_id,
            source_url=url,
            final_url=url,
            title=url,
            depth=depth,
            status=status,
            error=f"{type(error).__name__}: {error}"[:1000],
        )
