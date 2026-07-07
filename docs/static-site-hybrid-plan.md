## Plan: Static Firebase Site With Rich Client Search

Keep Firebase Hosting as a static deployment target, but upgrade the generated site so it ships a structured index dataset and client-side UI logic for sorting/filtering. This keeps ops simple (no always-on backend), reuses your existing publish pipeline, and still enables responsive table interactions and per-activist pages keyed by filer EDINET code.

**Steps**
1. Phase 1 - Define front-end data contract from existing draft/report records.
2. In the publisher build flow, derive one normalized record per draft containing doc_id, filing/report title, filing date, filer name, filer_edinet_code, target company, doc_type_code, ownership_pct (if available), and page URL.
3. Preserve backward compatibility by sourcing fields from the existing report JSON shape first, then fallback to filings metadata/draft title where report fields are missing.
4. Phase 2 - Extend static generation outputs.
5. Update index generation so the homepage emits a table container instead of only an unordered list, and include hooks for sorting and filter controls.
6. During publish build, emit a static JSON index artifact in the site output that contains the normalized records used by the table.
7. Generate per-filer static index pages (one page per filer_edinet_code) that can be loaded directly by URL and list only matching records.
8. Generate a small optional filer-directory index to link all filer pages from a single navigation point.
9. Phase 3 - Add client-side interactivity while staying static.
10. Add a lightweight browser script to load the JSON index, render rows, sort by selected columns, and filter by filer_edinet_code.
11. Implement URL-state behavior so homepage filters can be shared/bookmarked using query params.
12. Ensure progressive fallback: if JS fails, keep a minimal server-generated list/table so the site remains usable.
13. Phase 4 - Pipeline and deployment fit.
14. Keep publish/deploy command behavior unchanged so Cloud Run still runs publish then Firebase deploy.
15. Ensure generated static assets are written into the same site directory structure currently deployed by Firebase Hosting.
16. Phase 5 - Testing and rollout.
17. Add unit tests around normalization logic and index generation (including missing-field cases and non-ASCII filer names).
18. Run pipeline in dry-run data folder and verify generated homepage sorting/filtering and per-filer pages before production publish.
19. Deploy to preview or production Firebase target and validate page-load size/performance and direct-link routing for filer pages.

**Relevant files**
- /home/marukun/edinet_check/edinet_watcher/publisher.py - main static-site generation flow; extend build outputs, index rendering, and per-filer page generation.
- /home/marukun/edinet_check/edinet_watcher/storage.py - existing draft retrieval path (all_drafts) and related metadata that can be used in normalized records.
- /home/marukun/edinet_check/edinet_watcher/firestore_storage.py - confirm parity for all_drafts output shape when running in cloud Firestore backend.
- /home/marukun/edinet_check/edinet_watcher/pipeline.py - publish orchestration and stability checks after generator changes.
- /home/marukun/edinet_check/data/site/index.html - current generated output pattern used as baseline for expected structural changes.
- /home/marukun/edinet_check/data/site/style.css - current styling baseline for table/filter UI additions.
- /home/marukun/edinet_check/docs/cloud-deployment.md - deployment workflow reference to confirm no infrastructure changes are required.

**Verification**
1. Run publish with representative data and confirm index output includes sortable/filterable fields for each report.
2. Open the generated homepage and verify client-side sort works at least for filing date, filer name, and ownership percentage.
3. Filter by known filer_edinet_code and confirm only matching rows are shown.
4. Open a generated filer page directly by URL and verify it contains only that filer’s reports with valid links.
5. Re-run with Firestore-backed storage configuration and confirm generated index fields are unchanged.
6. Validate that firebase deploy still publishes successfully with no change to Cloud Run/Cloud Scheduler orchestration.

**Decisions**
- Included: static JSON + client-side interactivity, sortable homepage table, filer EDINET code filtering, and per-filer static pages.
- Excluded for first release: full-text search across article body and advanced multi-field faceted search.
- Architecture choice: static-hybrid model (JAMstack-style) over live browser Firestore queries for lower complexity and cost.

**Further Considerations**
1. Index payload size control for long history: optionally cap initial load with pagination or split JSON by month/year while keeping static hosting.
2. Data consistency rule: define precedence when metadata conflicts with parsed fields (for example filing date or target name), and document it in publisher normalization.
3. SEO preference for filer pages: optionally include summary metadata and canonical links once page structure is stable.
