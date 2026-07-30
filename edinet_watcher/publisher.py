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
            entries.append(_index_record(draft["doc_id"], title, f"filings/{page_name}", report))

        (self.settings.site_dir / "reports.json").write_text(
            json.dumps({"reports": entries}, ensure_ascii=False, indent=2, sort_keys=True),
            encoding="utf-8",
        )
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
.initial-report td:first-child {
  border-left: 4px solid #b42318;
  padding-left: 12px;
}
.initial-report-link {
  color: #b42318;
  font-weight: 700;
}
.initial-report-badge {
  background: #fbe9e7;
  border: 1px solid #e6a39c;
  border-radius: 999px;
  color: #8f1d14;
  display: inline-block;
  font-size: 11px;
  font-weight: 700;
  line-height: 1.4;
  margin-left: 7px;
  padding: 1px 7px;
  vertical-align: 1px;
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
.toolbar {
  display: grid;
  gap: 12px;
  grid-template-columns: minmax(0, 1fr) minmax(180px, 240px);
  margin: 0 0 18px;
}
label {
  color: #34434b;
  display: grid;
  font-size: 13px;
  gap: 5px;
}
input,
select {
  border: 1px solid #bfc5c1;
  border-radius: 4px;
  color: #172026;
  font: inherit;
  min-width: 0;
  padding: 8px 10px;
}
.table-wrap {
  overflow-x: auto;
}
table {
  border-collapse: collapse;
  min-width: 760px;
  width: 100%;
}
th,
td {
  border-bottom: 1px solid #ddded8;
  padding: 10px 8px;
  text-align: left;
  vertical-align: top;
}
th {
  color: #34434b;
  font-size: 13px;
  white-space: nowrap;
}
button.sort {
  background: none;
  border: 0;
  color: inherit;
  cursor: pointer;
  font: inherit;
  padding: 0;
}
.number {
  text-align: right;
  white-space: nowrap;
}
.empty {
  color: #5d686f;
  padding: 18px 0 0;
}
@media (max-width: 720px) {
  header, main {
    padding: 22px 14px;
  }
  article {
    padding: 18px;
  }
  .toolbar {
    grid-template-columns: 1fr;
  }
}
""".strip(),
            encoding="utf-8",
        )
        (self.settings.site_dir / "app.js").write_text(
            """
const state = {
  reports: [],
  sortKey: "filing_date",
  sortDirection: "desc",
};

const params = new URLSearchParams(window.location.search);
const els = {
  search: document.querySelector("[data-filter-search]"),
  filer: document.querySelector("[data-filter-filer]"),
  count: document.querySelector("[data-result-count]"),
  body: document.querySelector("[data-report-rows]"),
  empty: document.querySelector("[data-empty]"),
};

function text(value) {
  return value == null ? "" : String(value);
}

function display(value, fallback = "-") {
  const cleaned = text(value).trim();
  return cleaned || fallback;
}

function escapeHtml(value) {
  return display(value)
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#39;");
}

function escapeAttr(value) {
  return escapeHtml(value).replaceAll("`", "&#96;");
}

function pct(value) {
  return typeof value === "number" ? `${value.toFixed(2)}%` : "-";
}

function compareValues(a, b) {
  if (typeof a === "number" || typeof b === "number") {
    return (a ?? -Infinity) - (b ?? -Infinity);
  }
  return text(a).localeCompare(text(b), "ja");
}

function filteredReports() {
  const query = text(els.search?.value).trim().toLocaleLowerCase();
  const filer = text(els.filer?.value);
  return state.reports.filter((report) => {
    const haystack = [
      report.title,
      report.doc_id,
      report.filer_name,
      report.filer_edinet_code,
      report.target_company,
      report.target_ticker,
      report.doc_type_label,
    ].join(" ").toLocaleLowerCase();
    return (!query || haystack.includes(query)) && (!filer || report.filer_edinet_code === filer);
  });
}

function renderRows() {
  if (!els.body) return;
  const rows = filteredReports().sort((left, right) => {
    const compared = compareValues(left[state.sortKey], right[state.sortKey]);
    return state.sortDirection === "asc" ? compared : -compared;
  });
  els.body.innerHTML = rows.map((report) => {
    const initial = report.is_initial_report === true;
    const rowClass = initial ? ' class="initial-report"' : "";
    const linkClass = initial ? "initial-report-link" : "";
    const badge = initial ? '<span class="initial-report-badge">Initial 5% report</span>' : "";
    return `
    <tr${rowClass}>
      <td><a class="${linkClass}" href="${escapeAttr(report.href)}">${escapeHtml(display(report.title, "EDINET report"))}</a>${badge}<div class="meta">${escapeHtml(report.doc_id)}</div></td>
      <td>${escapeHtml(report.filing_date)}</td>
      <td>${escapeHtml(report.filer_name)}</td>
      <td>${escapeHtml(report.target_company)}</td>
      <td>${escapeHtml(report.doc_type_label || report.doc_type_code)}</td>
      <td class="number">${pct(report.ownership_pct)}</td>
    </tr>
  `;
  }).join("");
  if (els.count) els.count.textContent = `${rows.length} report${rows.length === 1 ? "" : "s"}`;
  if (els.empty) els.empty.hidden = rows.length > 0;
}

function updateUrl() {
  const next = new URLSearchParams();
  if (els.search?.value) next.set("q", els.search.value);
  if (els.filer?.value) next.set("filer", els.filer.value);
  const suffix = next.toString();
  window.history.replaceState({}, "", suffix ? `?${suffix}` : window.location.pathname);
}

function populateFilers() {
  if (!els.filer) return;
  const filers = new Map();
  for (const report of state.reports) {
    if (report.filer_edinet_code) {
      filers.set(report.filer_edinet_code, display(report.filer_name, report.filer_edinet_code));
    }
  }
  for (const [code, name] of [...filers.entries()].sort((a, b) => a[1].localeCompare(b[1], "ja"))) {
    const option = document.createElement("option");
    option.value = code;
    option.textContent = `${name} (${code})`;
    els.filer.appendChild(option);
  }
}

function bindControls() {
  els.search?.addEventListener("input", () => {
    updateUrl();
    renderRows();
  });
  els.filer?.addEventListener("change", () => {
    updateUrl();
    renderRows();
  });
  document.querySelectorAll("[data-sort]").forEach((button) => {
    button.addEventListener("click", () => {
      const key = button.getAttribute("data-sort") || "filing_date";
      if (state.sortKey === key) {
        state.sortDirection = state.sortDirection === "asc" ? "desc" : "asc";
      } else {
        state.sortKey = key;
        state.sortDirection = key === "filing_date" || key === "ownership_pct" ? "desc" : "asc";
      }
      renderRows();
    });
  });
}

async function init() {
  const response = await fetch("reports.json", { cache: "no-store" });
  const data = await response.json();
  state.reports = Array.isArray(data.reports) ? data.reports : [];
  populateFilers();
  if (els.search) els.search.value = params.get("q") || "";
  if (els.filer) els.filer.value = params.get("filer") || "";
  bindControls();
  renderRows();
}

init().catch(() => {
  if (els.count) els.count.textContent = "Static report list";
});
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
        rows = "\n".join(_index_row_html(entry) for entry in entries)
        if not rows:
            rows = '        <tr><td colspan="6">No reports generated yet.</td></tr>'
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
      <div class="toolbar">
        <label>
          Search
          <input type="search" data-filter-search placeholder="Investor, company, ticker, or document ID">
        </label>
        <label>
          Filer
          <select data-filter-filer>
            <option value="">All filers</option>
          </select>
        </label>
      </div>
      <p class="meta" data-result-count>{len(entries)} report{"s" if len(entries) != 1 else ""}</p>
      <div class="table-wrap">
        <table>
          <thead>
            <tr>
              <th><button class="sort" type="button" data-sort="title">Report</button></th>
              <th><button class="sort" type="button" data-sort="filing_date">Date</button></th>
              <th><button class="sort" type="button" data-sort="filer_name">Filer</button></th>
              <th><button class="sort" type="button" data-sort="target_company">Target</button></th>
              <th><button class="sort" type="button" data-sort="doc_type_label">Type</button></th>
              <th class="number"><button class="sort" type="button" data-sort="ownership_pct">Holding</button></th>
            </tr>
          </thead>
          <tbody data-report-rows>
{rows}
          </tbody>
        </table>
      </div>
      <p class="empty" data-empty hidden>No reports match the current filters.</p>
    </article>
  </main>
  <script src="app.js"></script>
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


def _index_record(doc_id: str, title: str, href: str, report: dict[str, Any]) -> dict[str, Any]:
    source = _dict_value(report.get("source"))
    metadata = _dict_value(source.get("metadata"))
    parsed = _dict_value(source.get("parsed_fields"))
    summary = _dict_value(report.get("summary"))
    raw_data = _dict_value(_dict_value(metadata.get("raw")).get("_data"))
    followup = _dict_value(report.get("followup"))

    if report.get("type") == "monthly_followup":
        record_doc_id = doc_id
    else:
        record_doc_id = _first_text(
            metadata.get("doc_id"),
            parsed.get("doc_id"),
            _dict_value(source.get("source")).get("doc_id"),
            doc_id,
        )
    filing_date = _first_date(
        parsed.get("filing_date"),
        metadata.get("submit_datetime"),
        raw_data.get("submitDateTime"),
        report.get("generated_at"),
    )
    filer_name = normalize_display_text(
        _first_text(
            parsed.get("filer_name_en"),
            parsed.get("filer_name"),
            summary.get("filer"),
            metadata.get("filer_name"),
            followup.get("filer_name"),
        )
        or ""
    )
    target_company = normalize_display_text(
        _first_text(
            parsed.get("target_company"),
            summary.get("target_company"),
            metadata.get("target_name"),
            followup.get("target_name"),
        )
        or ""
    )

    record = {
        "doc_id": record_doc_id,
        "title": title,
        "href": href,
        "filing_date": filing_date,
        "filer_name": filer_name,
        "filer_edinet_code": _first_text(
            parsed.get("filer_edinet_code"),
            metadata.get("filer_edinet_code"),
            followup.get("filer_edinet_code"),
        ),
        "target_company": target_company,
        "target_edinet_code": _first_text(
            metadata.get("target_edinet_code"),
            raw_data.get("issuerEdinetCode"),
            followup.get("target_edinet_code"),
        ),
        "target_ticker": _first_text(parsed.get("target_ticker"), raw_data.get("secCode")),
        "doc_type_code": _first_text(parsed.get("doc_type_code"), metadata.get("doc_type_code")),
        "doc_type_label": _first_text(summary.get("filing_type"), metadata.get("filing_type"), raw_data.get("docDescription")),
        "ownership_pct": _coerce_pct(
            _first_value(
                summary.get("current_ownership_pct"),
                parsed.get("ownership_pct"),
                _dict_value(source.get("comparison")).get("current_ownership_pct"),
            )
        ),
    }
    record["is_initial_report"] = _is_initial_report(record)
    return record


def _index_row_html(entry: dict[str, Any]) -> str:
    title = html.escape(str(entry.get("title") or "EDINET report"))
    href = html.escape(str(entry.get("href") or "#"))
    doc_id = html.escape(str(entry.get("doc_id") or ""))
    initial = _is_initial_report(entry)
    row_class = ' class="initial-report"' if initial else ""
    link_class = ' class="initial-report-link"' if initial else ""
    badge = '<span class="initial-report-badge">Initial 5% report</span>' if initial else ""
    return f"""        <tr{row_class}>
          <td><a{link_class} href="{href}">{title}</a>{badge}<div class="meta">{doc_id}</div></td>
          <td>{html.escape(str(entry.get("filing_date") or "-"))}</td>
          <td>{html.escape(str(entry.get("filer_name") or "-"))}</td>
          <td>{html.escape(str(entry.get("target_company") or "-"))}</td>
          <td>{html.escape(str(entry.get("doc_type_label") or entry.get("doc_type_code") or "-"))}</td>
          <td class="number">{_format_pct(entry.get("ownership_pct"))}</td>
        </tr>"""


def _is_initial_report(entry: dict[str, Any]) -> bool:
    """Classify initial filings while tolerating stale codes in historical records."""
    label = str(entry.get("doc_type_label") or "").casefold()
    non_initial_markers = (
        "変更報告書",
        "訂正報告書",
        "change report",
        "amendment",
        "correction",
    )
    return str(entry.get("doc_type_code") or "") == "350" and not any(
        marker in label for marker in non_initial_markers
    )


def _dict_value(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _first_value(*values: Any) -> Any:
    for value in values:
        if value not in (None, ""):
            return value
    return None


def _first_text(*values: Any) -> str | None:
    value = _first_value(*values)
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _first_date(*values: Any) -> str | None:
    for value in values:
        text = _first_text(value)
        if not text:
            continue
        match = re.search(r"\d{4}-\d{2}-\d{2}", text)
        if match:
            return match.group(0)
    return None


def _coerce_pct(value: Any) -> float | None:
    if value in (None, ""):
        return None
    try:
        pct = float(str(value).replace("%", ""))
    except ValueError:
        return None
    if 0 < pct <= 1:
        pct *= 100
    return round(pct, 4)


def _format_pct(value: Any) -> str:
    if not isinstance(value, (int, float)):
        return "-"
    return f"{value:.2f}%"
