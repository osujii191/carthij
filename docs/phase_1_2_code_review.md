# Phase 1, Phase 2, Phase 3 & Phase 4 Code Review

**Date:** 2026-11-21  
**Scope:** Full review of Phase 1 (Retrieval & Canonical Models), Phase 2 (Opinionated Specialists & Grounding), Phase 3 (Firestore Repository, Security Rules, Job Dispatcher & SSE Streaming), and Phase 4 (Cloud Run, Firebase App Hosting & Structured UI Upgrade) against [backend_architecture_plan.md](./backend_architecture_plan.md) and [development_log.md](./development_log.md).

---

## 1. Executive Summary

All four phases of the Carthij Backend & UI Architecture Plan have been implemented with high fidelity to the design. All 18 backend unit tests in `tests/test_harness.py` and the Next.js production build (`npm run build`) pass cleanly.

Below is a comprehensive file-by-file review across **Phases 1–4**, highlighting **strengths** and **actionable findings (P1/P2/P3)** to harden the application for production.

---

## 2. Phase 1 Review: Retrieval & Canonical Models

### 2.1 `src/core/tavily.py` — Tavily `/search` & `/extract` Client
**Strengths:**
- Clean Pydantic request/response models (`TavilyExtractRequest`, `TavilyExtractResult`, `TavilyFailedExtract`, `TavilyExtractResponse`) matching the Tavily OpenAPI specification.
- Shared retry/backoff logic handling `429`, `434`, and `5xx` with `Retry-After` header support and deterministic simulation fallback when `TAVILY_API_KEY` is unset.

**Review Comments:**
- **[P3 — DRY / Connection Pooling]**: `_execute_request()` (for `/search`) and `extract()` duplicate the retry/backoff loop (`lines 242–261` and `lines 440–458`). Extracting a small helper (e.g., `_post_with_retry(client, url, payload)`) would eliminate duplication.

### 2.2 `src/models/report.py` — Canonical Report Schema
**Strengths:**
- `FundingReport` promotes `owner_uid`, `submitter_email`, `intake_data`, `created_at`, and `updated_at` to top-level fields, making Firestore indexing and `firestore.rules` ownership checks straightforward.
- `SourceRecord.to_persisted_dict(max_excerpt_chars=2000)` cleanly enforces the Firestore 1 MiB document size guard while keeping full `extracted_content` in memory during pipeline execution.

**Review Comments:**
- **[P3 — Schema Strictness]**: `FundingOpportunity.source_id` defaults to `""`, which is helpful during initial specialist JSON parsing before grounding runs, and `verify_and_ground_opportunities()` populates the canonical `SRC-N` ID afterward. Consider adding a brief docstring note on `FundingOpportunity` clarifying that `source_id` and `citation_verified` are finalized by Stage 3 grounding.

### 2.3 `src/pipeline/registry.py` — `RetrievedSourceRegistry`
**Strengths:**
- Thread/async-safe mutations protected by `asyncio.Lock` with deterministic monotonic `SRC-N` IDs.
- Smart authority-weighted ranking (`AUTHORITATIVE_DOMAINS` + `.gov`/`.org`/`.edu` bonuses) in `select_top_urls_for_extraction()`.
- `format_for_prompt()` clearly tags extracted sources with `[SRC-N] [FULL EXTRACT]`.

**Review Comments:**
- **[P1 — Redirect / Canonical URL Mismatch in `register_extract_response()`]**:
  In `register_extract_response()` (`lines 168–183`), each item returned by Tavily `/extract` is upserted via `self._upsert_unlocked(url=item.url, extracted_content=item.raw_content)`. If Tavily `/extract` normalizes or resolves a redirect on `item.url` (e.g., query parameter ordering, utm tracking params, or a path redirect like `/grants` -> `/grants/overview`), `_upsert_unlocked()` will create a **new** `SourceRecord` with an empty `title`, `score=0.0`, and empty `matched_scopes` instead of enriching the existing `SourceRecord` that was selected for extraction.
  - **Recommendation**: In `select_top_urls_for_extraction()` / `register_extract_response()`, if `item.url` does not match an existing normalized URL and only 1 URL was requested (such as in `tavily_extract_url`), or by passing the requested URL mapping, fall back to attaching `extracted_content` to the original requested URL record.
