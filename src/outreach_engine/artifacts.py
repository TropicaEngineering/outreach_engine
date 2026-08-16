from __future__ import annotations

import json
import re
import shutil
import subprocess
import threading
import zipfile
from hashlib import sha256
from pathlib import Path
from typing import Any

from outreach_engine.storage import SQLiteRepository


MEDIA_TYPES = {
    "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "zip": "application/zip",
}
ARTIFACT_BUILDER_VERSION = "4"


def _slug(value: str, fallback: str = "bid-response") -> str:
    value = value.casefold().replace("&", " and ")
    value = re.sub(r"[^a-z0-9]+", "-", value).strip("-")
    return value[:72] or fallback


def _normalise(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", value.casefold()).strip()


def _markdown_sections(body: str) -> list[dict[str, str]]:
    pattern = re.compile(
        r"^###\s+(.+?)\s*$\n(.*?)(?=^###\s+|^##\s+Pricing schedule\s*$|\Z)",
        re.MULTILINE | re.DOTALL,
    )
    return [
        {"title": match.group(1).strip(), "content": _clean_content(match.group(2))}
        for match in pattern.finditer(body)
    ]


def _clean_content(value: str) -> str:
    lines = []
    for line in value.strip().splitlines():
        if line.strip().casefold().startswith("source:"):
            continue
        lines.append(line.rstrip())
    return "\n".join(lines).strip()


def _markdown_pricing(body: str) -> dict[str, Any]:
    marker = re.search(r"^##\s+Pricing schedule\s*$", body, re.MULTILINE)
    if marker is None:
        return {}
    section = body[marker.end() :]
    target_match = re.search(r"\*\*Total price:\*\*\s*(.+)", section)
    line_items: list[dict[str, str]] = []
    table_rows = [line.strip() for line in section.splitlines() if line.strip().startswith("|")]
    if len(table_rows) >= 3:
        for raw_row in table_rows[2:]:
            cells = [cell.strip() for cell in raw_row.strip("|").split("|")]
            if len(cells) < 6:
                continue
            line_items.append(
                dict(
                    zip(
                        ("item", "quantity", "unit", "unit_price", "total", "basis"),
                        cells[:6],
                        strict=True,
                    )
                )
            )
    assumptions_match = re.search(r"\*\*Pricing assumptions\*\*(.*)", section, re.DOTALL)
    assumptions = []
    if assumptions_match:
        assumptions = [
            match.group(1).strip()
            for match in re.finditer(r"^-\s+(.+)$", assumptions_match.group(1), re.MULTILINE)
        ]
    return {
        "target_total": target_match.group(1).strip() if target_match else "",
        "line_items": line_items,
        "assumptions": assumptions,
    }


def plan_submission_pack(
    detail: dict[str, Any], bid_profile: dict[str, Any]
) -> dict[str, Any]:
    """Turn the model's submission map into an explicit, deterministic file plan."""
    metadata = detail.get("draft_metadata") or {}
    stored_payload = metadata.get("artifact_payload") or {}
    parsed_sections = _markdown_sections(str(detail.get("draft_body") or ""))
    parsed_by_title = {_normalise(item["title"]): item["content"] for item in parsed_sections}

    source_deliverables = stored_payload.get("deliverables") or metadata.get("deliverables") or []
    deliverables: list[dict[str, Any]] = []
    for item in source_deliverables:
        title = str(item.get("title") or "Bid response section").strip()
        content = str(item.get("draft_content") or "").strip()
        if not content:
            content = parsed_by_title.get(_normalise(title), "")
        deliverables.append({**item, "title": title, "draft_content": _clean_content(content)})

    if not deliverables:
        deliverables = [
            {
                "title": item["title"],
                "deliverable_type": "narrative_response",
                "status": "drafted",
                "purpose": "Buyer-facing response section",
                "draft_content": item["content"],
            }
            for item in parsed_sections
        ]

    checklist = list(metadata.get("submission_checklist") or [])
    separate_items = [
        item for item in checklist if item.get("handling") == "separate_attachment"
    ]
    separate_deliverables: list[dict[str, Any]] = []
    main_deliverables: list[dict[str, Any]] = []
    used_separate_entries: set[int] = set()

    for deliverable in deliverables:
        title_key = _normalise(str(deliverable.get("title") or ""))
        matched_index = None
        for index, entry in enumerate(separate_items):
            haystack = _normalise(f"{entry.get('item', '')} {entry.get('output', '')}")
            if title_key and (title_key in haystack or haystack in title_key):
                matched_index = index
                break
        can_generate = bool(deliverable.get("draft_content")) and deliverable.get(
            "status"
        ) != "missing_template"
        if matched_index is not None and can_generate:
            separate_deliverables.append(deliverable)
            used_separate_entries.add(matched_index)
        elif can_generate:
            main_deliverables.append(deliverable)

    opportunity_slug = _slug(str(detail.get("title") or "bid-response"))
    files: list[dict[str, Any]] = []
    if main_deliverables:
        files.append(
            {
                "id": "bid-response",
                "kind": "docx",
                "filename": f"{opportunity_slug}-bid-response.docx",
                "label": "Editable bid response",
                "reason": f"{len(main_deliverables)} buyer-facing section(s) combined in order",
                "sections": main_deliverables,
            }
        )

    pricing = dict(stored_payload.get("pricing_schedule") or metadata.get("pricing") or {})
    markdown_pricing = _markdown_pricing(str(detail.get("draft_body") or ""))
    if not pricing.get("line_items"):
        pricing["line_items"] = markdown_pricing.get("line_items", [])
    if not pricing.get("assumptions"):
        pricing["assumptions"] = markdown_pricing.get("assumptions", [])
    if not pricing.get("target_total"):
        pricing["target_total"] = markdown_pricing.get("target_total", "")
    pricing["strategy_note"] = re.sub(
        r"\b1E\+1%", "10%", str(pricing.get("strategy_note") or "")
    )
    for line_item in pricing.get("line_items", []):
        basis = str(line_item.get("basis") or "").strip()
        repeated = "Planning allocation requiring commercial approval"
        if basis.casefold().count(repeated.casefold()) > 1:
            line_item["basis"] = f"{repeated}."
    if pricing.get("required"):
        files.append(
            {
                "id": "pricing-schedule",
                "kind": "xlsx",
                "filename": f"{opportunity_slug}-pricing-schedule.xlsx",
                "label": "Editable pricing schedule",
                "reason": "Kept separate from the narrative response for upload and commercial review",
                "pricing": pricing,
            }
        )

    for index, deliverable in enumerate(separate_deliverables, start=1):
        file_id = f"attachment-{index}-{_slug(str(deliverable.get('title') or 'attachment'))}"
        files.append(
            {
                "id": file_id,
                "kind": "docx",
                "filename": f"{opportunity_slug}-{_slug(str(deliverable.get('title')))}.docx",
                "label": str(deliverable.get("title") or "Separate attachment"),
                "reason": "The buyer asks for this as a separate attachment",
                "sections": [deliverable],
            }
        )

    actions: list[dict[str, str]] = []
    for entry in checklist:
        handling = str(entry.get("handling") or "generated_in_pack")
        if handling == "generated_in_pack":
            continue
        if handling == "separate_attachment":
            entry_index = separate_items.index(entry)
            if entry_index in used_separate_entries:
                continue
        actions.append(
            {
                "item": str(entry.get("item") or "Submission action"),
                "handling": handling,
                "status": str(entry.get("status") or "to_check"),
                "output": str(entry.get("output") or ""),
                "source": str(entry.get("source") or ""),
            }
        )

    return {
        "title": str(detail.get("draft_subject") or detail.get("title") or "Bid response"),
        "opportunityTitle": str(detail.get("title") or "Bid response"),
        "buyer": str(detail.get("organization") or "Buyer"),
        "bidder": str(bid_profile.get("company_name") or "Bidder"),
        "deadline": str(detail.get("deadline") or ""),
        "sourceUrl": str(detail.get("url") or ""),
        "requirementsAssurance": str(
            (metadata.get("brief") or {}).get("requirements_assurance") or ""
        ),
        "files": files,
        "actions": actions,
        "checklist": checklist,
    }


class SubmissionArtifactService:
    def __init__(
        self,
        repository: SQLiteRepository,
        artifact_root: Path | str = "./data/private/artifacts",
        *,
        node_executable: str = "node",
        builder_path: Path | str | None = None,
    ) -> None:
        self.repository = repository
        self.artifact_root = Path(artifact_root)
        self.node_executable = node_executable
        project_root = Path(__file__).resolve().parents[2]
        self.builder_path = Path(builder_path or project_root / "scripts" / "build_submission_pack.mjs")
        self._lock = threading.Lock()

    def build(self, opportunity_id: str) -> dict[str, Any]:
        detail = self.repository.get_opportunity_detail(opportunity_id)
        if detail is None:
            raise LookupError("Opportunity not found.")
        if not detail.get("draft_id"):
            raise ValueError("Build the response before preparing editable files.")

        plan = plan_submission_pack(detail, self.repository.get_bid_profile())
        revision_source = (
            f"{ARTIFACT_BUILDER_VERSION}:{detail.get('draft_id')}:"
            f"{detail.get('draft_metadata')}:{detail.get('draft_body')}"
        )
        revision = sha256(revision_source.encode("utf-8")).hexdigest()[:16]
        output_dir = self.artifact_root / opportunity_id / revision
        manifest_path = output_dir / "manifest.json"

        with self._lock:
            if manifest_path.exists():
                manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
                if self._manifest_is_complete(output_dir, manifest):
                    return manifest

            output_dir.mkdir(parents=True, exist_ok=True)
            command = [
                self.node_executable,
                str(self.builder_path),
                "--output-dir",
                str(output_dir),
            ]
            completed = subprocess.run(
                command,
                input=json.dumps(plan),
                text=True,
                capture_output=True,
                check=False,
                timeout=90,
            )
            if completed.returncode:
                message = (completed.stderr or completed.stdout or "artifact builder failed").strip()
                raise RuntimeError(message[-1200:])

            generated = json.loads((output_dir / "generated.json").read_text(encoding="utf-8"))
            artifact_files = []
            for item in generated.get("files", []):
                kind = str(item["kind"])
                artifact_files.append(
                    {
                        **item,
                        "media_type": MEDIA_TYPES[kind],
                        "download_url": (
                            f"/api/opportunities/{opportunity_id}/artifacts/{item['id']}"
                        ),
                    }
                )

            if artifact_files:
                zip_name = f"{_slug(str(detail.get('title') or 'bid-response'))}-editable-pack.zip"
                zip_path = output_dir / zip_name
                with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as archive:
                    for item in artifact_files:
                        archive.write(output_dir / item["filename"], item["filename"])
                artifact_files.insert(
                    0,
                    {
                        "id": "editable-pack",
                        "kind": "zip",
                        "filename": zip_name,
                        "label": "Complete editable pack",
                        "reason": "All generated submission files in one download",
                        "media_type": MEDIA_TYPES["zip"],
                        "download_url": (
                            f"/api/opportunities/{opportunity_id}/artifacts/editable-pack"
                        ),
                    },
                )

            manifest = {
                "opportunity_id": opportunity_id,
                "revision": revision,
                "files": artifact_files,
                "actions": plan["actions"],
                "requirements_assurance": plan["requirementsAssurance"],
            }
            manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
            return manifest

    def resolve_file(self, opportunity_id: str, artifact_id: str) -> tuple[Path, dict[str, Any]]:
        manifest = self.build(opportunity_id)
        item = next((entry for entry in manifest["files"] if entry["id"] == artifact_id), None)
        if item is None:
            raise LookupError("Submission file not found.")
        output_dir = self.artifact_root / opportunity_id / manifest["revision"]
        path = (output_dir / item["filename"]).resolve()
        if output_dir.resolve() not in path.parents or not path.is_file():
            raise LookupError("Submission file not found.")
        return path, item

    @staticmethod
    def _manifest_is_complete(output_dir: Path, manifest: dict[str, Any]) -> bool:
        files = manifest.get("files") or []
        return bool(files) and all((output_dir / item.get("filename", "")).is_file() for item in files)
