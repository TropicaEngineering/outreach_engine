from __future__ import annotations

import argparse
import json
import logging
from pathlib import Path

from outreach_engine.config import Settings
from outreach_engine.connectors.directory import DirectoryConnector
from outreach_engine.connectors.fixture import FixtureConnector
from outreach_engine.domain import ReviewStatus
from outreach_engine.engine import OutreachEngine
from outreach_engine.playbooks.procurement import ProcurementPlaybook
from outreach_engine.playbooks.recruitment import RecruitmentPlaybook
from outreach_engine.playbooks.universal import UniversalPlaybook
from outreach_engine.providers.heuristic import HeuristicExtractor, TemplateDrafter
from outreach_engine.storage import SQLiteRepository


PACKAGE_ROOT = Path(__file__).resolve().parent
PROJECT_ROOT = PACKAGE_ROOT.parent.parent
DEFAULT_FIXTURE = PROJECT_ROOT / "fixtures" / "procurement_signals.json"


PLAYBOOKS = {
    "procurement": ProcurementPlaybook,
    "recruitment": RecruitmentPlaybook,
    "universal": UniversalPlaybook,
}


def _playbook(name: str):
    try:
        return PLAYBOOKS[name]()
    except KeyError as exc:
        raise ValueError(f"unknown playbook: {name}") from exc


def _engine(
    database: Path,
    provider: str,
    playbook_name: str,
    settings: Settings,
    *,
    enable_retrieval: bool | None = None,
) -> OutreachEngine:
    repository = SQLiteRepository(database)
    if provider == "openai":
        from outreach_engine.providers.openai_provider import OpenAIProvider

        ai_provider = OpenAIProvider(
            settings.openai_extraction_model, settings.openai_drafting_model
        )
        extractor = ai_provider
        drafter = ai_provider
    else:
        extractor = HeuristicExtractor()
        drafter = TemplateDrafter()
    retrieval_is_enabled = (
        settings.retrieval_enabled if enable_retrieval is None else enable_retrieval
    )
    retriever = None
    if playbook_name == "procurement":
        from outreach_engine.retrieval.web import SafeHttpClient, WebTenderRetriever

        discoverer = None
        if (
            retrieval_is_enabled
            and settings.web_discovery_enabled
            and provider == "openai"
        ):
            from outreach_engine.retrieval.openai_discovery import OpenAIWebDiscoverer

            discoverer = OpenAIWebDiscoverer(settings.openai_extraction_model)
        retriever = WebTenderRetriever(
            settings.document_path,
            discoverer=discoverer,
            http_client=SafeHttpClient(
                max_resource_bytes=settings.crawl_max_resource_mb * 1_000_000
            ),
            max_pages=settings.crawl_max_pages,
            max_depth=settings.crawl_max_depth,
            max_total_bytes=settings.crawl_max_total_mb * 1_000_000,
        )
    return OutreachEngine(
        repository, extractor, _playbook(playbook_name), drafter, retriever=retriever
    )


def _print_results(results) -> int:
    failures = 0
    for result in results:
        if result.error:
            failures += 1
            print(f"FAILED  {result.signal.external_id}: {result.error}")
            continue
        decision = result.decision
        duplicate = " (duplicate)" if result.duplicate else ""
        print(
            f"{decision.action.value.upper():8} {decision.score:3}  "
            f"{result.opportunity.title}{duplicate}"
        )
    print(f"\nProcessed {len(results)} signal(s); {failures} failed.")
    return 1 if failures else 0