- **[P2 — Query Parameter Normalization in `normalize_url()`]**:
  `normalize_url()` (`lines 27–35`) preserves `parsed.query` verbatim. If two search results point to the same page with different tracking parameters (e.g., `?utm_source=...`) or reordered query params, they will register as two separate `SRC-N` records.
  - **Recommendation**: Strip common tracking parameters (`utm_*`, `fbclid`, `gclid`) and sort remaining query parameters via `urllib.parse.parse_qsl` + `urlencode`.
- **[P2 — Specialist Context Window After Follow-up Tools]**:
  `format_for_prompt(max_sources=12)` (`line 224`) slices `self.list_sources()[:max_sources]`, which sorts strictly by `score` descending. However, sources added via `register_extract_response()` on an unfamiliar URL have `score=0.0` (`line 70`), meaning they will sort to the bottom of `list_sources()`.
  - **Recommendation**: Give extracted sources a default minimum score (or sort by `(is_extracted, score)` / include `_authority_bonus(s.url)` in `list_sources()`) so extracted and authoritative sources always surface in the top `max_sources`.

### 2.4 `src/agents/planner.py` & `src/api/app.py` — Hybrid Stage 1 & Dev Endpoint Gate
**Strengths:**
- `QueryPlanner.execute_and_enrich()` cleanly coordinates parallel `/search`, `RetrievedSourceRegistry` population, and selective `/extract`.
- `/api/v1/query-plan` is properly gated behind `CARTHIJ_DEV_TOOLS=true` (`src/api/app.py:93`).

**Review Comments:**
- **[P3 — Legacy Deduplication Duplication]**:
  `QueryPlanner.deduplicate_results()` (`lines 247–287`) duplicates the URL deduplication logic that `RetrievedSourceRegistry` already performs during `execute_and_enrich()`. You can derive `deduplicated_sources` directly from `source_registry.list_sources()` to keep a single source of truth.

---

## 3. Phase 2 Review: Opinionated Specialists & Grounding

### 3.1 `src/core/agent.py` — Two-Phase `BaseAgent.run()`
**Strengths:**
- Clean two-phase execution (`two_phase = bool(self._tools_list and self.response_schema)`) that runs tool-calling turns with `include_tools=True, include_schema=False` and then performs a dedicated structured output turn with `include_tools=False, include_schema=True`.
- Directly tested in `test_base_agent_two_phase_tools_then_schema`.

**Review Comments:**
- **[P1 — Stateful `self.history` Across Multiple `run()` Calls]**:
  `BaseAgent` initializes `self.history: List[types.Content] = []` in `__init__()` (`line 48`) and appends to `self.history` on every `run()` call (`line 87`). While `build_specialists()` currently instantiates fresh `BaseAgent` objects per run, `QueryPlanner` and `ReportSynthesizer` instantiate `self.agent = BaseAgent(...)` in `__init__()`. If a `QueryPlanner` or `ReportSynthesizer` instance is ever reused across two requests, the second request will inherit the first request's conversation history!
  - **Recommendation**: Either use a local `history: List[types.Content] = []` inside `BaseAgent.run()` (unless multi-turn chat is explicitly requested via a parameter like `reset_history: bool = True`), or clear `self.history.clear()` at the start of `run()`.
- **[P2 — Defensive Guard on Empty `response.candidates`]**:
  In `BaseAgent.run()` (`lines 109` and `176`), `response.candidates[0]` is accessed directly without checking `if not response.candidates:`. If Gemini blocks a response due to safety filters or transient empty candidates, `response.candidates[0]` will raise an `IndexError`.
  - **Recommendation**: Guard with `if response.candidates and response.candidates[0].content: self.history.append(response.candidates[0].content)`.

### 3.2 `src/agents/specialists/tools.py` — `BoundedTavilyToolSuite`
**Strengths:**
- Enforces `max_calls` inside `_consume_budget()` under an `asyncio.Lock()`, returning a structured `"budget_exhausted"` message to the model rather than raising an unhandled exception.
- Automatically registers all follow-up search and extract results into the shared `RetrievedSourceRegistry`.

**Review Comments:**
- **[P2 — `tavily_extract_url` When URL Was Not Previously in Registry]**:
  When a specialist calls `tavily_extract_url(url)` (`lines 98–136`), `suite.registry.register_extract_response(res)` is called. If the URL was newly discovered or slightly different from the search snippet URL, `register_extract_response` creates a `SourceRecord` with `scope=[]` and `score=0.0`.
  - **Recommendation**: Pass `scope=suite.default_scope` into `register_extract_response()` (or `register_source()`) so follow-up extracted URLs retain the specialist's scope tag (`"state"`, `"federal"`, `"market"`).

