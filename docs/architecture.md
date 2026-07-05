# EDINET Watcher Architecture

This document explains how the project fits together. It is written as a map
for reading the code, not as a formal specification.

## Overview

The program watches EDINET for large-shareholding filings from a configured
list of activist investors.

At a high level it does seven jobs:

1. Load settings from `.env`.
2. Scan EDINET for recent large-shareholding filings.
3. Keep only filings whose filer matches `activists.yml`.
4. Download, parse, and store useful filing facts.
5. Ask OpenAI to draft immediate reports for initial and amended 5% filings.
6. Schedule monthly follow-up research for initial 5% filings.
7. Email drafts and build a static website that can be deployed to Firebase.

The main command is `edinet-watch`, which is registered in `pyproject.toml`.
That command enters the program through `edinet_watcher/cli.py`.

## Command Flow

The CLI exposes these steps:

```bash
edinet-watch scan --days 3
edinet-watch process
edinet-watch draft
edinet-watch email
edinet-watch followups run
edinet-watch publish
edinet-watch run --days 3
```

The `run` command performs the same steps in order:

```text
scan -> process -> draft -> email
                         -> publish
```

For local testing without OpenAI, use:

```bash
edinet-watch draft --offline
edinet-watch followups run --offline
```

or:

```bash
edinet-watch run --days 3 --offline --no-email
```

## Data Flow

The normal live flow looks like this:

```text
EDINET API
  -> FilingMetadata objects
  -> filings table
  -> raw ZIP files under data/raw/
  -> parsed JSON under data/parsed/
  -> filing_history table
  -> OpenAI report JSON under data/reports/
  -> Markdown draft under data/drafts/
  -> drafts table
  -> SMTP email
  -> static HTML under data/site/
```

Initial `350` filings also create follow-up schedules. A daily
`edinet-watch followups run` command checks due schedules, asks OpenAI to run a
web-search-backed research prompt, and stores the result as another draft.

Each stage records its progress so the next command knows what to work on.
For example, `process` looks for filings with status `discovered`,
`download_failed`, or `parse_failed`. This makes failed rows retryable.

## Main Modules

### `cli.py`

Builds the command-line interface with `argparse`.

It does very little business logic. Its job is to:

- parse command-line arguments,
- load settings from `.env`,
- create a `Pipeline`,
- call the requested pipeline method,
- print a small JSON result.

### `pipeline.py`

This is the central coordinator. If you want to understand the program, read
this file first after `cli.py`.

The important methods are:

- `scan`: fetch metadata from EDINET and insert matching activist filings.
- `process`: download and parse discovered filings.
- `draft`: call OpenAI, or offline helpers, to create report and draft files
  for `350` and `360` filings.
- `email`: send pending drafts through SMTP.
- `followups`: run and manage monthly follow-up research for initial `350`
  filings.
- `publish`: build the static site and optionally deploy through Firebase CLI.
- `run`: call the above steps in order.

The pipeline deliberately delegates specialized work to smaller modules:

- EDINET access goes to `edinet_client.py`.
- Database work goes to `storage.py`.
- OpenAI work goes to `llm.py`.
- Email delivery goes to `emailer.py`.
- Static site generation and Firebase CLI deployment go to `publisher.py`.
- Activist matching goes to `activists.py`.
- Field extraction from parsed filings goes to `parser.py`.

### `config.py`

Loads `.env` and turns environment variables into a `Settings` dataclass.

Important values include:

- `EDINET_API_KEY`
- `OPENAI_API_KEY`
- `OPENAI_MODEL`
- `SMTP_HOST`
- `SMTP_PORT`
- `SMTP_USER`
- `SMTP_PASSWORD`
- `EMAIL_FROM`
- `EMAIL_TO`
- `PROMPT_FOLLOWUP_PATH`
- `FOLLOWUP_MAX_RUNS`
- `FOLLOWUP_INTERVAL_DAYS`
- `SITE_DIR`
- `PUBLIC_SITE_URL`
- `FIREBASE_PROJECT`
- `FIREBASE_SITE`

The `--data-dir` CLI option also flows into `Settings`. By default, artifacts
and the SQLite database are stored under `data/`.

### `edinet_client.py`

Wraps the third-party `edinet-tools` package.

This module converts EDINET document objects into the project's own
`FilingMetadata` dataclass. It also handles downloading raw document ZIP files
and parsing documents into Python dictionaries.

The rest of the program talks to this wrapper instead of using `edinet-tools`
directly. That keeps EDINET-specific details in one place.

### `activists.py`

Loads `activists.yml` and decides whether a filing belongs to an activist you
care about.

Matching prefers exact EDINET submitter code matches. It then falls back to
name and alias matching.

### `storage.py`

Owns SQLite access.

It creates the database tables, inserts discovered filings, updates statuses,
stores parsed filing history, schedules follow-ups, and tracks whether
generated drafts still need to be emailed or published.

### `parser.py`

Extracts a few useful fields from the parsed EDINET output:

- ownership percentage,
- purpose of holding,
- important proposal rights.

The parsed EDINET dictionaries may vary in shape, so these helpers search
recursively for known field names.

### `llm.py`

Owns OpenAI calls and offline draft generation.

The immediate filing path has two calls:

1. `extract` asks OpenAI for a structured JSON summary.
2. `draft_article` asks OpenAI to turn that summary into a Markdown article.

