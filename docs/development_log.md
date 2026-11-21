# Carthij Development Log

High-level chronological log of completed architecture and implementation phases.

---

## 2026-11-21 — Initial Repository Setup & Architecture Planning

- **Initial Commit (`d89d301`)**: Committed the initial multi-agent workflow and Next.js UI scaffold to `https://github.com/osujii191/carthij.git` (`main`). Updated `.gitignore` for generated Next.js/TypeScript files.
- **Query Understanding & OpenAPI-Compliant Parallel Tavily Search**:
  - Upgraded `src/core/tavily.py` to match Tavily's `POST /search` OpenAPI spec (parameter constraint enforcement, shared `httpx.AsyncClient` connection pooling via `search_many()`, and `Retry-After` backoff on `429`/`434`/`5xx`).
  - Upgraded `src/agents/planner.py` with structured `QueryUnderstanding`, domain-preferred `PlannedQuery` generation, and cross-query URL deduplication.
- **Architecture Plan**: Finalized [backend_architecture_plan.md](./backend_architecture_plan.md) locking in hybrid Tavily retrieval, opinionated specialist agents, canonical structured `FundingReport`, programmatic citation grounding, Firebase (Firestore + Emulator Suite + `firestore.rules`) + Cloud Run deployment, and multi-instance SSE streaming.

---

## 2026-11-21 — Phase 1: Retrieval & Canonical Models (Completed)

- **Tavily `/extract` Support** (`src/core/tavily.py`):
  - Added typed `TavilyExtractRequest`, `TavilyExtractResult`, `TavilyFailedExtract`, and `TavilyExtractResponse`.
  - Implemented `TavilyClient.extract()` with retry/backoff handling and offline simulation fallback.
- **Canonical Structured Report Models** (`src/models/report.py`, `src/models/__init__.py`):
  - Defined `FundingReport`, `FundingOpportunity`, `ReadinessGap`, `ActionItem`, `StageProgress`, `SourceRecord`, and `SpecialistFinding`.
  - Promoted `owner_uid`, `submitter_email`, `intake_data`, `created_at`, and `updated_at` to top-level fields for Firestore indexing and security rules readiness.
  - Added `SourceRecord.to_persisted_dict(max_excerpt_chars=2000)` to keep Firestore documents well under the 1 MiB limit while retaining full extracted markdown in memory during pipeline execution.
- **Concurrency-Safe Source Registry** (`src/pipeline/registry.py`, `src/pipeline/__init__.py`):
  - Implemented `RetrievedSourceRegistry` with `asyncio.Lock`, deterministic monotonic `SRC-N` IDs, `normalize_url()` deduplication, authority-weighted URL selection for `/extract`, `verify_url()` for citation grounding, and `[SRC-N]` prompt formatting.
- **Hybrid Retrieval & API Hygiene** (`src/agents/planner.py`, `src/api/app.py`):
  - Added `QueryPlanner.execute_and_enrich()` and updated `plan_and_search()` to run parallel `/search` + selective `/extract` on top authoritative URLs.
  - Gated `POST /api/v1/query-plan` behind `CARTHIJ_DEV_TOOLS=true` so internal pipeline stages stay off the public API surface by default.
- **Verification**: All 12 unit tests pass (`uv run python -m unittest discover -s tests -v`).

---

## 2026-11-21 — Phase 2: Opinionated Specialists & Grounding (Completed)

- **Two-Phase `BaseAgent` Tool + Structured Output Execution** (`src/core/agent.py`):
  - Updated `BaseAgent.run()` so agents with both `tools` and `response_schema` execute tool-calling turns without `response_schema`, followed by a final structured extraction turn with `response_schema` and `tools=None` for reliable Gemini Developer API & Vertex AI parity.
- **Bounded Tavily Follow-Up Tool Suite** (`src/agents/specialists/tools.py`):
  - Implemented `BoundedTavilyToolSuite` enforcing per-specialist call caps (`<= 2`) inside the tool wrapper itself and automatically registering all follow-up search/extract URLs into the shared `RetrievedSourceRegistry`.
- **Opinionated 4-Specialist Roster** (`src/agents/specialists/roster.py`):
  - Implemented `build_specialists()` returning deterministic specialists routed by `org_type` and `target_state`: `state_local_programs`, `federal_foundation_grants`, `capital_strategy` (Startup) or `philanthropic_strategy` (Non-Profit), and `eligibility_compliance` (pure registry analysis, no extra Tavily calls), all emitting typed `SpecialistFinding` outputs.