### 3.3 `src/agents/specialists/roster.py` — Specialist Roster & Routing
**Strengths:**
- Deterministic routing between `capital_strategy` (Startup) and `philanthropic_strategy` (Non-Profit), plus `state_local_programs`, `federal_foundation_grants`, and tool-free `eligibility_compliance`.
- Clear `STATE_GUIDANCE` for `NY`, `NJ`, and `SF` and shared `GROUNDING_RULES`.

**Review Comments:**
- **[P3 — Jurisdiction Matching for `"San Francisco"` / `"CA"`]**:
  `STATE_GUIDANCE` (`lines 20–33`) keys on `"NY"`, `"NJ"`, and `"SF"`. If a form submits `"CA"`, `"California"`, or `"New York"`, `state_code = (target_state or "NY").upper()` will fall back to the generic hint instead of the rich `STATE_GUIDANCE` prompt.
  - **Recommendation**: Normalize common state names/codes (e.g., mapping `"CA"`, `"CALIFORNIA"`, `"SAN FRANCISCO"` -> `"SF"`, `"NEW YORK"` -> `"NY"`, `"NEW JERSEY"` -> `"NJ"`) before looking up `STATE_GUIDANCE`.

### 3.4 `src/pipeline/grounding.py` — Programmatic Citation Verification
**Strengths:**
- Verifies `opp.source_url` via `registry.verify_url()` with fallback to `registry.get_by_id(opp.source_id)`.
- Normalizes vague values (`"TBD"`, `"N/A"`, `""`, `"varies"`) to `"Unverified — check source"`.
- Deduplicates overlapping opportunities across specialists while merging `key_requirements` and keeping the higher `eligibility_fit`.

**Review Comments:**
- **[P1 — Guard Against `source_id` Fallback Masking a Hallucinated `source_url`]**:
  In `verify_and_ground_opportunities()` (`lines 54–56`):
  ```python
  rec = registry.verify_url(opp.source_url)
  if rec is None and opp.source_id:
      rec = registry.get_by_id(opp.source_id.strip())
  ```
  If an LLM hallucinates a non-existent `source_url` (e.g. `https://fake-agency.gov/grant`) **and** guesses `"SRC-1"` as `source_id` (which almost always exists in the registry since IDs start at `SRC-1`), `verify_url()` returns `None`, but `get_by_id("SRC-1")` succeeds! The opportunity is then silently rewritten to use `SRC-1`'s URL even though the program name/details were hallucinated for a completely different URL.
  - **Recommendation**: Only allow the `registry.get_by_id(opp.source_id)` fallback when `opp.source_url` is empty/missing (or when `opp.source_url`'s domain matches `rec.url`'s domain). If `opp.source_url` is provided and does not match the URL of `opp.source_id`, treat it as unverified and drop it.
- **[P2 — Cross-Specialist Deduplication Key]**:
  `dedup_key = (_slug(grounded.name), rec.source_id)` (`line 79`) works when two specialists cite the exact same `name` and `source_id`. However, if one specialist names a program `"NYSERDA Clean Energy Fund"` and another names it `"NYSERDA Clean Energy Fund (CEF)"` with the same `source_id`, or cites two sub-pages for the exact same `(_slug(grounded.name), _slug(grounded.provider))`, they won't deduplicate.
  - **Recommendation**: Consider deduplicating if either `(_slug(grounded.name), rec.source_id)` **or** `(_slug(grounded.name), _slug(grounded.provider))` matches.

### 3.5 `src/agents/synthesizer.py` & `src/pipeline/orchestrator.py` — Synthesis & Orchestration
**Strengths:**
- `FundingPipelineOrchestrator` cleanly executes Stage 1 -> Stage 2 (`asyncio.gather`) -> Stage 3 and emits `StageProgress` events via `on_progress`.
- Tracks detailed telemetry in `FundingReport.metadata` (`tavily_credits_used`, `specialist_tool_calls`, `citations_verified`, `citations_dropped`, `dropped_opportunities`, `latency_ms`).

