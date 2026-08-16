from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


def _load_dotenv(path: Path = Path(".env")) -> None:
    """Load a small, dependency-free subset of dotenv syntax."""
    if not path.exists():
        return
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key:
            os.environ.setdefault(key, value)


def _env_bool(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().casefold() in {"1", "true", "yes", "on"}


@dataclass(frozen=True, slots=True)
class Settings:
    database_path: Path
    playbook: str
    log_level: str
    openai_extraction_model: str
    openai_drafting_model: str
    gmail_credentials_path: Path
    gmail_token_path: Path
    gmail_label: str
    gmail_query: str
    retrieval_enabled: bool
    web_discovery_enabled: bool
    document_path: Path
    artifact_path: Path
    artifact_node: str
    crawl_max_pages: int
    crawl_max_depth: int
    crawl_max_resource_mb: int
    crawl_max_total_mb: int
    host: str
    port: int

    @classmethod
    def from_env(cls) -> "Settings":
        _load_dotenv()
        _load_dotenv(Path(".env.local"))
        return cls(
            database_path=Path(
                os.getenv("OUTREACH_DATABASE_PATH", "./data/outreach.sqlite3")
            ).expanduser(),
            playbook=os.getenv("OUTREACH_PLAYBOOK", "procurement"),
            log_level=os.getenv("OUTREACH_LOG_LEVEL", "INFO").upper(),
            openai_extraction_model=os.getenv(
                "OPENAI_EXTRACTION_MODEL", os.getenv("OPENAI_MODEL", "gpt-5.4-mini")
            ),
            openai_drafting_model=os.getenv(
                "OPENAI_DRAFTING_MODEL", os.getenv("OPENAI_MODEL", "gpt-5.4-mini")
            ),
            gmail_credentials_path=Path(
                os.getenv("GMAIL_CREDENTIALS_PATH", "./gmail_credentials.json")
            ).expanduser(),
            gmail_token_path=Path(
                os.getenv("GMAIL_TOKEN_PATH", "./gmail_token.json")
            ).expanduser(),
            gmail_label=os.getenv("GMAIL_LABEL", "Tender Alerts"),
            gmail_query=os.getenv("GMAIL_QUERY", ""),
            retrieval_enabled=_env_bool("OUTREACH_RETRIEVAL_ENABLED"),
            web_discovery_enabled=_env_bool("OUTREACH_WEB_DISCOVERY_ENABLED"),
            document_path=Path(
                os.getenv("OUTREACH_DOCUMENT_PATH", "./data/private/documents")
            ).expanduser(),
            artifact_path=Path(
                os.getenv("OUTREACH_ARTIFACT_PATH", "./data/private/artifacts")
            ).expanduser(),
            artifact_node=os.getenv("OUTREACH_ARTIFACT_NODE", "node"),
            crawl_max_pages=int(os.getenv("OUTREACH_CRAWL_MAX_PAGES", "12")),
            crawl_max_depth=int(os.getenv("OUTREACH_CRAWL_MAX_DEPTH", "2")),
            crawl_max_resource_mb=int(
                os.getenv("OUTREACH_CRAWL_MAX_RESOURCE_MB", "10")
            ),
            crawl_max_total_mb=int(os.getenv("OUTREACH_CRAWL_MAX_TOTAL_MB", "30")),
            host=os.getenv("OUTREACH_HOST", "127.0.0.1"),
            port=int(os.getenv("OUTREACH_PORT", "8080")),
        )
