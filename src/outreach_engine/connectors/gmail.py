from __future__ import annotations

import base64
import html
import logging
import re
from pathlib import Path
from typing import Any

from outreach_engine.domain import Signal


SCOPES = ["https://www.googleapis.com/auth/gmail.readonly"]
LOGGER = logging.getLogger(__name__)
FIND_A_TENDER_NOTICE_RE = re.compile(
    r"(?m)^(?P<title>[^\r\n]+?):\s+"
    r"(?P<url>https://www\.find-tender\.service\.gov\.uk/Notice/"
    r"(?P<notice_id>\d{6}-\d{4})[^\s]*)\s*$",
    re.IGNORECASE,
)
FIND_A_TENDER_FOOTER_MARKERS = (
    "\nYou can change the language we use in emails",
    "\nGallwch newid yr iaith",
)


class GmailConnector:
    """Pull messages from Gmail without modifying mailbox state."""

    name = "gmail"

    def __init__(
        self,
        credentials_path: Path,
        token_path: Path,
        label: str,
        query: str = "",
    ):
        self.credentials_path = credentials_path
        self.token_path = token_path
        self.label = label
        self.query = query

    def pull(self, limit: int = 50) -> list[Signal]:
        service = self._service()
        label_id = self._resolve_label_id(service, self.label)
        response = (
            service.users()
            .messages()
            .list(
                userId="me",
                labelIds=[label_id],
                q=self.query or None,
                maxResults=limit,
            )
            .execute()
        )
        signals: list[Signal] = []
        for reference in response.get("messages", []):
            message_id = reference.get("id")
            if not message_id:
                continue
            message = (
                service.users()
                .messages()
                .get(userId="me", id=message_id, format="full")
                .execute()
            )
            payload = message.get("payload", {})
            headers = {
                header.get("name", "").lower(): header.get("value", "")
                for header in payload.get("headers", [])
            }
            signals.extend(
                self._signals_from_message(
                    message_id=message_id,
                    thread_id=message.get("threadId", ""),
                    subject=headers.get("subject", ""),
                    body=self._extract_body(payload).strip(),
                    sender=headers.get("from", ""),
                    recipient=headers.get("to", ""),
                    received_at=headers.get("date", ""),
                )
            )
        deduplicated = {signal.external_id: signal for signal in signals}
        return list(deduplicated.values())

    def _signals_from_message(
        self,
        *,
        message_id: str,
        thread_id: str,
        subject: str,
        body: str,
        sender: str,
        recipient: str,
        received_at: str,
    ) -> list[Signal]:
        sections = self._split_find_a_tender_digest(body)
        base_metadata = {
            "thread_id": thread_id,
            "label": self.label,
            "query": self.query,
            "parent_message_id": message_id,
        }
        if not sections:
            return [
                Signal(
                    source=self.name,
                    external_id=message_id,
                    subject=subject,
                    body=body,
                    sender=sender,
                    recipient=recipient,
                    received_at=received_at,
                    source_metadata=base_metadata,
                )
            ]
        return [
            Signal(
                source=self.name,
                external_id=f"find-tender:{section['notice_id']}",
                subject=section["title"],
                body=section["body"],
                sender=sender,
                recipient=recipient,
                received_at=received_at,
                source_metadata={
                    **base_metadata,
                    "digest_subject": subject,
                    "digest_notice_index": index,
                    "notice_id": section["notice_id"],
                    "notice_url": section["url"],
                },
            )
            for index, section in enumerate(sections, 1)
        ]

    @staticmethod
    def _split_find_a_tender_digest(body: str) -> list[dict[str, str]]:
        matches = list(FIND_A_TENDER_NOTICE_RE.finditer(body))
        sections: list[dict[str, str]] = []
        for index, match in enumerate(matches):
            end = matches[index + 1].start() if index + 1 < len(matches) else len(body)
            section_body = body[match.start() : end]
            for marker in FIND_A_TENDER_FOOTER_MARKERS:
                section_body = section_body.split(marker, 1)[0]
            section_body = re.sub(r"(?m)^[=-]{20,}\s*$", "", section_body).strip()
            sections.append(
                {
                    "title": match.group("title").strip(),
                    "url": match.group("url").strip(),
                    "notice_id": match.group("notice_id"),
                    "body": section_body,
                }
            )
        return sections

    def _service(self) -> Any:
        try:
            from google.auth.transport.requests import Request
            from google.oauth2.credentials import Credentials
            from google_auth_oauthlib.flow import InstalledAppFlow
            from googleapiclient.discovery import build
        except ImportError as exc:
            raise RuntimeError(
                'Gmail dependencies are missing. Install with: pip install -e ".[gmail]"'
            ) from exc

        credentials = None
        if self.token_path.exists():
            # Preserve the token's originally granted scopes. Google rejects refreshes
            # when an existing token is reloaded with a different requested scope.
            credentials = Credentials.from_authorized_user_file(str(self.token_path))
            if set(credentials.scopes or []) != set(SCOPES):
                LOGGER.warning(
                    "existing Gmail token has legacy scopes; reauthorize to reduce access"
                )
        if not credentials or not credentials.valid:
            if credentials and credentials.expired and credentials.refresh_token:
                credentials.refresh(Request())
            else:
                if not self.credentials_path.exists():
                    raise FileNotFoundError(
                        f"Gmail OAuth credentials not found at {self.credentials_path}"
                    )
                flow = InstalledAppFlow.from_client_secrets_file(
                    str(self.credentials_path), SCOPES
                )
                credentials = flow.run_local_server(port=0)
            self.token_path.parent.mkdir(parents=True, exist_ok=True)
            self.token_path.write_text(credentials.to_json(), encoding="utf-8")
            self.token_path.chmod(0o600)
        return build("gmail", "v1", credentials=credentials, cache_discovery=False)

    @staticmethod
    def _resolve_label_id(service: Any, label_name: str) -> str:
        labels = service.users().labels().list(userId="me").execute().get("labels", [])
        label = next(
            (
                item
                for item in labels
                if item.get("name", "").casefold() == label_name.casefold()
                or item.get("id", "").casefold() == label_name.casefold()
            ),
            None,
        )
        if not label:
            raise ValueError(f'Gmail label "{label_name}" was not found')
        return str(label["id"])

    @classmethod
    def _extract_body(cls, payload: dict[str, Any]) -> str:
        plain_parts: list[str] = []
        html_parts: list[str] = []

        def visit(part: dict[str, Any]) -> None:
            data = part.get("body", {}).get("data")
            mime_type = part.get("mimeType", "")
            if data and mime_type in {"text/plain", "text/html"}:
                decoded = cls._decode(data)
                (plain_parts if mime_type == "text/plain" else html_parts).append(decoded)
            for child in part.get("parts", []):
                visit(child)

        visit(payload)
        if plain_parts:
            return "\n".join(plain_parts)
        raw_html = "\n".join(html_parts)
        without_tags = re.sub(r"<[^>]+>", " ", raw_html)
        return re.sub(r"\s+", " ", html.unescape(without_tags))

    @staticmethod
    def _decode(value: str) -> str:
        padded = value + "=" * (-len(value) % 4)
        return base64.urlsafe_b64decode(padded).decode("utf-8", errors="replace")