**Review Comments:**
- **[P2 — Graceful Degradation When All Specialists Fail or Synthesizer Fails]**:
  In `FundingPipelineOrchestrator.run()` (`lines 167–228`), individual specialist failures are caught in `_run_specialist()`, which is great. However, if all 4 specialists fail (e.g., invalid Gemini API key or quota exhaustion), the orchestrator still calls `synthesizer.synthesize()` with 0 findings, and if `synthesizer.synthesize()` fails or if all specialists failed, the report could either crash without partial data or complete with an empty report.
  - **Recommendation**:
    1. If all specialists fail (`len(findings) == 0`), raise an explicit runtime error (or mark report status as `"failed"`) rather than synthesizing an empty report as `"completed"`.
    2. Wrap `synthesizer.synthesize()` in a `try/except` fallback so that if only the final synthesis LLM call fails after Stage 1 & Stage 2 succeeded, the verified opportunities and readiness gaps from the specialists are still preserved with a deterministic fallback executive summary.
- **[P2 — Accurate `searching` vs. `extracting` Progress Events]**:
  In `orchestrator.py` (`lines 126–145`), both `"searching"` and `"extracting"` stage events are emitted *before* `query_planner.execute_and_enrich()` starts, so clients watching SSE see `"Extracting full program details..."` immediately before the parallel `/search` calls have even completed.
  - **Recommendation**: Emit `"searching"` before `execute_plan()`, and emit `"extracting"` inside `execute_and_enrich()` (or pass `on_progress` into `execute_and_enrich()`) right before `tavily_client.extract()` is called.
- **[P3 — Deduplicating `readiness_gaps` Across Specialists]**:
  `all_gaps.extend(finding.readiness_gaps)` (`line 189`) concatenates gaps from all 4 specialists without deduplication. Multiple specialists often flag the same gap (e.g., `"SAM.gov UEI registration"`). Adding a light deduplication pass on `(gap.category, _slug(gap.description[:60]))` will keep `FundingReport.readiness_gaps` crisp.

---

## 4. Phase 3 Review: Firestore Repository, Security Rules, Job Dispatcher & SSE Streaming

### 4.1 `src/core/repository.py` — `ReportRepository` (`SQLiteReportRepository` & `FirestoreReportRepository`)
**Strengths:**
- Clean `@runtime_checkable` `ReportRepository` protocol with full parity between `SQLiteReportRepository` and `FirestoreReportRepository`.
- Proper `closing(self._connect())` context management in `SQLiteReportRepository`.
- Atomic `firestore.ArrayUnion([progress.model_dump()])` in `FirestoreReportRepository.append_stage_progress()` and `mark_failed()`.
- Seamless detection of `FIRESTORE_EMULATOR_HOST`, `REPORT_STORAGE_BACKEND=firestore`, or `USE_FIRESTORE=true` in `get_report_repository()`.

**Review Comments:**
- **[P1 — `stage_history` and `created_at` Overwrite in `execute_report_job` -> `report_repository.save(report)`]**:
  In `src/api/app.py` (`lines 141–156`), `create_report()` creates an initial `FundingReport` with `created_at=now` and an initial `stage_history=[StageProgress(stage="queued", ...)]`. Then `execute_report_job()` runs `orchestrator.run()`, which creates a **new** `stage_history: List[StageProgress] = []` (`orchestrator.py:118`) and a new `started_at = time.time()` (`orchestrator.py:112`), and at the end calls `await report_repository.save(report)` (`app.py:74`).
  - **Impact**: When `report_repository.save(report)` overwrites the document in SQLite or Firestore, the initial `"queued"` entry in `stage_history` is wiped out, and `created_at` changes from the job creation timestamp to `started_at`.
  - **SSE Off-By-One Bug**: During the run, `current.stage_history` in DB has `["queued", "query_planning", "searching", "extracting", "analyzing_specialists", "verifying_and_synthesizing", "completed"]` (length **7**). Immediately after `_emit("completed")` finishes, `await report_repository.save(report)` overwrites `stage_history` with `orchestrator`'s list of **6** items (missing `"queued"`). If the SSE loop polls right after `save(report)`, `sent_count` is already `6`, and now `len(current.stage_history)` is also `6`! As a result, `while sent_count < len(current.stage_history):` does **not** execute for `"completed"`, skipping the final `stage="completed"` SSE event before emitting `event: complete`!
  - **Recommendation**: Preserve the existing report's `created_at` and prepend the initial `"queued"` stage (or have `save()` preserve persisted `created_at` and merge `stage_history`), so `stage_history` is strictly append-only and never shrinks on final `save()`.
