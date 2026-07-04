Yes. The whole project is feasible. I would treat it as a **low-cost serverless monitoring and drafting system**, not as a fully automated publishing system at first.

## 1. EDINET monitoring: feasible

The EDINET side is well suited to this. Japan’s large shareholding regime requires a Large Shareholding Report when a holder exceeds 5%, and a Change Report when the holding ratio later increases or decreases by 1% or more, generally within five business days. ([Financial Services Agency][1]) These reports are submitted through EDINET and made publicly available online. ([Financial Services Agency][1])

A technical point: what you called a later “amendment” should usually be separated into:

| Filing type                         | Meaning                                                         |
| ----------------------------------- | --------------------------------------------------------------- |
| 大量保有報告書 / Large Shareholding Report | Initial 5%+ filing                                              |
| 変更報告書 / Change Report               | Subsequent 1%+ change, change in purpose, other material change |
| 訂正報告書 / Amendment Report            | Correction of a prior filing                                    |

`edinet-tools` looks suitable as a starting point. Its documentation says it can fetch EDINET listings, download filings in XBRL/PDF/HTML, parse document types into typed Python objects, and includes large shareholding fields such as `filer_name`, `target_company`, and `ownership_pct`. It also identifies document types 350/360 as large shareholding filings and 370/380 as shareholding change filings. ([GitHub][2])

I would not rely on the package blindly. It is an independent project, not FSA-affiliated, and its own README says to verify data independently before making financial decisions. ([GitHub][2]) For a publication workflow, that is manageable: store the raw EDINET document, parsed fields, and a link back to the source filing.

## 2. Polling architecture: feasible and cheap

For your use case, I would not run a VM. The clean Google Cloud design is:

```text
Cloud Scheduler
   ↓ hourly
Cloud Run Job or Cloud Run Function
   ↓
EDINET API
   ↓
Firestore / Cloud SQL / BigQuery state table
   ↓
OpenAI API
   ↓
Email / Gmail draft / saved Markdown / Substack manual post
```

Google Cloud Scheduler can trigger Cloud Run on a cron-like schedule. ([Google Cloud Documentation][3]) Cloud Run Jobs are designed for run-to-completion batch work and can run without exposing a normal HTTP service. ([Google Cloud][4]) Cloud Run charges only for resources used, rounded to 100 milliseconds, subject to the pricing table and free tier. ([Google Cloud][5])

Firebase is also possible, but it is mostly a convenience layer here. Cloud Functions for Firebase can run Python or JavaScript/TypeScript backend code without managing servers, and scheduled Firebase functions use Cloud Scheduler. ([Firebase][6]) If you are already using Firebase for other projects, Firebase Functions plus Firestore would be fine. If this is a standalone data pipeline, I would prefer **Cloud Run Job + Cloud Scheduler + Firestore**.

## 3. Detection logic

The important part is not the cloud platform; it is the state model.

You would keep a table such as:

| Field                       | Purpose                                           |
| --------------------------- | ------------------------------------------------- |
| `doc_id`                    | EDINET document ID; prevents duplicate processing |
| `submit_datetime`           | EDINET filing time                                |
| `doc_type_code`             | 350/360/370/380 etc.                              |
| `filer_edinet_code`         | Submitter                                         |
| `filer_name`                | Activist or fund name                             |
| `target_edinet_code`        | Issuer                                            |
| `target_name`               | Listed company                                    |
| `ownership_pct`             | Current reported holding                          |
| `previous_ownership_pct`    | Extracted from prior filing if available          |
| `purpose_of_holding`        | Important for activism angle                      |
| `important_proposal_rights` | Whether proposal rights etc. are mentioned        |
| `raw_xbrl_path`             | Stored raw source                                 |
| `processed_at`              | Pipeline audit trail                              |
| `llm_report_status`         | pending / generated / reviewed / sent             |

The hourly job would:

1. Query EDINET document listings for the relevant date window.
2. Filter to large shareholding/change/amendment document types.
3. Filter submitters against your activist list.
4. Download and parse new documents.
5. Compare with the last stored filing for the same activist/issuer pair.
6. Generate a structured transaction summary.
7. Send that summary to the LLM.
8. Store the LLM article draft and notify you.

You should poll more than “only the last hour”. Use a rolling window, perhaps the last 2–3 business days, and deduplicate by `doc_id`. That protects you against outages, EDINET maintenance, retries, and timezone/date-boundary issues.

## 4. LLM report generation: feasible

The OpenAI API is well suited to this. The current API documentation describes the Responses API as the direct path for model requests, including text and JSON outputs. ([OpenAI Developers][7]) For this workflow I would use two LLM calls, not one:

| Step                     | Output                                                                             |
| ------------------------ | ---------------------------------------------------------------------------------- |
| Extraction/normalisation | Strict JSON: filer, issuer, percentages, filing type, purpose, change, key clauses |
| Article drafting         | Markdown or HTML suitable for Substack                                             |

Structured Outputs are useful for the first step because they constrain the model to a supplied JSON schema. ([OpenAI Developers][8]) This matters because the article draft should be based on a verified intermediate data object, not merely on prose generated from a long filing.

I would also put the following rule in the prompt: **do not infer facts not present in the EDINET filing; distinguish filing facts from commentary.** EDINET itself warns that Japanese originals are authoritative and that English translations or XBRL reference information may not be assured. ([disclosure2.edinet-fsa.go.jp][9])

