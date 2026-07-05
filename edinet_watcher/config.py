from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


def load_dotenv_if_available(path: Path = Path(".env")) -> None:
    try:
        from dotenv import load_dotenv
    except ImportError:
        return
    load_dotenv(path)


@dataclass(frozen=True)
class Settings:
    data_dir: Path
    database_path: Path
    activists_path: Path
    extract_prompt_path: Path
    article_prompt_path: Path
    followup_prompt_path: Path
    edinet_api_key: str | None
    openai_api_key: str | None
    openai_model: str
    smtp_host: str | None
    smtp_port: int
    smtp_user: str | None
    smtp_password: str | None
    email_from: str | None
    email_to: str | None
    site_dir: Path
    public_site_url: str | None
    firebase_project: str | None
    firebase_site: str | None
    followup_max_runs: int
    followup_interval_days: int
    storage_backend: str
    google_cloud_project: str | None
    firestore_prefix: str

    @classmethod
    def from_env(cls, data_dir: str | Path = "data") -> "Settings":
        load_dotenv_if_available()
        base = Path(data_dir)
        return cls(
            data_dir=base,
            database_path=base / "edinet_watch.sqlite3",
            activists_path=Path(os.getenv("ACTIVISTS_PATH", "activists.yml")),
            extract_prompt_path=Path(os.getenv("PROMPT_EXTRACT_PATH", "prompt_extract.md")),
            article_prompt_path=Path(os.getenv("PROMPT_ARTICLE_PATH", "prompt_article.md")),
            followup_prompt_path=Path(os.getenv("PROMPT_FOLLOWUP_PATH", "prompt_followup.md")),
            edinet_api_key=os.getenv("EDINET_API_KEY") or None,
            openai_api_key=os.getenv("OPENAI_API_KEY") or None,
            openai_model=os.getenv("OPENAI_MODEL", "gpt-5.5"),
            smtp_host=os.getenv("SMTP_HOST") or None,
            smtp_port=int(os.getenv("SMTP_PORT", "587")),
            smtp_user=os.getenv("SMTP_USER") or None,
            smtp_password=os.getenv("SMTP_PASSWORD") or None,
            email_from=os.getenv("EMAIL_FROM") or None,
            email_to=os.getenv("EMAIL_TO") or None,
            site_dir=Path(os.getenv("SITE_DIR", str(base / "site"))),
            public_site_url=os.getenv("PUBLIC_SITE_URL") or None,
            firebase_project=os.getenv("FIREBASE_PROJECT") or None,
            firebase_site=os.getenv("FIREBASE_SITE") or None,
            followup_max_runs=int(os.getenv("FOLLOWUP_MAX_RUNS", "6")),
            followup_interval_days=int(os.getenv("FOLLOWUP_INTERVAL_DAYS", "30")),
            storage_backend=os.getenv("STORAGE_BACKEND", "sqlite").casefold(),
            google_cloud_project=os.getenv("GOOGLE_CLOUD_PROJECT") or os.getenv("GOOGLE_CLOUD_PROJECT_ID") or None,
            firestore_prefix=os.getenv("FIRESTORE_PREFIX", "edinet_watcher"),
        )

    def ensure_directories(self) -> None:
        for subdir in ("raw", "parsed", "reports", "drafts", "followups"):
            (self.data_dir / subdir).mkdir(parents=True, exist_ok=True)
        self.site_dir.mkdir(parents=True, exist_ok=True)