- **[P2 — Blocking SQLite Calls on the Event Loop in `SQLiteReportRepository.get()` and `list_reports()`]**:
  `create()`, `save()`, `append_stage_progress()`, and `mark_failed()` acquire `async with self._lock:`, whereas `get()` (`line 107`) and `list_reports()` (`line 117`) run synchronous `sqlite3` queries without acquiring `self._lock`. Acquiring `self._lock` or enabling WAL mode (`PRAGMA journal_mode=WAL;`) in `_init_db()` prevents `sqlite3.OperationalError: database is locked` when SSE polling (`get()`) runs concurrently with rapid `append_stage_progress()` writes.
- **[P2 — Deprecated Firestore `.where()` Positional Arguments Warning]**:
  In `FirestoreReportRepository.list_reports()` (`line 281`), `query.where("owner_uid", "==", owner_uid)` uses positional arguments, which triggers a `UserWarning: Detected filter using positional arguments` in `google-cloud-firestore >= 2.11`.
  - **Recommendation**: Use `from google.cloud.firestore_v1.base_query import FieldFilter` and `query.where(filter=FieldFilter("owner_uid", "==", owner_uid))`.

### 4.2 `firestore.rules`, `firestore.indexes.json` & `firebase.json` — Firebase Security & Emulator Config
**Strengths:**
- `firestore.rules` is well-documented and secure by default: denies all client writes (`allow write: if false;`), denies unauthenticated reads, and restricts client reads on `reports/{reportId}` strictly to `resource.data.owner_uid is string && request.auth.uid == resource.data.owner_uid`, ensuring unauthenticated MVP reports (`owner_uid == null`) are never leaked to direct Firestore client queries.
- `firestore.indexes.json` defines the exact composite index (`owner_uid ASC, created_at DESC`) needed by `FirestoreReportRepository.list_reports(owner_uid=...)`.

**Review Comments:**
- **[P2 — Git Hygiene for Firebase Emulator & `src/.env`]**:
  1. Running the Firebase Emulator Suite (`firebase.json`) generates `firebase-debug.log`, `firestore-debug.log`, `ui-debug.log`, and `.firebase/` in the project root. Add these to `.gitignore`.
  2. Remove or ensure `src/.env` is ignored and not tracked.

### 4.3 `src/core/dispatcher.py` — `JobDispatcher` (`LocalAsyncDispatcher` & `CloudTasksDispatcher`)
**Strengths:**
- `LocalAsyncDispatcher` stores tasks in `self._active_tasks: Set[asyncio.Task[None]]` and cleans them up via `task.add_done_callback(self._active_tasks.discard)`, preventing premature garbage collection of in-flight tasks.
- `CloudTasksDispatcher` wraps the synchronous `tasks_v2.CloudTasksClient` call in `asyncio.to_thread(_enqueue)`.

**Review Comments:**
- **[P2 — Reuse `CloudTasksClient` Instead of Re-Instantiating Per Request]**:
  In `CloudTasksDispatcher.dispatch()` (`lines 87–89`), `client = tasks_v2.CloudTasksClient()` is instantiated inside `_enqueue()` on every single dispatch call. Lazily initialize `self._client` once on `CloudTasksDispatcher` and reuse it.
- **[P2 — OIDC Token `audience` Parameter for Cloud Run]**:
  In `CloudTasksDispatcher.dispatch()` (`lines 79–83`), `http_request["oidc_token"] = {"service_account_email": self.service_account_email}` omits `"audience"`. Set `"audience": self.worker_base_url` so Cloud Run IAM invoker verification matches the service base URL.

### 4.4 `src/api/app.py` — Canonical Report Endpoints, Worker Target & Multi-Instance SSE
**Strengths:**
- Clean RESTful lifecycle (`POST /api/v1/reports` -> `202 Accepted` with `stream_url`, `GET /api/v1/reports`, `GET /api/v1/reports/{report_id}`, `GET /api/v1/reports/{report_id}/stream`).
- SSE streaming (`stream_report_progress`) is backed by `ReportRepository` and sets `Cache-Control: no-cache` and `X-Accel-Buffering: no`.

**Review Comments:**
- **[P2 — SSE Stream Maximum Timeout / Client Disconnect Check]**:
  In `stream_report_progress()` (`lines 211–233`), `while True:` polls `report_repository.get(report_id)` indefinitely if a worker crashes hard (e.g., OOM kill on Cloud Run before `mark_failed()` can run). Add a bounded `max_duration_seconds: float = 300.0` or check `await request.is_disconnected()`.