- **Programmatic Citation Grounding & Synthesis** (`src/pipeline/grounding.py`, `src/agents/synthesizer.py`):
  - Implemented `verify_and_ground_opportunities()` to verify every candidate `FundingOpportunity` against `RetrievedSourceRegistry`, drop unverified/hallucinated URLs, normalize vague deadlines/amounts to `"Unverified — check source"`, and deduplicate overlapping programs across specialists.
  - Implemented `ReportSynthesizer` emitting structured `SynthesisOutput` (`executive_summary`, `readiness_score`, `prioritized_action_plan`) strictly from verified opportunities.
- **3-Stage Pipeline Orchestrator & Facade** (`src/pipeline/orchestrator.py`, `src/agents/coordinator.py`):
  - Replaced dynamic subagent invention with `FundingPipelineOrchestrator` (Stage 1: QueryPlanner + Hybrid Tavily → Stage 2: Parallel Specialists → Stage 3: Grounding & Synthesis) with an async `on_progress` hook for Phase 3 Firestore/SSE streaming.
  - Kept `CoordinatorAgent` as a backwards-compatible facade over `FundingPipelineOrchestrator`.
- **Verification**: All 16 unit tests pass (`uv run python -m unittest discover -s tests -v`).

---

## 2026-11-21 — Phase 3: Firestore Repository, Security Rules, Job Dispatcher & SSE Streaming (Completed)

- **Repository Abstraction** (`src/core/repository.py`):
  - Defined async `ReportRepository` protocol with `create`, `get`, `list_reports`, `save`, atomic `append_stage_progress`, and `mark_failed`.
  - Implemented `FirestoreReportRepository` (primary, works with both production Cloud Firestore and Firebase Local Emulator via `FIRESTORE_EMULATOR_HOST`, using `firestore.ArrayUnion` for concurrent stage updates) and `SQLiteReportRepository` (local/unit-test fallback with `asyncio.Lock` + context-managed connections).
- **Firebase Config & Security Rules** (`firestore.rules`, `firestore.indexes.json`, `firebase.json`):
  - Authored `firestore.rules` (via `firestore-rules-author`): default-deny all client writes (backend Admin SDK owns writes) and restrict `reports/{reportId}` client reads strictly to authenticated owners (`request.auth.uid == resource.data.owner_uid`).
  - Added composite index (`owner_uid ASC, created_at DESC`) in `firestore.indexes.json` and local Firestore Emulator config in `firebase.json`.
- **Job Dispatcher Abstraction** (`src/core/dispatcher.py`):
  - Implemented `JobDispatcher` protocol with `LocalAsyncDispatcher` (in-process `asyncio.create_task` with strong task refs) and `CloudTasksDispatcher` (Cloud Tasks HTTP POST to `/internal/worker/execute` with OIDC token / worker secret).
- **Canonical Report API & Multi-Instance SSE** (`src/api/app.py`):
  - Added `POST /api/v1/reports` (`202 Accepted`), `GET /api/v1/reports`, `GET /api/v1/reports/{report_id}`, `GET /api/v1/reports/{report_id}/stream` (multi-instance-safe SSE backed by `ReportRepository`), and `POST /internal/worker/execute`.
  - Kept `POST /api/v1/webhook/submit` as a backwards-compatible wrapper backed by `ReportRepository`.
  - Documented Firebase/Firestore & Cloud Tasks env vars in `.env.example` and added `[project.optional-dependencies] gcp` in `pyproject.toml`.
- **Verification**: All 18 unit tests pass (`uv run python -m unittest discover -s tests -v`).

---

## 2026-11-21 — Phase 4: Cloud Run, Firebase App Hosting & Structured UI Upgrade (Completed)

- **Cloud Run & Firebase App Hosting Deployment Configs** (`Dockerfile`, `.dockerignore`, `ui/apphosting.yaml`, `firebase.json`):
  - Added production `Dockerfile` for Cloud Run (`python:3.12-slim` + `uv`, installs `.[gcp]` extras, runs non-root `uvicorn` on `$PORT`) and `.dockerignore`.
  - Added `ui/apphosting.yaml` and wired the `apphosting` block (`backendId: "carthij-ui"`, `rootDir: "ui"`) into `firebase.json`.
- **Canonical `FundingReport` & Live SSE UI Upgrade** (`ui/types/index.ts`, `ui/components/IntakeForm.tsx`, `ui/components/ResultsPanel.tsx`, `ui/app/reports/page.tsx`, `ui/app/reports/[id]/page.tsx`):
  - Updated TypeScript contracts to match the canonical `FundingReport` schema.
  - Switched `IntakeForm` to `POST /api/v1/reports` (`202 Accepted`) and immediate redirect to `/reports/{report_id}`.
  - Upgraded `/reports/[id]` to subscribe to live Server-Sent Events (`GET /api/v1/reports/{id}/stream`) with a 5-stage real-time progress indicator.
  - Upgraded `ResultsPanel` to display Readiness Score (`0–100`), scope-filterable Verified Funding Opportunities with `[SRC-N]` citations, Readiness Gaps by severity, Prioritized 7d/30d/90d Action Plan, Specialist Summaries, and the Verified Source Registry.
