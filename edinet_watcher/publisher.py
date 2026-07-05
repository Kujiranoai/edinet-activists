from __future__ import annotations

import html
import json
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .config import Settings
from .storage import Storage
from .text import normalize_display_text


@dataclass(frozen=True)
class PublishResult:
    built: int
    deployed: bool


class StaticSitePublisher:
    """Build a static report site and optionally deploy it through Firebase CLI."""

    def __init__(self, settings: Settings, storage: Storage) -> None:
        self.settings = settings
        self.storage = storage

    def build(self) -> int:
        """Render all generated Markdown drafts into static HTML pages."""
        self.settings.site_dir.mkdir(parents=True, exist_ok=True)
        posts_dir = self.settings.site_dir / "filings"
        posts_dir.mkdir(parents=True, exist_ok=True)
        self._write_static_assets()

        drafts = self.storage.all_drafts()
        entries = []
        for draft in drafts:
            draft_path = Path(draft["draft_path"])
            report_path = Path(draft["report_path"])
            draft_text = _read_text_or_stored(draft_path, draft.get("draft_markdown"))
            report_text = _read_text_or_stored(report_path, draft.get("report_json"))
            if not draft_text or not report_text:
                continue
            report = json.loads(report_text)
            markdown = normalize_display_text(draft_text)
            title = self._title_from_markdown(markdown)
            page_name = f"{self._safe_slug(draft['doc_id'])}.html"
            page_path = posts_dir / page_name
            page_path.write_text(
                self._page_html(
                    title=title,
                    body=_markdown_to_html(markdown),
                    report=report,
                ),
                encoding="utf-8",
            )
            public_url = self._public_url(f"filings/{page_name}")
            self.storage.mark_publish_status(draft["doc_id"], "built", public_url)
            entries.append({"title": title, "href": f"filings/{page_name}", "report": report})

        (self.settings.site_dir / "index.html").write_text(self._index_html(entries), encoding="utf-8")
        return len(entries)

    def deploy(self) -> None:
        """Deploy the generated site using Firebase CLI.

        This method is intentionally narrow so a REST API deployer can replace it later.
        """
        command = ["firebase", "deploy", "--only", "hosting"]
        if self.settings.firebase_project:
            command.extend(["--project", self.settings.firebase_project])
        subprocess.run(command, cwd=self.settings.site_dir, check=True)

    def publish(self, deploy: bool = False) -> PublishResult:
        """Build the static site and optionally deploy it."""
        built = self.build()
        if deploy:
            self.deploy()
            for draft in self.storage.all_drafts():
                self.storage.mark_publish_status(draft["doc_id"], "deployed", draft.get("public_url"))
        return PublishResult(built=built, deployed=deploy)

    def _write_static_assets(self) -> None:
        hosting = {"public": ".", "ignore": ["firebase.json"]}
        if self.settings.firebase_site:
            hosting["site"] = self.settings.firebase_site
        (self.settings.site_dir / "firebase.json").write_text(
            json.dumps({"hosting": hosting}, indent=2),
            encoding="utf-8",
        )
        (self.settings.site_dir / "style.css").write_text(
            """
body {
  margin: 0;
  color: #172026;
  background: #f7f7f4;
  font-family: Arial, Helvetica, sans-serif;
  line-height: 1.6;
}
header, main {
  max-width: 860px;
  margin: 0 auto;
  padding: 28px 20px;
}
header {
  border-bottom: 1px solid #d7d8d2;
}
a {
  color: #0a5c7a;
}
article {
  background: #ffffff;
  border: 1px solid #ddded8;
  border-radius: 6px;
  padding: 28px;
}
li + li {
  margin-top: 8px;
}
.meta {
  color: #5d686f;
  font-size: 14px;
}
code {
  background: #eef0ec;
  padding: 2px 4px;
  border-radius: 4px;
}
""".strip(),
            encoding="utf-8",
        )

    def _page_html(self, *, title: str, body: str, report: dict[str, Any]) -> str:
        metadata = report.get("source", {}).get("metadata") or report.get("followup", {})
        doc_id = metadata.get("doc_id") or metadata.get("root_doc_id") or ""
        return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{html.escape(title)}</title>
  <link rel="stylesheet" href="../style.css">
</head>
<body>
  <header>
    <a href="../index.html">EDINET Watcher</a>
    <p class="meta">Source document: {html.escape(str(doc_id))}</p>
  </header>
  <main>
    <article>
{body}
    </article>
  </main>
</body>
</html>
"""

    def _index_html(self, entries: list[dict[str, Any]]) -> str:
        items = "\n".join(
            f'      <li><a href="{html.escape(entry["href"])}">{html.escape(entry["title"])}</a></li>'
            for entry in entries
        )
        if not items:
            items = "      <li>No reports generated yet.</li>"
        return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>EDINET Watcher</title>
  <link rel="stylesheet" href="style.css">
</head>
<body>
  <header>
    <h1>EDINET Watcher</h1>
    <p class="meta">Large-shareholding reports and monthly follow-ups.</p>
  </header>
  <main>
    <article>
      <h2>Reports</h2>
      <ul>
{items}
      </ul>
    </article>
  </main>
</body>
</html>
"""

    def _title_from_markdown(self, text: str) -> str:
        for line in text.splitlines():
            if line.startswith("# "):
                return line[2:].strip()
        return "EDINET report"

    def _safe_slug(self, value: str) -> str:
        return "".join(ch if ch.isalnum() or ch in ("-", "_") else "-" for ch in value)

    def _public_url(self, path: str) -> str | None:
        if not self.settings.public_site_url:
            return None
        return f"{self.settings.public_site_url.rstrip('/')}/{path}"


def _markdown_to_html(text: str) -> str:
    """Render a small Markdown subset used by generated reports."""
    lines = text.splitlines()
    html_lines: list[str] = []
    in_list = False

    for line in lines:
        stripped = line.strip()
        if not stripped:
            if in_list:
                html_lines.append("</ul>")
                in_list = False
            continue
        if stripped.startswith("#"):
            if in_list:
                html_lines.append("</ul>")
                in_list = False
            level = min(len(stripped) - len(stripped.lstrip("#")), 6)
            content = stripped[level:].strip()
            html_lines.append(f"<h{level}>{_inline_markdown(content)}</h{level}>")
        elif stripped.startswith("- "):
            if not in_list:
                html_lines.append("<ul>")
                in_list = True
            html_lines.append(f"<li>{_inline_markdown(stripped[2:])}</li>")
        else:
            if in_list:
                html_lines.append("</ul>")
                in_list = False
            html_lines.append(f"<p>{_inline_markdown(stripped)}</p>")
    if in_list:
        html_lines.append("</ul>")
    return "\n".join(f"      {line}" for line in html_lines)


def _read_text_or_stored(path: Path, stored: Any) -> str | None:
    if path.exists():
        return path.read_text(encoding="utf-8")
    if stored:
        return str(stored)
    return None


def _inline_markdown(text: str) -> str:
    escaped = html.escape(text)
    escaped = re.sub(
        r"\[([^\]]+)\]\((https?://[^)\s]+)\)",
        lambda match: f'<a href="{match.group(2)}">{match.group(1)}</a>',
        escaped,
    )
    parts = escaped.split("**")
    if len(parts) > 1:
        escaped = "".join(
            f"<strong>{part}</strong>" if index % 2 else part
            for index, part in enumerate(parts)
        )
    return escaped