- **[P2 — Callback Webhook Failure Should Not Mark Completed Report as `"failed"`]**:
  In `execute_report_job()` (`lines 74–82`), `await client.post(payload.callback_url, ...)` is inside the same `try` block after `await report_repository.save(report)`. If the external `callback_url` fails or times out, the `except` block calls `await report_repository.mark_failed(report_id, str(exc))`, overwriting a completed report with `status="failed"`. Wrap the `callback_url` POST in its own `try/except` block.
- **[P3 — Legacy `src/core/storage.py` Unclosed Connection Warning in Tests]**:
  Wrap `self._get_connection()` with `contextlib.closing` in legacy `src/core/storage.py` to silence the `ResourceWarning: unclosed database` seen during `unittest` runs.

---

## 5. Phase 4 Review: Cloud Run, Firebase App Hosting & Structured UI Upgrade

### 5.1 `Dockerfile`, `.dockerignore`, `ui/apphosting.yaml` & `firebase.json` — Cloud Run & App Hosting Configs
**Strengths:**
- `Dockerfile` uses `python:3.12-slim`, copies `uv`, installs `".[gcp]"`, runs as a non-root `appuser`, defaults `REPORT_STORAGE_BACKEND=firestore`, and binds to `${PORT:-8080}`.
- `.dockerignore` cleanly excludes `.git`, `.venv`, `.env*`, `src/.env`, `storage/`, `tests/`, and `ui/`.
- `ui/apphosting.yaml` and `firebase.json` configure Firebase App Hosting (`backendId: "carthij-ui"`, `rootDir: "ui"`) with sensible resource limits (`cpu: 1`, `memoryMiB: 512`, `minInstances: 0`, `maxInstances: 10`).

**Review Comments:**
- **[P1 — `Dockerfile` Layer Caching & Package Build Failure (`COPY pyproject.toml` Before `COPY src`)]**:
  In `Dockerfile` (`lines 14–17`):
  ```dockerfile
  COPY pyproject.toml ./
  RUN uv pip install --system --no-cache ".[gcp]"
  COPY src ./src
  ```
  Because `pyproject.toml` specifies `[build-system] build-backend = "hatchling.build"` and `[tool.hatch.build.targets.wheel] packages = ["src"]`, running `uv pip install --system --no-cache ".[gcp]"` **before** `COPY src ./src` will fail during `docker build` because `hatchling` cannot find the `src/` directory when building the local wheel `.`! (Or if `README.md` is missing, `hatchling` also fails because `readme = "README.md"` is declared in `pyproject.toml:5`).
  - **Recommendation**: Either:
    1. Copy `pyproject.toml`, `README.md`, and `src ./src` before `RUN uv pip install --system --no-cache ".[gcp]"`, **or**
    2. Use `uv.lock` for two-stage caching (`COPY pyproject.toml uv.lock README.md ./` + `COPY src ./src` -> `RUN uv pip install --system --no-cache ".[gcp]"`).

### 5.2 `ui/app/reports/[id]/page.tsx` — Live SSE Progress & Report Detail View
**Strengths:**
- Cleanly fetches the initial `FundingReport`, and if `status` is `"queued"` or `"in_progress"`, subscribes to `EventSource(/api/v1/reports/${id}/stream)` for real-time `stage`, `complete`, and `error` events.
- Renders a 5-stage progress stepper (`query_planning`, `searching`, `extracting`, `analyzing_specialists`, `verifying_and_synthesizing`) and automatically transitions to `ResultsPanel` when `complete` fires.

**Review Comments:**
- **[P1 — Native `EventSource` `"error"` Event Prematurely Closes Stream on Transient Reconnects]**:
  In `ui/app/reports/[id]/page.tsx` (`lines 91–94`):
  ```tsx
  eventSource.addEventListener("error", () => {
    eventSource?.close();
    fetchReport();
  });
  ```
  In the browser's `EventSource` API, the `"error"` event listener triggers on **both**:
  1. A custom server-sent `event: error\ndata: ...\n\n` frame from FastAPI when `current.status === "failed"`, **and**
  2. Any transient network blip or connection reset while the pipeline is still `"in_progress"`.
  If a transient network blip triggers `"error"` while the report is still `"in_progress"`, `eventSource.close()` permanently kills the SSE stream, and `fetchReport()` returns the report still in `status: "in_progress"`. Because the `useEffect` (`line 61`) only runs on `[id]`, the page will now sit stuck on the `"Multi-Agent Pipeline Running"` spinner forever with no active `EventSource` and no polling fallback!
  - **Recommendation**:
    1. Check `if ((evt as MessageEvent).data)` inside the `"error"` listener to distinguish an explicit server-sent `event: error` payload from a browser-level connection drop, **or**
    2. Add a lightweight fallback `setInterval(fetchReport, 4000)` while `report.status === "queued" || report.status === "in_progress"` if `eventSource.readyState === EventSource.CLOSED`, so the UI always converges to the final report even if SSE disconnects mid-run.
