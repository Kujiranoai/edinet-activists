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
    edinet_api_key: str | None
    openai_api_key: str | None
    openai_model: str
    smtp_host: str | None
    smtp_port: int
    smtp_user: str | None
    smtp_password: str | None
    email_from: str | None
    email_to: str | None

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
            edinet_api_key=os.getenv("EDINET_API_KEY") or None,
            openai_api_key=os.getenv("OPENAI_API_KEY") or None,
            openai_model=os.getenv("OPENAI_MODEL", "gpt-5.5"),
            smtp_host=os.getenv("SMTP_HOST") or None,
            smtp_port=int(os.getenv("SMTP_PORT", "587")),
            smtp_user=os.getenv("SMTP_USER") or None,
            smtp_password=os.getenv("SMTP_PASSWORD") or None,
            email_from=os.getenv("EMAIL_FROM") or None,
            email_to=os.getenv("EMAIL_TO") or None,
        )

    def ensure_directories(self) -> None:
        for subdir in ("raw", "parsed", "reports", "drafts"):
            (self.data_dir / subdir).mkdir(parents=True, exist_ok=True)