## 5. Substack posting: partly feasible, but not the best first target

This is the weak point. Substack now has API terms, updated January 2026, but the permitted “Authorized Data” described there appears focused on public creator/publication profile data, not creating or publishing posts. ([Substack][10]) Substack also says API access is subject to rate limits and may be modified, suspended, or terminated. ([Substack][10])

Substack’s own publishing support materials describe publishing through the profile, website, app, and publisher dashboard, including managing drafts. ([Substack Support][11]) I would therefore assume **no stable official post-creation API** unless your logged-in publisher account exposes documentation showing otherwise.

Practical alternatives:

| Option                                                       | Recommendation                                                         |
| ------------------------------------------------------------ | ---------------------------------------------------------------------- |
| Fully automatic Substack post                                | I would avoid initially                                                |
| Generate Markdown/HTML and email it to yourself              | Best MVP                                                               |
| Create a Gmail draft with title/body/source links            | Very practical                                                         |
| Store draft in Google Drive / Firestore / GitHub             | Good audit trail                                                       |
| Use browser automation against Substack                      | Possible but brittle and potentially contrary to platform expectations |
| Publish first to WordPress/Ghost then import/RSS to Substack | Possible, but indirect and not ideal for timely alerts                 |

For your stated purpose, I would make the system produce a **ready-to-paste Substack draft** plus a short factual checklist. Manual review is also prudent because activist filings can be market-sensitive and easy to misread.

## 6. Cost profile

The likely cost drivers are:

1. **LLM calls**, especially if you include long XBRL text blocks.
2. **Document downloads and storage**, probably small.
3. **Cloud Run execution**, likely negligible for hourly polling.
4. **Database**, likely minimal if using Firestore or a small Cloud SQL/Postgres instance.

A low-cost version would use:

```text
Cloud Scheduler: hourly
Cloud Run Job: Python script
Firestore: processed filings + activist list
Cloud Storage: raw EDINET XBRL/PDF files
Secret Manager: EDINET and OpenAI API keys
Gmail/email: notification with draft
```

BigQuery is useful later if you want historical analytics, but not necessary for the MVP.

## 7. Main risks

The project is technically straightforward, but these risks are real:

| Risk                                               | Mitigation                                                                |
| -------------------------------------------------- | ------------------------------------------------------------------------- |
| Activist names vary across filings                 | Maintain EDINET submitter codes, not only English/Japanese names          |
| Group/joint holder filings are complex             | Parse joint holders and beneficial ownership carefully                    |
| “1% change” may not equal a simple market purchase | Extract transaction table and purpose text                                |
| EDINET parsed fields may miss important narrative  | Store and review raw XBRL/text blocks                                     |
| LLM hallucination                                  | Use structured extraction first; cite filing facts; keep raw source links |
| Substack automation uncertainty                    | Notify/draft first, publish manually                                      |
| Duplicate alerts                                   | Deduplicate by EDINET `doc_id` and activist-target pair                   |
| Delayed filings                                    | Poll rolling windows, not just current hour                               |

## 8. My recommended MVP

Build it in four stages:

1. **EDINET watcher only**
   Hourly job finds new 350/360/370/380 filings by your activist list and emails you a factual alert.

2. **Parser and database**
   Store each activist-target history and calculate changes from prior filing.

3. **LLM draft generation**
   Produce a Markdown draft with: headline, transaction summary, why it matters, prior filing history, caveats, and source links.

4. **Manual Substack workflow**
   Send yourself the draft and publish manually. Revisit API automation only after the rest is reliable.

Overall: **highly feasible**, with the qualification that Substack should be treated as a manual publication endpoint unless and until you confirm official write-access for your account. The best first build is a reliable EDINET-to-draft pipeline, not an EDINET-to-auto-publish pipeline.

[1]: https://www.fsa.go.jp/en/laws_regulations/faq_on_fiea/section05.html "FAQ on Financial Instruments and Exchange Act : Financial Services Agency"
[2]: https://github.com/matthelmer/edinet-api-tools "GitHub - matthelmer/edinet-tools: Python library for Japanese corporate disclosure data. 42 EDINET document types parsed as typed Python. 11,000+ entities resolvable by name, ticker, EDINET code, or 法人番号. · GitHub"
[3]: https://docs.cloud.google.com/run/docs/triggering/using-scheduler?utm_source=chatgpt.com "Running services on a schedule"
[4]: https://cloud.google.com/run?utm_source=chatgpt.com "Cloud Run"
[5]: https://cloud.google.com/run/pricing?utm_source=chatgpt.com "Cloud Run pricing"
[6]: https://firebase.google.com/docs/functions?utm_source=chatgpt.com "Cloud Functions for Firebase"
[7]: https://developers.openai.com/api/docs/guides/text?utm_source=chatgpt.com "Text generation | OpenAI API"
[8]: https://developers.openai.com/api/docs/guides/structured-outputs?utm_source=chatgpt.com "Structured model outputs | OpenAI API"
[9]: https://disclosure2.edinet-fsa.go.jp/week0020.aspx "EDINET"
[10]: https://substack.com/api-tos "Terms of Service for the Substack API"
[11]: https://support.substack.com/hc/en-us/articles/29152946791188-How-can-I-publish-on-Substack?utm_source=chatgpt.com "How can I publish on Substack?"