- **[P2 — `handleCopyLink` Clipboard Error Handling]**:
  In `handleCopyLink()` (`line 107`), `navigator.clipboard.writeText(window.location.href)` returns a Promise that can reject in non-HTTPS or restricted iframe contexts. Add `.catch(() => {})` or `await` with `try/catch` before setting `setIsCopied(true)`.

### 5.3 `ui/components/IntakeForm.tsx` & `ui/app/page.tsx` — Intake Form & Landing Page
**Strengths:**
- `IntakeForm.tsx` now posts directly to `POST /api/v1/reports` and redirects immediately on `202 Accepted` to `/reports/${data.report_id}` for live SSE streaming.
- Accessible form markup (`useId`, `aria-labelledby`, `fieldset`/`legend`, keyboard validation focus).

**Review Comments:**
- **[P3 — Misleading Copy in `IntakeForm.tsx` & `ui/app/page.tsx`]**:
  1. In `IntakeForm.tsx` (`lines 326–357`), the checkbox label still says *"Async Background Webhook Dispatch — Deliver results to an external callback URL instead of waiting synchronously"*. Since `POST /api/v1/reports` is now **always** asynchronous (`202 Accepted` + live SSE stream), consider renaming this toggle to *"Optional Webhook Callback Notification — Also POST completed report JSON to an external webhook URL"* so users aren't confused into thinking unchecked means a blocking HTTP request.
  2. In `ui/app/page.tsx` (`lines 41–55`), the *"How the Agentic Workflow Works"* cards still reference the legacy `/api/v1/webhook/submit` endpoint and dynamic coordinator decomposition. Updating the copy to mention `/api/v1/reports` + Hybrid Tavily Retrieval + 4 Opinionated Specialists + Citation Grounding will match the upgraded architecture.

### 5.4 `ui/components/ResultsPanel.tsx` & `ui/app/reports/page.tsx` — Results & Dashboard
**Strengths:**
- `ResultsPanel.tsx` renders all canonical `FundingReport` sections: `readiness_score` badge (`0–100`), scope-filtered `opportunities` with `[SRC-N]` badges and external links, `readiness_gaps` with severity badges, `action_plan` with 7d/30d/90d timeframes, `specialist_outputs`, and `sources` with `Full Extract` indicators.
- `ui/app/reports/page.tsx` cleanly handles both `"queued"` and `"in_progress"` states via `isRunning(report.status)` and displays the `readiness_score` badge and verified opportunity count on completed cards.

**Review Comments:**
- **[P2 — External Link Protocol Validation (`source_url` / `src.url`)]**:
  In `ResultsPanel.tsx` (`lines 183` and `328`), `href={opp.source_url}` and `href={src.url}` render anchor tags directly from LLM/search output. Although `verify_and_ground_opportunities()` verifies URLs against `RetrievedSourceRegistry`, adding a quick helper `const safeHref = (url: string) => (url.startsWith("https://") || url.startsWith("http://") ? url : "#")` in `ResultsPanel.tsx` is a good defense-in-depth measure against `javascript:` or malformed relative URLs.
- **[P3 — Currency Formatting on `target_amount_usd`]**:
  In `ui/app/reports/page.tsx` (`line 248`) and `ui/app/reports/[id]/page.tsx` (`line 210`), `${fundingAmount}` prepends a `$` directly to the user's raw string. If the user typed `$500,000` in the form input, it renders as `$$500,000`. Stripping a leading `$` (`fundingAmount.replace(/^\$/, "")`) avoids double dollar signs.

---

## 6. Complete Summary Priority Matrix (Phases 1–4)

