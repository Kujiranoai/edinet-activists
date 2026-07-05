# EDINET Activist Filing Watcher

Local-first Python MVP for monitoring EDINET large-shareholding filings by a curated activist-investor list, generating factual LLM summaries, emailing drafts, and building a static report site.

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
edinet-watch email
edinet-watch publish
edinet-watch run --days 3
```

For local plumbing tests without OpenAI, use:

```bash
edinet-watch draft --offline
edinet-watch followups run --offline
```

Monthly follow-up schedules are created from initial 5% filings (`350`) only:

```bash
edinet-watch followups run
edinet-watch followups list
edinet-watch followups pause S100XXXX
edinet-watch followups resume S100XXXX
edinet-watch followups stop S100XXXX
edinet-watch followups set-limit S100XXXX --max-runs 12
```

Static-site output is written to `data/site` by default. To deploy locally with Firebase CLI:

```bash
edinet-watch publish --deploy
```

Cloud deployment notes live in `docs/cloud-deployment.md`. The intended production path is GitHub Actions -> Cloud Build -> Cloud Run Jobs -> Cloud Scheduler, with Firestore used for workflow state.

Artifacts are written under `data/`:

- `data/raw/{doc_id}/...`
- `data/parsed/{doc_id}.json`
- `data/reports/{doc_id}.json`
- `data/followups/{doc_id}-{run}.json`
- `data/drafts/{date}-{doc_id}.md`
- `data/site/index.html`
- `data/edinet_watch.sqlite3`

## Notes

- The watcher filters EDINET document type codes `350`, `360`, `370`, and `380`.
- It deduplicates by EDINET `doc_id`.
- Immediate OpenAI filing drafts are generated for `350` and `360`; `370` and `380` are parsed but skipped for immediate drafting.
- Initial `350` filings create monthly follow-up schedules, defaulting to six reports at 30-day intervals.
- OpenAI filing generation is two-stage: structured filing summary, then article draft.
- Static publishing currently builds local HTML and can deploy through Firebase CLI. The deploy boundary is isolated so it can later be replaced by Firebase Hosting REST API deployment.
