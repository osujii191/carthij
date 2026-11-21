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