| Priority | Phase | Component | Issue | Recommended Fix |
| :--- | :--- | :--- | :--- | :--- |
| **P1** | Phase 4 | `Dockerfile` | `RUN uv pip install --system --no-cache ".[gcp]"` runs before `COPY src ./src` and without `README.md`, causing `hatchling` wheel build to fail during `docker build` | Copy `pyproject.toml`, `README.md`, and `src ./src` before running `uv pip install` |
| **P1** | Phase 4 | `ui/app/reports/[id]/page.tsx` | `eventSource.addEventListener("error")` closes the SSE stream on transient network blips without a reconnect or polling fallback | Distinguish server `event: error` data from transient disconnects, and add a fallback poll while `isRunning` if SSE closes early |
| **P1** | Phase 3 | `src/api/app.py` / `src/pipeline/orchestrator.py` | `save(report)` overwrites initial `"queued"` stage in `stage_history` and `created_at`, causing SSE `sent_count` off-by-one skip on the final `"completed"` stage event | Preserve `created_at` and initial `"queued"` stage in `stage_history` when saving completed report |
| **P1** | Phase 2 | `src/pipeline/grounding.py` | `source_id` fallback (`SRC-1`) can accidentally validate a hallucinated `source_url` if the LLM guesses `"SRC-1"` | Only fall back to `get_by_id(opp.source_id)` when `opp.source_url` is blank or shares the same domain as the `SRC-N` record |
| **P1** | Phase 2 | `src/core/agent.py` | `self.history` persists on `BaseAgent` instance across multiple `run()` calls | Reset `self.history` per `run()` call (or default `reset_history: bool = True`) |
| **P1** | Phase 1 | `src/pipeline/registry.py` | `register_extract_response()` can create a disconnected `score=0.0` record if `/extract` returns a redirected URL | Map extracted content back to the requested URL record when redirected, and boost `is_extracted` in `list_sources()` |
| **P2** | Phase 4 | `ui/components/ResultsPanel.tsx` | `href={opp.source_url}` / `href={src.url}` rendered without `http(s)://` protocol guard | Ensure URLs start with `https://` or `http://` before rendering in `<a href>` |
| **P2** | Phase 3 | `src/api/app.py` | External `callback_url` POST is inside the main `try` block; webhook failure calls `mark_failed()` on an already-completed report | Isolate `callback_url` POST in its own `try/except` after `report_repository.save(report)` |
| **P2** | Phase 3 | `src/api/app.py` | SSE `while True:` loop has no max timeout if a worker container is OOM-killed before `mark_failed()` | Add `max_duration_seconds` timeout guard in `stream_report_progress()` |
| **P2** | Phase 3 | `src/core/dispatcher.py` | `CloudTasksDispatcher` instantiates a new `CloudTasksClient()` on every dispatch and omits OIDC `audience` | Cache `CloudTasksClient` on the instance and set `"audience": self.worker_base_url` |
| **P2** | Phase 3 | `src/core/repository.py` | `FirestoreReportRepository.list_reports()` uses deprecated positional `.where("owner_uid", "==", ...)` | Use `FieldFilter("owner_uid", "==", owner_uid)` |
| **P2** | Phase 3 | `.gitignore` | Firebase Emulator debug logs (`firebase-debug.log`, `firestore-debug.log`, `.firebase/`) not in `.gitignore` | Add Firebase Emulator log/cache paths to `.gitignore` |
| **P2** | Phase 2 | `src/pipeline/orchestrator.py` | `"searching"` and `"extracting"` SSE events fire back-to-back before `/search` runs; no fallback if `synthesizer` fails | Emit `"extracting"` between `/search` and `/extract`; fail fast if 0 specialists succeed; add fallback summary if only synthesizer fails |
| **P2** | Phase 2 | `src/core/agent.py` | Unchecked `response.candidates[0]` access | Guard against empty `response.candidates` |
| **P2** | Phase 1 | `src/pipeline/registry.py` | `normalize_url()` keeps `utm_*` and unsorted query params | Strip tracking params and sort query parameters |
| **P3** | Phase 4 | `ui/components/IntakeForm.tsx` & `ui/app/page.tsx` | Copy in `Home` and `IntakeForm` still mentions legacy `/api/v1/webhook/submit` and synchronous waiting | Update copy to reflect `/api/v1/reports` + SSE streaming and optional webhook callback |
| **P3** | Phase 3 | `src/core/storage.py` | Legacy `ReportStorage` emits `ResourceWarning: unclosed database` in unit tests | Wrap `sqlite3.connect` with `contextlib.closing` |
| **P3** | Phase 2 | `src/agents/specialists/roster.py` | `STATE_GUIDANCE` only matches exact `"NY"`, `"NJ"`, `"SF"` strings | Normalize full state/city names (`"California"`, `"CA"`, `"New York"`, `"New Jersey"`) |
| **P3** | Phase 2 | `src/pipeline/orchestrator.py` | `readiness_gaps` from 4 specialists are concatenated without deduplication | Deduplicate overlapping gaps across specialists |
