from __future__ import annotations

import logging

from outreach_engine.connectors.base import SignalConnector
from outreach_engine.domain import (
    DecisionAction,
    ProcessingResult,
    RetrievalReport,
    Signal,
    SignalStatus,
)
from outreach_engine.playbooks.base import Playbook
from outreach_engine.providers.base import Drafter, Extractor
from outreach_engine.retrieval.base import NullRetriever, TenderRetriever
from outreach_engine.storage import SQLiteRepository


LOGGER = logging.getLogger(__name__)


class OutreachEngine:
    """Idempotent orchestration of extraction, qualification, and drafting."""

    def __init__(
        self,
        repository: SQLiteRepository,
        extractor: Extractor,
        playbook: Playbook,
        drafter: Drafter,
        retriever: TenderRetriever | None = None,
    ):
        self.repository = repository
        self.extractor = extractor
        self.playbook = playbook
        self.drafter = drafter
        self.retriever = retriever or NullRetriever()
        self.repository.initialize()

    def process(
        self,
        incoming: Signal,
        force: bool = False,
        *,
        run_retrieval: bool = True,
        run_draft: bool = True,
    ) -> ProcessingResult:
        signal, duplicate = self.repository.insert_signal(incoming)
        if duplicate and signal.status == SignalStatus.PROCESSED and not force:
            existing = self.repository.load_result_for_signal(signal.id)
            if existing is None:
                raise RuntimeError("processed signal has no stored result")
            existing.duplicate = True
            self.repository.record_event(
                "signal", signal.id, "signal.duplicate_ignored", {"source": signal.source}
            )
            return existing

        self.repository.update_signal_status(signal.id, SignalStatus.PROCESSING)
        self.repository.record_event(
            "signal", signal.id, "signal.processing_started", {"duplicate_retry": duplicate}
        )
        run_id = self.repository.start_run(
            signal.id,
            provider=self.extractor.provider_name,
            model=self.extractor.model_name,
            playbook=self.playbook.name,
        )

        try:
            previous = self.repository.load_result_for_signal(signal.id)
            opportunity = self.extractor.extract(signal)
            if previous and previous.opportunity:
                workflow_attributes = {
                    key: value
                    for key, value in previous.opportunity.attributes.items()
                    if key.startswith(("retrieval_", "draft_", "bid_pack_"))
                }
                opportunity.attributes.update(workflow_attributes)
            opportunity.attributes.setdefault("retrieval_status", "not_run")
            opportunity.attributes.setdefault("draft_status", "not_run")
            opportunity.attributes.setdefault("bid_pack_status", "not_run")
            opportunity.attributes.setdefault("pack_access_status", "not_checked")
            self.repository.save_opportunity(opportunity)
            self.repository.record_event(
                "opportunity",
                opportunity.id,
                "opportunity.extracted",
                {"provider": self.extractor.provider_name, "model": self.extractor.model_name},
            )

            decision = self.playbook.decide(opportunity)
            self.repository.save_decision(decision)
            self.repository.record_event(
                "opportunity",
                opportunity.id,
                "opportunity.qualified",
                {
                    "action": decision.action.value,
                    "score": decision.score,
                    "playbook": decision.playbook,
                    "playbook_version": decision.playbook_version,
                },
            )

            retrieval = RetrievalReport()
            if run_retrieval and decision.action not in {
                DecisionAction.IGNORE,
                DecisionAction.MONITOR,
            }:
                retrieval = self.retrieve_pack(opportunity.id)

            draft = previous.draft if previous else None
            if run_draft and decision.action not in {
                DecisionAction.IGNORE,
                DecisionAction.MONITOR,
            }:
                draft = self.create_draft(opportunity.id)
            elif decision.action in {DecisionAction.IGNORE, DecisionAction.MONITOR} and (
                self.repository.delete_draft_for_opportunity(opportunity.id)
            ):
                draft = None
                self.repository.record_event(
                    "opportunity",
                    opportunity.id,
                    "draft.removed",
                    {"reason": "current decision does not permit outreach"},
                )

            self.repository.update_signal_status(signal.id, SignalStatus.PROCESSED)
            self.repository.finish_run(run_id, "completed")
            signal.status = SignalStatus.PROCESSED
            LOGGER.info(
                "processed signal source=%s external_id=%s action=%s score=%s",
                signal.source,
                signal.external_id,
                decision.action.value,
                decision.score,
            )
            return ProcessingResult(
                signal=signal,
                opportunity=opportunity,
                decision=decision,
                draft=draft,
                documents=retrieval.documents,
                duplicate=duplicate,
            )
        except Exception as exc:
            error = f"{type(exc).__name__}: {exc}"
            self.repository.update_signal_status(signal.id, SignalStatus.FAILED, error)
            self.repository.finish_run(run_id, "failed", error)
            self.repository.record_event(
                "signal", signal.id, "signal.processing_failed", {"error": error[:500]}
            )
            signal.status = SignalStatus.FAILED
            LOGGER.exception(
                "failed to process signal source=%s external_id=%s",
                signal.source,
                signal.external_id,
            )
            return ProcessingResult(
                signal=signal,
                opportunity=None,
                decision=None,
                draft=None,
                duplicate=duplicate,
                error=error,
            )

    def retrieve_pack(self, opportunity_id: str) -> RetrievalReport:
        context = self.repository.load_context_for_opportunity(opportunity_id)
        if context is None:
            raise LookupError("Opportunity not found or qualification is incomplete.")
        signal, opportunity, decision, _ = context
        if decision.action in {DecisionAction.IGNORE, DecisionAction.MONITOR}:
            raise ValueError("This opportunity does not need a source pack.")

        opportunity.attributes.update(
            {"retrieval_status": "running", "retrieval_message": ""}
        )
        self.repository.save_opportunity(opportunity)
        self.repository.record_event(
            "opportunity", opportunity.id, "tender_pack.started", {}
        )
        report = RetrievalReport()
        try:
            report = self.retriever.retrieve(signal, opportunity)
        except Exception as exc:
            LOGGER.exception("tender retrieval failed opportunity_id=%s", opportunity.id)
            report.errors.append(f"{type(exc).__name__}: {exc}")

        self.repository.replace_documents(opportunity.id, report.documents)
        retrieved_count = sum(
            document.status.value == "retrieved" for document in report.documents
        )
        core_count = sum(
            document.status.value == "retrieved" and document.is_core
            for document in report.documents
        )
        issue_count = sum(
            document.status.value in {"blocked", "failed"}
            for document in report.documents
        )
        if report.pack_status == "found":
            message = "A high-confidence bid pack was found. Drafting is ready."
        elif report.pack_status == "portal_required":
            message = (
                "The exact procurement portal was identified. Automated retrieval stopped "
                "at the portal boundary; use the browser handoff to collect the bid pack."
            )
        elif retrieved_count == 0:
            message = (
                "We could not confidently locate the bid pack. Upload the official "
                "tender documents if you have them."
            )
        else:
            message = (
                "Public notice information was found, but no governing bid document "
                "could be identified with high confidence."
            )
        opportunity.attributes.update(
            {
                "retrieval_status": report.status,
                "retrieved_document_count": retrieved_count,
                "retrieval_issue_count": issue_count,
                "retrieval_message": message,
                "bid_pack_status": report.pack_status,
                "bid_pack_confidence": report.pack_confidence,
                "bid_pack_portal_url": report.portal_url,
                "bid_pack_core_count": core_count,
                "bid_pack_missing_roles": report.missing_roles,
            }
        )
        self.repository.save_opportunity(opportunity)
        self.repository.record_event(
            "opportunity",
            opportunity.id,
            "tender_pack.retrieved",
            {
                "status": report.status,
                "documents": retrieved_count,
                "core_documents": core_count,
                "issues": issue_count,
                "discovered_urls": report.discovered_urls,
                "pack_status": report.pack_status,
            },
        )
        return report

    def resolve_access_route(self, opportunity_id: str, *, force: bool = False) -> None:
        """Enrich an opportunity from its exact official notice without broad discovery."""
        context = self.repository.load_context_for_opportunity(opportunity_id)
        if context is None:
            raise LookupError("Opportunity not found or qualification is incomplete.")
        signal, opportunity, _, _ = context
        if (
            not force
            and opportunity.attributes.get("pack_access_status")
            in {"resolved", "advert_only"}
        ):
            return
        resolver = getattr(self.retriever, "resolve_access_route", None)
        if resolver is None:
            return

        route = resolver(signal, opportunity)
        opportunity.attributes.update(
            {
                "pack_access_status": route.status,
                "pack_access_type": route.access_type,
                "pack_access_label": route.label,
                "pack_access_url": route.url,
                "pack_access_email": route.email,
                "pack_access_evidence": route.evidence,
                "pack_document_urls": list(route.document_urls),
                "submission_method": route.submission_method,
                "submission_url": route.submission_url,
                "notice_id": route.notice_id,
                "route_error": route.error,
            }
        )
        if route.deadline:
            opportunity.deadline = route.deadline
        self.repository.save_opportunity(opportunity)
        self.repository.record_event(
            "opportunity",
            opportunity.id,
            "tender_route.resolved",
            {
                "status": route.status,
                "access_type": route.access_type,
                "documents": len(route.document_urls),
                "notice_id": route.notice_id,
            },
        )

    def upload_bid_pack_file(
        self,
        opportunity_id: str,
        filename: str,
        media_type: str,
        body: bytes,
    ) -> None:
        context = self.repository.load_context_for_opportunity(opportunity_id)
        if context is None:
            raise LookupError("Opportunity not found or qualification is incomplete.")
        _, opportunity, decision, existing_documents = context
        if decision.action in {DecisionAction.IGNORE, DecisionAction.MONITOR}:
            raise ValueError("This opportunity does not need a bid pack.")
        store_upload = getattr(self.retriever, "store_upload", None)
        classifier = getattr(self.retriever, "classifier", None)
        if store_upload is None or classifier is None:
            raise RuntimeError("Bid-pack uploads are not configured.")
        upload = store_upload(opportunity, filename, media_type, body)
        by_source = {document.source_url: document for document in existing_documents}
        by_source.update({document.source_url: document for document in upload.documents})
        documents = list(by_source.values())
        assessment = classifier.assess(opportunity, documents)
        self.repository.replace_documents(opportunity.id, documents)
        core_count = len(assessment.core_documents)
        opportunity.attributes.update(
            {
                "bid_pack_status": assessment.status,
                "bid_pack_confidence": assessment.confidence,
                "bid_pack_portal_url": assessment.portal_url,
                "bid_pack_core_count": core_count,
                "bid_pack_missing_roles": assessment.missing_roles,
                "retrieval_status": "complete" if core_count else "incomplete",
                "retrieval_message": (
                    "Uploaded bid pack recognised. Drafting is ready."
                    if assessment.status == "found"
                    else "The files were saved, but no governing bid document was recognised."
                ),
            }
        )
        self.repository.save_opportunity(opportunity)
        self.repository.record_event(
            "opportunity",
            opportunity.id,
            "tender_pack.uploaded",
            {
                "filename": filename,
                "files_added": len(upload.documents),
                "core_documents": core_count,
                "pack_status": assessment.status,
            },
        )

    def remove_bid_pack_document(
        self, opportunity_id: str, document_id: str
    ) -> None:
        context = self.repository.load_context_for_opportunity(opportunity_id)
        if context is None:
            raise LookupError("Opportunity not found or qualification is incomplete.")
        _, opportunity, decision, documents = context
        if decision.action in {DecisionAction.IGNORE, DecisionAction.MONITOR}:
            raise ValueError("This opportunity does not use a bid pack.")
        removed = next(
            (document for document in documents if document.id == document_id), None
        )
        if removed is None:
            raise LookupError("Document not found in this bid pack.")
        remaining = [document for document in documents if document.id != document_id]
        classifier = getattr(self.retriever, "classifier", None)
        if classifier is None:
            raise RuntimeError("Bid-pack document management is not configured.")
        assessment = classifier.assess(opportunity, remaining)
        if not self.repository.delete_document(opportunity_id, document_id):
            raise LookupError("Document not found in this bid pack.")
        core_count = len(assessment.core_documents)
        response_cleared = self.repository.delete_draft_for_opportunity(opportunity_id)
        opportunity.attributes.update(
            {
                "bid_pack_status": assessment.status,
                "bid_pack_confidence": assessment.confidence,
                "bid_pack_portal_url": assessment.portal_url,
                "bid_pack_core_count": core_count,
                "bid_pack_missing_roles": assessment.missing_roles,
                "retrieval_status": "complete" if core_count else "incomplete",
                "retrieval_message": (
                    "Bid pack ready."
                    if assessment.status == "found"
                    else "Add the governing tender documents to build the response."
                ),
                "draft_status": "not_run",
                "draft_message": "",
            }
        )
        self.repository.save_opportunity(opportunity)
        self.repository.record_event(
            "opportunity",
            opportunity.id,
            "tender_pack.document_removed",
            {
                "document_id": document_id,
                "filename": removed.filename or removed.title,
                "core_documents": core_count,
                "response_cleared": response_cleared,
            },
        )

    def save_bid_inputs(
        self, opportunity_id: str, payload: object
    ) -> dict[str, str]:
        context = self.repository.load_context_for_opportunity(opportunity_id)
        if context is None:
            raise LookupError("Opportunity not found or qualification is incomplete.")
        _, opportunity, decision, _ = context
        if decision.action in {DecisionAction.IGNORE, DecisionAction.MONITOR}:
            raise ValueError("This opportunity does not use bid inputs.")
        if not isinstance(payload, dict):
            raise ValueError("Bid inputs must be an object.")
        existing = opportunity.attributes.get("bid_inputs", {})
        inputs = dict(existing) if isinstance(existing, dict) else {}
        for raw_key, raw_value in payload.items():
            key = str(raw_key).strip()[:300]
            value = str(raw_value).strip()[:20_000]
            if not key:
                continue
            if value:
                inputs[key] = value
            else:
                inputs.pop(key, None)
        opportunity.attributes["bid_inputs"] = inputs
        self.repository.save_opportunity(opportunity)
        self.repository.record_event(
            "opportunity",
            opportunity.id,
            "bid_inputs.saved",
            {"input_count": len(inputs)},
        )
        return inputs

    def create_draft(self, opportunity_id: str, *, require_pack: bool = False):
        context = self.repository.load_context_for_opportunity(opportunity_id)
        if context is None:
            raise LookupError("Opportunity not found or qualification is incomplete.")
        _, opportunity, decision, documents = context
        if decision.action in {DecisionAction.IGNORE, DecisionAction.MONITOR}:
            raise ValueError("This opportunity does not need a draft.")
        if require_pack and opportunity.attributes.get("bid_pack_status") != "found":
            raise ValueError(
                "A high-confidence bid pack is required before creating the draft."
            )

        opportunity.attributes.update({"draft_status": "running", "draft_message": ""})
        self.repository.save_opportunity(opportunity)
        self.repository.record_event("opportunity", opportunity.id, "draft.started", {})
        try:
            draft_documents = []
            seen_documents: set[str] = set()
            for document in documents:
                if not (document.is_core or document.document_role == "notice_data"):
                    continue
                identity = document.content_hash or document.final_url or document.source_url
                if identity in seen_documents:
                    continue
                seen_documents.add(identity)
                draft_documents.append(document)
            draft = self.drafter.draft(
                opportunity,
                decision,
                draft_documents,
                bid_profile=self.repository.get_bid_profile(),
            )
        except Exception as exc:
            LOGGER.exception("draft creation failed opportunity_id=%s", opportunity.id)
            opportunity.attributes.update(
                {
                    "draft_status": "failed",
                    "draft_message": (
                        "The draft could not be created. Your source pack is safe; retry "
                        "when the model service is available."
                    ),
                }
            )
            self.repository.save_opportunity(opportunity)
            self.repository.record_event(
                "opportunity",
                opportunity.id,
                "draft.failed",
                {"error_type": type(exc).__name__},
            )
            raise
        if draft:
            self.repository.save_draft(draft)
            opportunity.attributes.update(
                {"draft_status": "ready", "draft_message": ""}
            )
            self.repository.save_opportunity(opportunity)
            self.repository.record_event(
                "opportunity", opportunity.id, "draft.created", {"channel": draft.channel}
            )
        return draft

    def process_connector(
        self, connector: SignalConnector, limit: int = 50, force: bool = False
    ) -> list[ProcessingResult]:
        return [self.process(signal, force=force) for signal in connector.pull(limit=limit)]