- **Verification**: Backend unit tests (18/18) and Next.js production build (`npm run build`) verified cleanly.

---

## 2026-11-21 — Production Hardening Pass (Code Review P1/P2/P3 Fixes)

Addressed all P1 and high-value P2/P3 findings from [phase_1_2_code_review.md](./phase_1_2_code_review.md):

- **P1 Fixes**:
  - `Dockerfile`: Copy `pyproject.toml`, `README.md`, and `src/` before `uv pip install --system --no-cache ".[gcp]"` so `hatchling` builds cleanly on Cloud Run.
  - `ui/app/reports/[id]/page.tsx`: Distinguished server-sent `event: error` frames from transient `EventSource` disconnects and added a seamless polling fallback if SSE closes early.
  - `src/api/app.py` & `src/pipeline/orchestrator.py`: Preserved initial `"queued"` stage in `stage_history` and original `created_at` on final `save(report)` so SSE never skips the final `"completed"` stage event.
  - `src/pipeline/grounding.py`: Restricted `source_id` fallback to blank URLs or matching hosts so hallucinated URLs cannot hijack `SRC-1`; added cross-URL same-program deduplication preferring `.gov` / authoritative sources.
  - `src/core/agent.py`: Defaulted `reset_history: bool = True` in `BaseAgent.run()` and guarded empty `response.candidates`.
  - `src/pipeline/registry.py`: Mapped redirected Tavily `/extract` URLs back to the original requested `SRC-N` record, prioritized extracted/authoritative sources in `list_sources()`, and stripped tracking params (`utm_*`, `fbclid`, `gclid`) in `normalize_url()`.
- **P2/P3 Fixes**:
  - Isolated `callback_url` webhook delivery in its own `try/except`, added SSE timeout (`max_duration_seconds`) + `request.is_disconnected()`, enabled SQLite WAL mode, switched Firestore `.where()` to `FieldFilter`, cached `CloudTasksClient` with OIDC `audience`, added Firebase Emulator logs to `.gitignore`, added `http(s)://` link guard in `ResultsPanel.tsx`, normalized `STATE_GUIDANCE` aliases, deduplicated `readiness_gaps`, added synthesizer fallback + fail-fast when all specialists fail, emitted `"extracting"` after `/search` completes, and closed legacy SQLite connections cleanly.
- **Verification**: All 20 backend unit tests pass with zero SQLite `ResourceWarning`s, and `ui/` `npm run build` succeeds cleanly.

---

## 2026-11-21 — Draft Phase A: Browser-Use Draft Models, Repository, Firestore Rules & Demo Grant Portal (Completed)

- **Canonical Draft Models** (`src/models/draft.py`, `src/models/__init__.py`):
  - Defined `ApplicationDraft`, `BrowserStepEvent`, `FilledFieldRecord`, `MissingInputField`, `DraftCreateRequest`, `DraftReviseRequest`, and `DraftAcceptedResponse`.
  - Enforced metadata-only `step_log` serialization (`to_metadata_dict()`) paired with a single `latest_screenshot_b64` thumbnail field for Firestore 1 MiB safety and multi-instance SSE streaming.
- **Draft Repository** (`src/core/draft_repository.py`):
  - Implemented `DraftRepository` protocol with `SQLiteDraftRepository` (WAL mode + `asyncio.Lock`) and `FirestoreDraftRepository` (`application_drafts/{draftId}`, `FieldFilter`, `firestore.ArrayUnion` on `step_log` + atomic overwrite on `latest_screenshot_b64`).
- **Firestore Security Rules & Indexes** (`firestore.rules`, `firestore.indexes.json`):
  - Updated `firestore.rules` (via `firestore-rules-author`) to add locked-down rules for `application_drafts/{draftId}` (deny all client writes; owner-only authenticated reads) and added composite indexes `(report_id ASC, created_at DESC)` and `(owner_uid ASC, created_at DESC)`.
- **Stage-Proof 3-Step Demo Grant Portal** (`ui/app/demo-portal/page.tsx`):
  - Built a realistic 3-step State Innovation & Workforce Grant Portal with deterministic `id`/`name` attributes on React controlled inputs, non-blocking Step 1 navigation when `Federal EIN` / `SAM.gov UEI` are blank, 250-word abstract counter on Step 2, and missing-field warnings + Save Draft / Submit Final Application buttons on Step 3.