def _configure_logging(level: str) -> None:
    logging.basicConfig(
        level=getattr(logging, level, logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="signal-route",
        description="Ingest, qualify, and review opportunity signals.",
    )
    parser.add_argument("--database", type=Path, help="Override the configured SQLite database")
    subparsers = parser.add_subparsers(dest="command", required=True)

    subparsers.add_parser("init-db", help="Initialize the database schema")

    demo = subparsers.add_parser("demo", help="Run the sanitized offline procurement demo")
    demo.add_argument("--reset", action="store_true", help="Reset only the demo database first")
    demo.add_argument("--fixture", type=Path, default=DEFAULT_FIXTURE)
    demo.add_argument(
        "--playbook", choices=sorted(PLAYBOOKS), default="procurement"
    )

    fixtures = subparsers.add_parser("ingest-fixtures", help="Ingest a JSON fixture file")
    fixtures.add_argument("path", type=Path)
    fixtures.add_argument("--limit", type=int, default=50)
    fixtures.add_argument("--provider", choices=["heuristic", "openai"], default="heuristic")
    fixtures.add_argument("--playbook", choices=sorted(PLAYBOOKS), default=None)
    fixtures.add_argument(
        "--retrieve", action=argparse.BooleanOptionalAction, default=False
    )

    directory = subparsers.add_parser(
        "ingest-directory", help="Ingest text files and optional legacy metadata"
    )
    directory.add_argument("path", type=Path)
    directory.add_argument("--metadata-dir", type=Path)
    directory.add_argument("--limit", type=int, default=50)
    directory.add_argument("--provider", choices=["heuristic", "openai"], default="heuristic")
    directory.add_argument("--playbook", choices=sorted(PLAYBOOKS), default=None)
    directory.add_argument(
        "--retrieve", action=argparse.BooleanOptionalAction, default=False
    )
    directory.add_argument(
        "--force", action="store_true", help="Reprocess records already completed"
    )

    gmail = subparsers.add_parser(
        "ingest-gmail", help="Ingest signals from the configured Gmail label"
    )
    gmail.add_argument("--limit", type=int, default=25)
    gmail.add_argument("--provider", choices=["heuristic", "openai"], default="openai")
    gmail.add_argument("--playbook", choices=sorted(PLAYBOOKS), default=None)
    gmail.add_argument(
        "--retrieve",
        action=argparse.BooleanOptionalAction,
        default=False,
        help="Automatically collect the source pack and draft after parsing",
    )
    gmail.add_argument(
        "--force", action="store_true", help="Reprocess records already completed"
    )
    gmail.add_argument(
        "--notice-id",
        help="Process only one Find a Tender notice ID from the pulled digest messages",
    )

    list_command = subparsers.add_parser("list", help="List recent opportunities")
    list_command.add_argument("--limit", type=int, default=25)
    list_command.add_argument("--json", action="store_true")

    show = subparsers.add_parser("show", help="Show an opportunity and audit history")
    show.add_argument("opportunity_id")

    review = subparsers.add_parser("review", help="Approve or reject a draft")
    review.add_argument("draft_id")
    review.add_argument("status", choices=["approved", "rejected"])

    serve = subparsers.add_parser("serve", help="Start the local review console")
    serve.add_argument("--host")
    serve.add_argument("--port", type=int)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    settings = Settings.from_env()
    _configure_logging(settings.log_level)

    if args.command == "demo":
        database = args.database or Path("./data/demo.sqlite3")
        if args.reset and database.exists():
            database.unlink()
            for suffix in ("-shm", "-wal"):
                sidecar = Path(str(database) + suffix)
                if sidecar.exists():
                    sidecar.unlink()
        engine = _engine(
            database, "heuristic", args.playbook, settings, enable_retrieval=False
        )
        return _print_results(engine.process_connector(FixtureConnector(args.fixture)))

    database = args.database or settings.database_path
    repository = SQLiteRepository(database)
    repository.initialize()

    if args.command == "init-db":
        print(f"Database ready: {database.resolve()}")
        return 0
    if args.command == "ingest-fixtures":
        engine = _engine(
            database,
            args.provider,
            args.playbook or settings.playbook,
            settings,
            enable_retrieval=args.retrieve,
        )
        return _print_results(
            engine.process_connector(FixtureConnector(args.path), limit=args.limit)
        )
    if args.command == "ingest-directory":
        engine = _engine(
            database,
            args.provider,
            args.playbook or settings.playbook,
            settings,
            enable_retrieval=args.retrieve,
        )
        connector = DirectoryConnector(args.path, args.metadata_dir, source="legacy_real_data")
        return _print_results(
            engine.process_connector(connector, limit=args.limit, force=args.force)
        )
    if args.command == "ingest-gmail":
        from outreach_engine.connectors.gmail import GmailConnector

        connector = GmailConnector(
            settings.gmail_credentials_path,
            settings.gmail_token_path,
            settings.gmail_label,
            settings.gmail_query,
        )
        engine = _engine(
            database,
            args.provider,
            args.playbook or settings.playbook,
            settings,
            enable_retrieval=args.retrieve,
        )
        signals = connector.pull(limit=args.limit)
        if args.notice_id:
            signals = [
                signal
                for signal in signals
                if signal.source_metadata.get("notice_id") == args.notice_id
            ]
            if not signals:
                print(f'Notice ID "{args.notice_id}" was not found in pulled messages.')
                return 1
        results = []
        for signal in signals:
            result = engine.process(
                signal,
                force=args.force,
                run_retrieval=args.retrieve,
                run_draft=args.retrieve,
            )
            results.append(result)
            if not result.error and result.opportunity is not None:
                engine.resolve_access_route(result.opportunity.id)
        return _print_results(results)
    if args.command == "list":
        rows = repository.list_opportunities(limit=args.limit)
        if args.json:
            print(json.dumps(rows, indent=2))
        else:
            for row in rows:
                print(
                    f"{row['id'][:8]}  {(row['action'] or '-'):8} "
                    f"{str(row['score'] or '-'):>3}  {row['title']}"
                )
        return 0
    if args.command == "show":
        detail = repository.get_opportunity_detail(args.opportunity_id)
        if detail is None:
            print("Opportunity not found")
            return 1
        print(json.dumps(detail, indent=2))
        return 0
    if args.command == "review":
        updated = repository.review_draft(args.draft_id, ReviewStatus(args.status))
        print(f"Draft {args.status}." if updated else "Draft not found.")
        return 0 if updated else 1
    if args.command == "serve":
        from outreach_engine.artifacts import SubmissionArtifactService
        from outreach_engine.connectors.gmail import GmailConnector
        from outreach_engine.web import serve

        action_engine = _engine(
            database,
            "openai",
            settings.playbook,
            settings,
            enable_retrieval=True,
        )
        serve(
            repository,
            args.host or settings.host,
            args.port or settings.port,
            engine=action_engine,
            inbox_connector=(
                None
                if str(database).endswith("demo.sqlite3")
                else GmailConnector(
                    settings.gmail_credentials_path,
                    settings.gmail_token_path,
                    settings.gmail_label,
                    settings.gmail_query,
                )
            ),
            artifact_service=SubmissionArtifactService(
                repository,
                settings.artifact_path,
                node_executable=settings.artifact_node,
            ),
        )
        return 0
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
