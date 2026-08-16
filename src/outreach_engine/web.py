from __future__ import annotations

import json
import logging
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import TYPE_CHECKING
from urllib.parse import parse_qs, urlparse

from outreach_engine.domain import ReviewStatus
from outreach_engine.storage import SQLiteRepository

if TYPE_CHECKING:
    from outreach_engine.artifacts import SubmissionArtifactService
    from outreach_engine.connectors.base import SignalConnector
    from outreach_engine.engine import OutreachEngine


LOGGER = logging.getLogger(__name__)
ASSET_ROOT = Path(__file__).resolve().parent / "web_assets"


def _handler(
    repository: SQLiteRepository,
    engine: OutreachEngine | None = None,
    inbox_connector: SignalConnector | None = None,
    artifact_service: SubmissionArtifactService | None = None,
):
    class ReviewHandler(BaseHTTPRequestHandler):
        server_version = "SignalRoute/0.1"

        def do_GET(self) -> None:  # noqa: N802
            path = urlparse(self.path).path
            if path == "/":
                return self._asset("index.html", "text/html; charset=utf-8")
            if path == "/assets/styles.css":
                return self._asset("styles.css", "text/css; charset=utf-8")
            if path == "/assets/app.js":
                return self._asset("app.js", "text/javascript; charset=utf-8")
            if path == "/favicon.svg":
                return self._asset("favicon.svg", "image/svg+xml")
            if path == "/api/health":
                return self._json({"status": "ok", "service": "signal-route"})
            if path == "/api/opportunities":
                return self._json({"items": repository.list_opportunities()})
            if path == "/api/bid-profile":
                return self._json(repository.get_bid_profile())
            prefix = "/api/opportunities/"
            if path.startswith(prefix):
                parts = path.strip("/").split("/")
                if len(parts) == 4 and parts[3] == "artifacts":
                    return self._build_artifacts(parts[2])
                if len(parts) == 5 and parts[3] == "artifacts":
                    return self._download_artifact(parts[2], parts[4])
                opportunity_id = path.removeprefix(prefix)
                detail = repository.get_opportunity_detail(opportunity_id)
                if detail is None:
                    return self._json({"error": "not_found"}, HTTPStatus.NOT_FOUND)
                return self._json(detail)
            return self._json({"error": "not_found"}, HTTPStatus.NOT_FOUND)

        def _build_artifacts(self, opportunity_id: str) -> None:
            if artifact_service is None:
                return self._json(
                    {
                        "error": "artifacts_unavailable",
                        "message": "Editable downloads are not configured.",
                    },
                    HTTPStatus.SERVICE_UNAVAILABLE,
                )
            try:
                return self._json(artifact_service.build(opportunity_id))
            except LookupError as exc:
                return self._json(
                    {"error": "not_found", "message": str(exc)}, HTTPStatus.NOT_FOUND
                )
            except ValueError as exc:
                return self._json(
                    {"error": "not_ready", "message": str(exc)},
                    HTTPStatus.UNPROCESSABLE_ENTITY,
                )
            except Exception:
                LOGGER.exception("artifact build failed opportunity_id=%s", opportunity_id)
                return self._json(
                    {
                        "error": "artifact_build_failed",
                        "message": "Editable files could not be prepared. The bid draft is safe.",
                    },
                    HTTPStatus.BAD_GATEWAY,
                )

        def _download_artifact(self, opportunity_id: str, artifact_id: str) -> None:
            if artifact_service is None:
                return self._json(
                    {"error": "artifacts_unavailable"}, HTTPStatus.SERVICE_UNAVAILABLE
                )
            try:
                path, item = artifact_service.resolve_file(opportunity_id, artifact_id)
            except LookupError as exc:
                return self._json(
                    {"error": "not_found", "message": str(exc)}, HTTPStatus.NOT_FOUND
                )
            except Exception:
                LOGGER.exception(
                    "artifact download failed opportunity_id=%s artifact_id=%s",
                    opportunity_id,
                    artifact_id,
                )
                return self._json(
                    {"error": "artifact_download_failed"}, HTTPStatus.BAD_GATEWAY
                )
            body = path.read_bytes()
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", item["media_type"])
            self.send_header("Content-Length", str(len(body)))
            self.send_header(
                "Content-Disposition", f'attachment; filename="{item["filename"]}"'
            )
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def do_POST(self) -> None:  # noqa: N802
            path = urlparse(self.path).path
            parts = path.strip("/").split("/")
            if parts == ["api", "bid-profile"]:
                return self._save_bid_profile()
            if parts == ["api", "inbox", "sync"]:
                return self._sync_inbox()
            if len(parts) == 4 and parts[:2] == ["api", "opportunities"]:
                opportunity_id, action = parts[2], parts[3]
                if action == "upload":
                    return self._upload_bid_pack(opportunity_id)
                if action == "inputs":
                    return self._save_bid_inputs(opportunity_id)
                if action in {"route", "pack", "draft"}:
                    return self._run_opportunity_stage(opportunity_id, action)
            if len(parts) == 4 and parts[:2] == ["api", "drafts"]:
                draft_id, action = parts[2], parts[3]
                if action not in {"approve", "reject"}:
                    return self._json({"error": "invalid_action"}, HTTPStatus.BAD_REQUEST)
                status = (
                    ReviewStatus.APPROVED if action == "approve" else ReviewStatus.REJECTED
                )
                if repository.review_draft(draft_id, status):
                    return self._json({"draft_id": draft_id, "status": status.value})
                return self._json({"error": "not_found"}, HTTPStatus.NOT_FOUND)
            return self._json({"error": "not_found"}, HTTPStatus.NOT_FOUND)

        def _save_bid_inputs(self, opportunity_id: str) -> None:
            if engine is None:
                return self._json(
                    {"error": "actions_unavailable", "message": "Bid inputs are unavailable."},
                    HTTPStatus.SERVICE_UNAVAILABLE,
                )
            try:
                length = int(self.headers.get("Content-Length", "0"))
            except ValueError:
                length = 0
            if length <= 0 or length > 250_000:
                return self._json(
                    {"error": "invalid_inputs", "message": "Bid inputs are too large."},
                    HTTPStatus.BAD_REQUEST,
                )
            try:
                payload = json.loads(self.rfile.read(length).decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError):
                return self._json(
                    {"error": "invalid_json", "message": "Bid inputs are not valid JSON."},
                    HTTPStatus.BAD_REQUEST,
                )
            inputs = payload.get("inputs") if isinstance(payload, dict) else None
            try:
                engine.save_bid_inputs(opportunity_id, inputs)
                if payload.get("regenerate", True):
                    engine.create_draft(opportunity_id, require_pack=True)
            except LookupError as exc:
                return self._json(
                    {"error": "not_found", "message": str(exc)}, HTTPStatus.NOT_FOUND
                )
            except ValueError as exc:
                return self._json(
                    {"error": "invalid_inputs", "message": str(exc)},
                    HTTPStatus.UNPROCESSABLE_ENTITY,
                )
            except Exception:
                LOGGER.exception("bid inputs failed opportunity_id=%s", opportunity_id)
                return self._json(
                    {
                        "error": "inputs_failed",
                        "message": (
                            "The inputs were saved, but the response could not be regenerated. "
                            "Use Regenerate to retry."
                        ),
                    },
                    HTTPStatus.BAD_GATEWAY,
                )
            detail = repository.get_opportunity_detail(opportunity_id)
            return self._json(detail or {"error": "not_found"})

        def do_DELETE(self) -> None:  # noqa: N802
            path = urlparse(self.path).path
            parts = path.strip("/").split("/")
            if (
                len(parts) == 5
                and parts[:2] == ["api", "opportunities"]
                and parts[3] == "documents"
            ):
                return self._remove_bid_pack_document(parts[2], parts[4])
            return self._json({"error": "not_found"}, HTTPStatus.NOT_FOUND)

        def _remove_bid_pack_document(
            self, opportunity_id: str, document_id: str
        ) -> None:
            if engine is None:
                return self._json(
                    {"error": "actions_unavailable", "message": "Removal is unavailable."},
                    HTTPStatus.SERVICE_UNAVAILABLE,
                )
            try:
                engine.remove_bid_pack_document(opportunity_id, document_id)
            except LookupError as exc:
                return self._json(
                    {"error": "not_found", "message": str(exc)}, HTTPStatus.NOT_FOUND
                )
            except ValueError as exc:
                return self._json(
                    {"error": "invalid_action", "message": str(exc)},
                    HTTPStatus.UNPROCESSABLE_ENTITY,
                )
            except Exception:
                LOGGER.exception(
                    "bid-pack removal failed opportunity_id=%s document_id=%s",
                    opportunity_id,
                    document_id,
                )
                return self._json(
                    {
                        "error": "remove_failed",
                        "message": "The document could not be removed. Nothing was changed.",
                    },
                    HTTPStatus.BAD_GATEWAY,
                )
            detail = repository.get_opportunity_detail(opportunity_id)
            return self._json(detail or {"error": "not_found"})

        def _save_bid_profile(self) -> None:
            try:
                length = int(self.headers.get("Content-Length", "0"))
            except ValueError:
                length = 0
            if length <= 0 or length > 100_000:
                return self._json(
                    {"error": "invalid_profile", "message": "Bid profile is too large."},
                    HTTPStatus.BAD_REQUEST,
                )
            try:
                payload = json.loads(self.rfile.read(length).decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError):
                return self._json(
                    {"error": "invalid_json", "message": "Bid profile is not valid JSON."},
                    HTTPStatus.BAD_REQUEST,
                )
            if not isinstance(payload, dict):
                return self._json(
                    {"error": "invalid_profile", "message": "Bid profile must be an object."},
                    HTTPStatus.BAD_REQUEST,
                )
            return self._json(repository.save_bid_profile(payload))

        def _sync_inbox(self) -> None:
            if engine is None or inbox_connector is None:
                return self._json(
                    {
                        "error": "inbox_unavailable",
                        "message": "Gmail sync is not configured for this review console.",
                    },
                    HTTPStatus.SERVICE_UNAVAILABLE,
                )
            try:
                signals = inbox_connector.pull(limit=5)
                results = []
                for signal in signals:
                    result = engine.process(
                        signal,
                        run_retrieval=False,
                        run_draft=False,
                    )
                    results.append(result)
                    if not result.error and result.opportunity is not None:
                        engine.resolve_access_route(result.opportunity.id)
            except Exception:
                LOGGER.exception("inbox sync failed")
                return self._json(
                    {
                        "error": "inbox_sync_failed",
                        "message": (
                            "Gmail could not be checked. Existing opportunities are safe; "
                            "check the connection and retry."
                        ),
                    },
                    HTTPStatus.BAD_GATEWAY,
                )
            failures = sum(bool(result.error) for result in results)
            duplicates = sum(result.duplicate for result in results)
            return self._json(
                {
                    "signals": len(results),
                    "new": len(results) - duplicates - failures,
                    "duplicates": duplicates,
                    "failures": failures,
                }
            )

        def _run_opportunity_stage(self, opportunity_id: str, action: str) -> None:
            if engine is None:
                return self._json(
                    {
                        "error": "actions_unavailable",
                        "message": "Start the review console with the configured action engine.",
                    },
                    HTTPStatus.SERVICE_UNAVAILABLE,
                )
            try:
                if action == "route":
                    engine.resolve_access_route(opportunity_id, force=True)
                elif action == "pack":
                    engine.retrieve_pack(opportunity_id)
                else:
                    engine.create_draft(opportunity_id, require_pack=True)
            except LookupError as exc:
                return self._json(
                    {"error": "not_found", "message": str(exc)}, HTTPStatus.NOT_FOUND
                )
            except ValueError as exc:
                return self._json(
                    {"error": "stage_not_ready", "message": str(exc)},
                    HTTPStatus.UNPROCESSABLE_ENTITY,
                )
            except Exception:
                LOGGER.exception(
                    "opportunity stage failed opportunity_id=%s action=%s",
                    opportunity_id,
                    action,
                )
                message = (
                    "The source pack could not be collected. Retry or open the notice manually."
                    if action == "pack"
                    else "The draft could not be created. Your source pack is safe; retry shortly."
                )
                return self._json(
                    {"error": f"{action}_failed", "message": message},
                    HTTPStatus.BAD_GATEWAY,
                )
            detail = repository.get_opportunity_detail(opportunity_id)
            return self._json(detail or {"error": "not_found"})

        def _upload_bid_pack(self, opportunity_id: str) -> None:
            if engine is None:
                return self._json(
                    {"error": "actions_unavailable", "message": "Uploads are unavailable."},
                    HTTPStatus.SERVICE_UNAVAILABLE,
                )
            query = parse_qs(urlparse(self.path).query)
            filename = (query.get("filename") or [""])[0].strip()
            media_type = (query.get("media_type") or [""])[0].strip()
            if not filename:
                return self._json(
                    {"error": "filename_required", "message": "Choose a tender file."},
                    HTTPStatus.BAD_REQUEST,
                )
            try:
                length = int(self.headers.get("Content-Length", "0"))
            except ValueError:
                length = 0
            if length <= 0 or length > 20_000_000:
                return self._json(
                    {
                        "error": "invalid_file_size",
                        "message": "Each bid-pack file must be smaller than 20 MB.",
                    },
                    HTTPStatus.REQUEST_ENTITY_TOO_LARGE,
                )
            body = self.rfile.read(length)
            try:
                engine.upload_bid_pack_file(
                    opportunity_id, filename, media_type, body
                )
            except LookupError as exc:
                return self._json(
                    {"error": "not_found", "message": str(exc)}, HTTPStatus.NOT_FOUND
                )
            except ValueError as exc:
                return self._json(
                    {"error": "invalid_upload", "message": str(exc)},
                    HTTPStatus.UNPROCESSABLE_ENTITY,
                )
            except Exception:
                LOGGER.exception("bid-pack upload failed opportunity_id=%s", opportunity_id)
                return self._json(
                    {
                        "error": "upload_failed",
                        "message": "The file could not be saved. Existing work is safe.",
                    },
                    HTTPStatus.BAD_GATEWAY,
                )
            detail = repository.get_opportunity_detail(opportunity_id)
            return self._json(detail or {"error": "not_found"})

        def log_message(self, format: str, *args: object) -> None:
            LOGGER.info("review_console %s", format % args)

        def _asset(self, filename: str, content_type: str) -> None:
            body = (ASSET_ROOT / filename).read_bytes()
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def _json(self, payload: object, status: HTTPStatus = HTTPStatus.OK) -> None:
            body = json.dumps(payload, default=str).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

    return ReviewHandler


def serve(
    repository: SQLiteRepository,
    host: str,
    port: int,
    *,
    engine: OutreachEngine | None = None,
    inbox_connector: SignalConnector | None = None,
    artifact_service: SubmissionArtifactService | None = None,
) -> None:
    server = ThreadingHTTPServer(
        (host, port), _handler(repository, engine, inbox_connector, artifact_service)
    )
    print(f"SignalRoute review console: http://{host}:{port}")
    print("Press Ctrl+C to stop.")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