The offline path uses deterministic local functions instead:

- `offline_summary`
- `offline_article`
- `offline_followup_article`

Monthly follow-ups use `followup_research`, which enables OpenAI hosted web
search and asks for cited public developments since the initial filing.

### `publisher.py`

Builds static HTML from generated Markdown drafts under `data/site/`.

The current deploy backend shells out to Firebase CLI with `firebase deploy
--only hosting`. The deploy boundary is intentionally narrow so it can later be
replaced by Firebase Hosting REST API calls without changing the pipeline or
report generation code.

### `emailer.py`

Sends generated draft/report files through SMTP.

For Gmail, this normally means:

```env
SMTP_HOST=smtp.gmail.com
SMTP_PORT=587
SMTP_USER=youraddress@gmail.com
SMTP_PASSWORD=your_google_app_password
EMAIL_FROM=youraddress@gmail.com
EMAIL_TO=youraddress@gmail.com
```

## Database Tables

The database file is:

```text
data/edinet_watch.sqlite3
```

### `filings`

One row per discovered EDINET filing.

Important columns:

- `doc_id`: EDINET document ID, used as the primary key.
- `doc_type_code`: EDINET type code, such as `350` or `370`.
- `filer_name`: filer name from EDINET.
- `target_name`: target company name if EDINET exposes it in metadata.
- `status`: current pipeline state.
- `error`: last error message if a step failed.
- `metadata_json`: full metadata captured as JSON.

Common statuses:

- `discovered`: scan found it, but it has not been parsed yet.
- `download_failed`: raw EDINET download failed.
- `parse_failed`: parsing failed.
- `parsed`: parsed JSON and history were written.
- `llm_failed`: OpenAI or draft generation failed.
- `drafted`: report and draft files were generated.
- `draft_skipped`: parsed successfully, but immediate drafting is skipped
  because the filing is a `370` or `380`.

### `filing_history`

One row per successfully parsed filing.

This table stores extracted facts such as ownership percentage and holding
purpose. It also lets the program compare a new filing with the previous
known filing for the same activist-target pair.

### `drafts`

One row per generated draft.

Important columns:

- `report_path`: JSON report file.
- `draft_path`: Markdown draft file.
- `email_status`: usually `pending` or `sent`.
- `publish_status`: usually `pending`, `built`, or `deployed`.
- `public_url`: static site URL if configured.

The `email` command only sends rows where `email_status = 'pending'`.
The `publish` command renders all known drafts into static HTML and updates
publish status.

### `followups`

One row per initial `350` filing that should receive monthly follow-up
research.

Important columns:

- `root_doc_id`: the initial `350` EDINET document ID.
- `status`: `active`, `paused`, `stopped`, `completed`, or `failed`.
- `next_run_date`: the next daily run date that should trigger research.
- `run_count`: number of monthly follow-ups already generated.
- `max_runs`: default six.
- `interval_days`: default thirty.

### `followup_runs`

One row per generated monthly follow-up report.

## Artifact Files

The project writes files under `data/` by default:

```text
data/raw/{doc_id}/xbrl.zip
data/parsed/{doc_id}.json
data/reports/{doc_id}.json
data/followups/{doc_id}-{run}.json
data/drafts/{date}-{doc_id}.md
data/site/index.html
data/edinet_watch.sqlite3
```

The SQLite database is the workflow tracker. The files are the evidence and
draft outputs for each filing.

## Typical Local Test Flow

After setting up `.venv`:

```bash
. .venv/bin/activate
python -m pytest
```

To test with real EDINET data but no OpenAI or email:

```bash
edinet-watch scan --days 3
edinet-watch process
edinet-watch draft --offline
edinet-watch followups run --offline
edinet-watch publish
```

To test the full live path:

```bash
edinet-watch scan --days 3
edinet-watch process
edinet-watch draft
edinet-watch email
edinet-watch publish --deploy
```

To use a clean test database and avoid changing your normal `data/` directory:

```bash
edinet-watch --data-dir data-live-test run --days 3
```

## Convenient Database Inspection

List recent filing statuses:

```bash
python -c "import sqlite3; c=sqlite3.connect('data/edinet_watch.sqlite3'); c.row_factory=sqlite3.Row; [print(dict(r)) for r in c.execute('select doc_id,status,error,filer_name,target_name from filings')]"
```

List draft email states:

```bash
python -c "import sqlite3; c=sqlite3.connect('data/edinet_watch.sqlite3'); c.row_factory=sqlite3.Row; [print(dict(r)) for r in c.execute('select doc_id,email_status,publish_status,draft_path,report_path from drafts')]"
```

List follow-up schedules:

```bash
edinet-watch followups list
```

In VS Code, a SQLite viewer extension can open `data/edinet_watch.sqlite3`
directly and show the tables.

## Reading Path

A good order for learning the code is:

1. `README.md`
2. `docs/architecture.md`
3. `edinet_watcher/cli.py`
4. `edinet_watcher/pipeline.py`
5. `edinet_watcher/storage.py`
6. `edinet_watcher/edinet_client.py`
7. `edinet_watcher/llm.py`
8. `edinet_watcher/emailer.py`
9. `edinet_watcher/publisher.py`

The key mental model is this: the pipeline moves filings through statuses in
SQLite, while artifact files under `data/` hold the raw, parsed, and drafted
content.
