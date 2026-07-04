# EDINET Activist Filing Watcher

Local-first Python MVP for monitoring EDINET large-shareholding filings by a curated activist-investor list, generating a factual LLM summary and Substack-ready Markdown draft, then emailing the draft to you.

## Setup

```bash
python -m venv .venv
. .venv/bin/activate
pip install -e ".[dev]"
cp .env.example .env
```

Fill in `.env`, then replace the sample entry in `activists.yml` with real EDINET submitter codes and aliases.

## Commands

```bash
edinet-watch scan --days 3
edinet-watch process
edinet-watch draft
edinet-watch run --days 3
```

For local plumbing tests without OpenAI, use:

```bash
edinet-watch draft --offline
```

Artifacts are written under `data/`:

- `data/raw/{doc_id}/...`
- `data/parsed/{doc_id}.json`
- `data/reports/{doc_id}.json`
- `data/drafts/{date}-{doc_id}.md`
- `data/edinet_watch.sqlite3`

## Notes

- The watcher filters EDINET document type codes `350`, `360`, `370`, and `380`.
- It deduplicates by EDINET `doc_id`.
- OpenAI generation is two-stage: structured filing summary, then article draft.
- Substack publishing is manual by design.