- **Verification**: All 21 backend unit tests pass and `ui/` `npm run build` succeeds cleanly.

---

## 2026-11-21 — Draft Phase B: Playwright Browser-Use Driver, SSRF Guard, FormMapperAgent & Orchestrator (Completed)

- **SSRF & Portal URL Guard** (`src/drafter/security.py`):
  - Implemented `validate_portal_url()` allowing only `http(s)://` and the configured `DEMO_PORTAL_BASE_URL` origin while blocking cloud metadata IPs (`169.254.169.254`, `metadata.google.internal`), loopback, link-local, and RFC1918 private networks.
- **BrowserDriver Protocol & Implementations** (`src/drafter/browser.py`):
  - Implemented `PlaywrightBrowserDriver` sharing a single lazily-initialized Chromium `Browser` guarded by `asyncio.Semaphore(2)` with isolated contexts closed in `finally:`, DOM form schema extraction, login/CAPTCHA detection, native Playwright `.fill()`/`.select_option()`/`.check()` for React 19 controlled inputs, element highlighting, and compressed JPEG screenshot capture.
  - Implemented `SimulatedBrowserDriver` matching the 3-step `/demo-portal` for zero-dependency unit tests and offline CI.
- **Grounded Form Mapper & Multi-Step Orchestrator** (`src/drafter/mapper.py`, `src/drafter/orchestrator.py`):
  - Implemented `ApplicantFactLedger` + `FormMapperAgent` (Gemini 3.8 Flash, `response_schema=FormFillPlan`) with strict anti-fabrication rules (`skip_missing` + `missing_question` for unverified facts like EIN/UEI).
  - Implemented `BrowserDraftOrchestrator` supporting multi-step wizard navigation (`max_wizard_steps=4`), immediate halt on login/CAPTCHA (`awaiting_human_action`), live per-step `BrowserStepEvent` callbacks, and hard stop before final Submit (`ready_for_review`).
- **Dependencies & Verification**: Added `[project.optional-dependencies] browser = ["playwright>=1.49.0"]` to `pyproject.toml`. All 24 backend unit tests pass cleanly.

---

## 2026-11-21 — Draft Phase C: FastAPI Draft Endpoints, Live Screenshot SSE, Revision Loop & Markdown Export (Completed)

- **Browser-Use Draft API** (`src/api/app.py`):
  - Added `POST /api/v1/reports/{report_id}/drafts` (`202 Accepted` with SSRF URL validation, initial draft persistence, and async task dispatch).
  - Added `GET /api/v1/reports/{report_id}/drafts` and `GET /api/v1/drafts/{draft_id}`.
  - Added `GET /api/v1/drafts/{draft_id}/stream`: multi-instance-safe SSE backed by `DraftRepository`, streaming `event: browser_step` with live `screenshot_b64` frames, disconnect/timeout guards, and terminal `complete`/`error` events.
  - Added `POST /api/v1/drafts/{draft_id}/revise` to merge user answers to `missing_inputs`, increment `version`, and re-run browser pre-fill.
  - Added `GET /api/v1/drafts/{draft_id}/export` returning a clean downloadable Markdown application package.
- **Verification**: All 25 backend unit tests pass cleanly (`uv run --no-sync python -m unittest discover -s tests -v`).

---

## 2026-11-21 — Draft Phase D: Live Browser-Watch UI & Interactive Missing-Inputs Revision (Completed)

- **Frontend Types & Opportunity Launch Trigger** (`ui/types/index.ts`, `ui/components/ResultsPanel.tsx`):
  - Added TypeScript interfaces for `ApplicationDraft`, `BrowserStepEvent`, `FilledFieldRecord`, `MissingInputField`, and `DraftAcceptedResponse`.
  - Added a **"Draft & Pre-Fill"** button on every verified `FundingOpportunity` card in `ResultsPanel.tsx` that posts to `POST /api/v1/reports/{id}/drafts` and routes directly into the Live Browser-Watch workspace.
- **Live Browser-Watch Workspace** (`ui/app/reports/[id]/drafts/[draftId]/page.tsx`):
  - Streams live `event: browser_step` screenshots and action logs over SSE (`GET /api/v1/drafts/{draftId}/stream`) with automatic polling fallback.
  - Displays the Human-in-the-Loop pre-Submit safety banner, live browser viewport frame, step-by-step action log, pre-filled fields audit table, drafted narrative copy button, Markdown export button, and interactive **"Re-Fill Portal with My Inputs"** form wired to `POST /api/v1/drafts/{draftId}/revise`.
- **Verification**: Backend unit tests (`25/25`) and Next.js production build (`npm run build`) verified cleanly.
