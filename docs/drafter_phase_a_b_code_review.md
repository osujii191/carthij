# Application Drafter — Draft Phases A, B, C & D Code Review

**Date:** 2026-11-21  
**Scope:** Complete review of **Draft Phase A** (Canonical Draft Models, `DraftRepository`, Firestore Rules & Indexes, and Built-In 3-Step Demo Grant Portal), **Draft Phase B** (SSRF Security Guard, Playwright `BrowserDriver`, `FormMapperAgent`, and `BrowserDraftOrchestrator`), **Draft Phase C** (FastAPI Draft Endpoints, Live Screenshot SSE, Revision Loop & Markdown Export), and **Draft Phase D** (Live Browser-Watch UI & Interactive Missing-Inputs Revision) against [application_drafter_plan.md](./application_drafter_plan.md) and [development_log.md](./development_log.md).

---

## 1. Executive Summary

All four phases (`A`, `B`, `C`, and `D`) of the Browser-Use Application Drafter are implemented cleanly and cohesively. The end-to-end lifecycle—from clicking **"Draft & Pre-Fill"** on a verified `FundingOpportunity` card in `ResultsPanel.tsx`, to `POST /api/v1/reports/{report_id}/drafts`, to live `event: browser_step` screenshot streaming over SSE, to human-in-the-loop revision (`POST /api/v1/drafts/{draft_id}/revise`) and Markdown export (`GET /api/v1/drafts/{draft_id}/export`)—is well-structured and covered by unit/integration tests (`test_draft_api_create_stream_revise_and_export`).

Below is a file-by-file review across **Draft Phases A–D**, highlighting **what was done well** and **actionable findings (P1/P2/P3)** to harden the feature.

---

## 2. Draft Phase A Review: Models, Repository, Security Rules & Demo Portal

### 2.1 `src/models/draft.py` — Canonical `ApplicationDraft` & `BrowserStepEvent` Models
**Strengths:**
- `BrowserStepEvent.to_metadata_dict()` strips `screenshot_b64` before appending to `step_log`, while `ApplicationDraft.latest_screenshot_b64` holds only the single latest frame (`<= 100 KB`), protecting Firestore's 1 MiB document limit while still enabling repository-backed SSE frame streaming.
- Clean request/response schemas (`DraftCreateRequest`, `DraftReviseRequest`, `DraftAcceptedResponse`).

### 2.2 `src/core/draft_repository.py` — `DraftRepository` (`SQLiteDraftRepository` & `FirestoreDraftRepository`)
**Strengths:**
- Full protocol parity between `SQLiteDraftRepository` (WAL mode + `closing()` + `asyncio.Lock`) and `FirestoreDraftRepository` (`FieldFilter` + `firestore.ArrayUnion` on `step_log` + atomic overwrite on `latest_screenshot_b64`).
- Tested in `test_sqlite_draft_repository_single_thumbnail_and_step_log`.

**Review Comments:**
- **[P2 — `SQLiteDraftRepository.get()` and `list_by_report()` Lock Consistency]**:
  `get()` (`line 101`) and `list_by_report()` (`line 111`) read without `async with self._lock:`. Since WAL mode is enabled (`line 61`), concurrent reads are generally safe, but acquiring `async with self._lock:` (or setting `timeout=10.0` on `sqlite3.connect`) guarantees zero `database is locked` errors during high-frequency 750ms screenshot SSE polling.

### 2.3 `firestore.rules` & `firestore.indexes.json` — Security Rules & Composite Indexes
**Strengths:**
- `firestore.rules` (`lines 231–297` & `317–329`) defines a thorough `isValidApplicationDraft(data)` validator (including `validStringLength(data.latest_screenshot_b64, 0, 150000)` to enforce the thumbnail size cap at the schema level), denies all client writes (`allow write: if false;`), and restricts client reads via `isDraftOwner()`.
- `firestore.indexes.json` defines both `(report_id ASC, created_at DESC)` and `(owner_uid ASC, created_at DESC)`.

### 2.4 `ui/app/demo-portal/page.tsx` — Built-In 3-Step Demo Grant Portal
**Strengths:**
- Every input has a deterministic `id` and `name` (`#org_legal_name`, `#org_type`, `#jurisdiction`, `#contact_email`, `#federal_ein`, `#sam_uei`, `#target_program_name`, `#requested_amount_usd`, `#project_title`, `#project_abstract`, `#budget_breakdown`, `#attest_eligibility`, `#attest_non_lobbying`, `#next_step_btn`, `#review_step_btn`, `#save_draft_btn`, `#final_submit_btn`).
- Step 1 explicitly allows clicking `#next_step_btn` when `#federal_ein` and `#sam_uei` are left blank by the agent on the first pass, surfacing them in the amber warning box on Step 3 (`missingForFinalSubmit`) and disabling `#final_submit_btn` until all required fields are present.
- Wrapped in `<Suspense>` so `useSearchParams()` builds cleanly in Next.js 15 static/SSR prerendering.

---

## 3. Draft Phase B Review: SSRF Guard, Playwright Driver, Mapper & Orchestrator

### 3.1 `src/drafter/security.py` — SSRF & Portal URL Guard
**Strengths:**
- Blocks non-HTTP(S) schemes (`file://`, `javascript:`, `ftp://`), cloud metadata hosts (`169.254.169.254`, `metadata.google.internal`), `.internal` domains, and private/loopback/link-local IPs while explicitly whitelisting the configured `DEMO_PORTAL_BASE_URL` (`127.0.0.1:3000` / `localhost:3000`).

**Review Comments:**
- **[P1 — `ip.is_unspecified` (`0.0.0.0`) & Post-Redirect SSRF Validation in Playwright]**:
  1. In `validate_portal_url()` (`line 73`), add `or ip.is_unspecified` so `http://0.0.0.0:8000` is blocked alongside `127.0.0.1`.
  2. In `PlaywrightBrowserDriver.open_session()` (`browser.py:226`), after `await page.goto(url, ...)`, call `validate_portal_url(page.url)` so an HTTP `302 Redirect` from a public domain to an internal IP (`http://169.254.169.254/...`) is caught immediately before DOM inspection or screenshot capture.

### 3.2 `src/drafter/browser.py` — `PlaywrightBrowserDriver` & `SimulatedBrowserDriver`
**Strengths:**
- `PlaywrightBrowserDriver` shares a single Chromium instance behind `self._init_lock` and bounds concurrent tabs via `self._semaphore = asyncio.Semaphore(2)`, releasing the semaphore reliably in `_PlaywrightSession.close()`.
- `_DOM_INSPECT_JS` detects visible password fields and CAPTCHA iframes (`recaptcha`, `hcaptcha`, `turnstile`), extracts `<label for="...">` via `CSS.escape(el.id)`, and classifies buttons into `next`, `save_draft`, `submit`, and `other`.

**Review Comments:**
- **[P1 — Graceful Fallback When Playwright Is Installed Without Chromium Binary or Local `/demo-portal` Is Offline]**:
  In `get_browser_driver()` (`lines 337–346`), it checks `import playwright` and returns `PlaywrightBrowserDriver()`. If the `playwright` package is installed in `.venv` without `playwright install chromium` having been run (or if `open_session` tries to connect to `http://127.0.0.1:3000/demo-portal` when the Next.js server isn't running on port 3000), `open_session()` raises at runtime.
  - **Recommendation**: In `PlaywrightBrowserDriver.open_session()` (`lines 220–230`), catch browser launch / connection errors, release `self._semaphore`, log a warning, and fall back to `_SimulatedSession(url=url)` (unless `CARTHIJ_BROWSER_STRICT=true`). That guarantees the live demo and API never fail even if Chromium binaries or port 3000 are unavailable.
- **[P2 — `wait_until="networkidle"` Timeout on Next.js Dev Server]**:
  In `PlaywrightBrowserDriver.open_session()` (`line 226`), `await page.goto(url, wait_until="networkidle", timeout=30000)` can hang for 30 seconds against a Next.js `npm run dev` server because Next.js keeps a persistent HMR WebSocket / event stream open.
  - **Recommendation**: Change `wait_until="networkidle"` to `wait_until="domcontentloaded"` followed by `await page.wait_for_timeout(400)`.

### 3.3 `src/drafter/mapper.py` & `src/drafter/orchestrator.py` — Grounded Mapping & Multi-Step Loop
**Strengths:**
- Clean multi-step wizard loop (`for wizard_page in range(1, self.max_wizard_steps + 1):`) with immediate halts on login/CAPTCHA (`awaiting_human_action`) and Review screen (`ready_for_review`).

**Review Comments:**
- **[P1 — Programmatic Enforcement of `user_supplied_facts` Key Matching & Anti-Fabrication in `FormMapperAgent` / `BrowserDraftOrchestrator`]**:
  Look closely at how `DraftWorkspacePage` (`ui/app/reports/[id]/drafts/[draftId]/page.tsx:430-460`) submits user revisions vs. how `FormMapperAgent` receives them:
  1. In `orchestrator.py` (`lines 187–192`), when `#federal_ein` (labeled `"Federal EIN (Tax ID) (Required before final submit)"`) is skipped on pass 1, its `field_key` is generated via `_field_key(action.field_label)`:
     `field_key = "federal_ein_tax_id_required_before_final_submit"`.
  2. In the UI (`DraftWorkspacePage`), the user types `"12-3456789"` into that input, which sends:
     `POST /api/v1/drafts/{id}/revise` with `{"user_supplied_facts": {"federal_ein_tax_id_required_before_final_submit": "12-3456789"}}`.
  3. On pass 2 (`revise`), `BrowserDraftOrchestrator` calls `self.mapper.plan_fill(...)`.
     - If `FormMapperAgent` is backed by a live LLM, it sees `user_supplied_facts`, **but** if the LLM fails to match the long key `"federal_ein_tax_id_required_before_final_submit"` to `#federal_ein` (or if in Pass 1 the LLM hallucinated `"00-0000000"` instead of returning `skip_missing`), the field won't behave deterministically!
     - Furthermore, in `orchestrator.py` (`line 187`), if the LLM slightly changes `action.field_label` on Pass 1 (e.g., `"Federal EIN"` instead of `"Federal EIN (Tax ID) (Required before final submit)"`), `_field_key(action.field_label)` won't match `_field_key(schema_field.label)`!
  - **Recommendation**: In `BrowserDraftOrchestrator.run()` (`lines 178–216`), right after `fill_plan = await self.mapper.plan_fill(...)`:
    1. Build a lookup of `schema_field_by_selector = {f.selector: f for f in schema.fields}` and use the **canonical `schema_field.label`** (and `selector.lstrip("#")`) when computing `field_key`.
    2. Before executing each `action`, check if `user_supplied_facts` contains a non-empty value matching `action.selector`, `action.selector.lstrip("#")`, or `_field_key(schema_field.label)`. If so, **deterministically override** `action.action_type = "fill"` and `action.value = supplied_val`!
    3. Conversely, if `schema_field` is a sensitive compliance ID (`ein`, `tax_id`, `uei`, `sam_uei`, `duns`) and is **not** present in `user_supplied_facts` or `report.intake_data`, **deterministically force** `action.action_type = "skip_missing"` and `action.value = None` so the LLM can never hallucinate a fake EIN or UEI on Pass 1!
- **[P2 — Unchanged Wizard Page Loop Guard]**:
  In `orchestrator.py` (`line 103`), track `seen_page_signatures`. If clicking `next_btn` fails to advance the page (because a required field failed client-side validation) and `inspect_form()` returns the exact same `(schema.page_url, schema.heading, tuple(f.selector for f in schema.fields))`, break out of the wizard loop and pause with `status="ready_for_review"` instead of re-filling the same page 4 times.

---

## 4. Draft Phase C Review: FastAPI Endpoints, Live Screenshot SSE, Revision & Export

### 4.1 `src/api/app.py` (`lines 313–620`) — Draft API Lifecycle
**Strengths:**
- `POST /api/v1/reports/{report_id}/drafts` validates `target_portal_url` via `validate_portal_url()` (returning `400 Bad Request` on SSRF attempts), persists the `queued` `ApplicationDraft`, and spawns the async worker with strong task references (`_active_draft_tasks`).
- `POST /api/v1/drafts/{draft_id}/revise` properly merges `merged_facts = {**draft.user_supplied_facts, **req.user_supplied_facts}`, increments `draft.version`, and **clears `draft.step_log = []`, `draft.filled_fields = []`, and `draft.missing_inputs = []` in the DB before spawning the worker** (`lines 552–555`), which cleanly avoids the SSE `sent_steps` offset bug on revision!
- `GET /api/v1/drafts/{draft_id}/export` formats a clean Markdown table + checklist + narrative with `Content-Disposition: attachment` and escapes pipe characters (`replace("|", "\\|")`).

**Review Comments:**
- **[P2 — Cloud Tasks Parity for Draft Jobs (`/internal/worker/execute` or `DraftJobDispatcher`)]**:
  In `src/api/app.py` (`lines 317–320`), `create_application_draft` and `revise_application_draft` use `_spawn_draft_task(...)` (in-process `asyncio.create_task`), whereas report jobs use `job_dispatcher.dispatch(...)`. On Cloud Run with CPU-only-during-request allocation (default Cloud Run CPU throttling), background `asyncio.create_task` threads can be throttled after the `202 Accepted` HTTP response finishes unless Cloud Run is configured with `--no-cpu-throttling` (CPU always allocated) or dispatched via Cloud Tasks.
  - **Recommendation**: Document `--no-cpu-throttling` (or `cpu-throttling: false`) in the Cloud Run deployment notes, or add an internal `/internal/worker/execute-draft` route for Cloud Tasks parity.
- **[P2 — Clearing `latest_screenshot_b64` on `revise` Reset (`app.py:552`)]**:
  In `revise_application_draft()` (`lines 548–555`), `draft.step_log = []`, `draft.filled_fields = []`, and `draft.missing_inputs = []` are cleared before `await draft_repository.save(draft)`, while `draft.latest_screenshot_b64` still holds the final Review screen screenshot from `v1`.
  - **Impact**: On the first SSE poll of `v2` (`app.py:488–494`), if `step 1`'s screenshot hasn't overwritten `latest_screenshot_b64` yet (e.g., if a step had `screenshot_b64=None`), it could briefly attach `v1`'s final screenshot. Setting `draft.latest_screenshot_b64 = None` alongside `draft.step_log = []` in `revise_application_draft()` ensures `v2` starts with a clean slate.

---

## 5. Draft Phase D Review: Live Browser-Watch UI & Opportunity Card Trigger

### 5.1 `ui/components/ResultsPanel.tsx` — Opportunity Card "Draft & Pre-Fill" Trigger
**Strengths:**
- Every verified `FundingOpportunity` card now includes a **"Draft & Pre-Fill"** button with per-card loading spinner (`draftingOpp === opp.name`), error banner (`draftError`), and automatic redirect to `/reports/${report.report_id}/drafts/${accepted.draft_id}`.
- Uses `safeHref()` (`lines 37–40`) on both `opp.source_url` and `src.url`.

### 5.2 `ui/app/reports/[id]/drafts/[draftId]/page.tsx` — Live Browser-Watch Workspace
**Strengths:**
- Excellent split-pane layout: macOS-style browser window chrome displaying the live `liveScreenshot` (`<img>`), real-time step-by-step action log (`#1`, `#2`, ... with status badges), interactive **Missing User Inputs** form wired to `POST /api/v1/drafts/{draftId}/revise`, **Pre-Filled Fields** audit list, and **Drafted Narrative Copy** with one-click copy and **Export Markdown** download.
- Handles `EventSource` `"complete"`, `"timeout"`, and `"error"` (distinguishing server-sent `event: error` frames from transient disconnects and starting a `3000ms` fallback poll when `EventSource.CLOSED`).
- Uses `streamGeneration` state counter (`line 43` & `171`) to cleanly re-subscribe the `useEffect` SSE stream after the user clicks **"Re-Fill Portal with My Inputs"**.

**Review Comments:**
- **[P1 — Race Condition in `DraftWorkspacePage` When Clicking "Re-Fill Portal with My Inputs" (`handleRevise`)]**:
  Look at `handleRevise` (`lines 170–171`) and `fetchDraft` (`lines 53–55`) in `ui/app/reports/[id]/drafts/[draftId]/page.tsx`:
  1. When `v1` finishes, `draft.status` is `"ready_for_review"`, and `isRunning` (`line 223`) is `false`.
  2. The user fills in `missingAnswers` and clicks **"Re-Fill Portal with My Inputs"**.
  3. `handleRevise` POSTs to `/api/v1/drafts/${draftId}/revise` (which returns `202 Accepted` with `status: "queued"`), clears `setLiveSteps([])`, and increments `setStreamGeneration((g) => g + 1)`.
  4. **Catch 1 (Optimistic UI State)**: `handleRevise` does **not** optimistically update `draft.status` to `"queued"` before `init()` runs `fetchDraft()`. More importantly, in `SimulatedBrowserDriver` mode (or fast local runs), the background worker spawned by `/revise` is so fast (`< 5ms`) that by the time `init()` calls `await fetchDraft()` (`line 98`), `data.status` is **already `"ready_for_review"`** (`v2` finished)!
     - When that happens, `fetchDraft()` sets `setLiveSteps(data.step_log)` (`line 54`) and `setLiveScreenshot(data.latest_screenshot_b64)` (`line 55`), which works!
     - **However**, look at `missingAnswers` in `fetchDraft()` (`lines 56–64`): once `v2` finishes with `data.missing_inputs = []`, `draft.missing_inputs.length === 0` (`line 420`) switches to the green `"All portal fields were grounded and pre-filled with zero missing compliance inputs"` banner — which is great!
  5. **Catch 2 (When `data.step_log` Is Empty on Queue Reset)**: In `fetchDraft()` (`line 54`):
     ```tsx
     if (data.step_log?.length) setLiveSteps(data.step_log);
     ```
     Because `if (data.step_log?.length)` only updates `liveSteps` when `data.step_log` is non-empty, if `fetchDraft()` runs right after `/revise` reset `step_log = []` on the backend, that's fine because `handleRevise` called `setLiveSteps([])`. **But** if `eventSource` receives `browser_step` events (`1..16`) during `v2`, and then `"complete"` fires (`line 119`) and calls `fetchDraft()`, `data.step_log` from `GET /api/v1/drafts/{id}` has `BrowserStepEvent` metadata objects (without `screenshot_b64`), overwriting `liveSteps` cleanly while `liveScreenshot` retains `data.latest_screenshot_b64`.
- **[P2 — Image `src` Security Guard on `liveScreenshot` (`page.tsx:358`)]**:
  In `DraftWorkspacePage` (`line 358`), `<img src={liveScreenshot} ... />` renders `liveScreenshot`. Add a quick helper `const safeImageSrc = (src: string | null) => (src && (src.startsWith("data:image/jpeg;base64,") || src.startsWith("data:image/png;base64,") || src.startsWith("data:image/svg+xml;base64,")) ? src : null)` so only valid base64 image data URIs are rendered in `<img src>`.
- **[P3 — Link Back to Existing Drafts from `ReportDetailPage` (`/reports/[id]`)]**:
  Right now, `ResultsPanel.tsx` has a **"Draft & Pre-Fill"** button that creates a *new* draft every time it is clicked, and `GET /api/v1/reports/{report_id}/drafts` (`app.py:434`) is implemented on the backend, but nothing in the UI calls `GET /api/v1/reports/{report_id}/drafts` yet!
  - **Recommendation**: In `ResultsPanel.tsx`, fetch existing drafts for the report (`GET /api/v1/reports/${report.report_id}/drafts`) on mount or show a small **"Previous Application Drafts"** bar (or change the button to **"View Draft (v2)"** / **"New Draft"**) so users returning to `/reports/[id]` can jump right back into their existing pre-filled drafts without re-running the browser agent from scratch.

---

## 6. Complete Summary Priority Matrix (Draft Phases A–D)

| Priority | Phase | Component | Issue | Recommended Fix |
| :--- | :--- | :--- | :--- | :--- |
| **P1** | Phase B/C/D | `src/drafter/orchestrator.py` & `mapper.py` | `user_supplied_facts` from the Phase D UI are keyed by `_field_key(label)` (e.g. `federal_ein_tax_id_...`), which relies on the LLM to match back to `#federal_ein`; and Pass 1 anti-fabrication relies solely on the LLM prompt | Add deterministic post-processing in `BrowserDraftOrchestrator` / `FormMapperAgent`: match `user_supplied_facts` by `selector`, `selector.lstrip("#")`, or `_field_key(schema_field.label)`, and programmatically force `skip_missing` on `EIN`/`UEI`/`DUNS` when absent from `ApplicantFactLedger` |
| **P1** | Phase B | `src/drafter/browser.py` | If `playwright` package is installed without Chromium binaries or `/demo-portal` server isn't running on port 3000, `open_session()` raises at runtime | Catch browser launch / connection errors in `PlaywrightBrowserDriver.open_session()` and fall back cleanly to `_SimulatedSession` (unless `CARTHIJ_BROWSER_STRICT=true`) |
| **P1** | Phase B | `src/drafter/security.py` & `browser.py` | `validate_portal_url()` doesn't check `ip.is_unspecified` (`0.0.0.0`) or re-validate `page.url` after `page.goto()` redirects | Add `ip.is_unspecified` check in `validate_portal_url()` and re-validate `page.url` after `page.goto()` |
| **P2** | Phase D | `ui/app/reports/[id]/drafts/[draftId]/page.tsx` | `<img src={liveScreenshot}>` does not validate `data:image/...;base64,` prefix | Guard `liveScreenshot` with `safeImageSrc()` allowing only `data:image/(jpeg\|png\|svg+xml);base64,` |
| **P2** | Phase C | `src/api/app.py` | `revise_application_draft()` resets `step_log = []` but leaves `v1`'s `latest_screenshot_b64` in place | Also reset `draft.latest_screenshot_b64 = None` when resetting `step_log = []` on `revise` |
| **P2** | Phase B | `src/drafter/browser.py` | `page.goto(..., wait_until="networkidle")` can time out (30s) against Next.js `npm run dev` HMR WebSockets | Use `wait_until="domcontentloaded"` + short 400ms settle delay |
| **P2** | Phase B | `src/drafter/orchestrator.py` | Clicking `next_btn` when client-side validation blocks navigation causes the loop to re-fill the same page `max_wizard_steps` times | Detect unchanged form signature `(heading, field_selectors)` after clicking `next_btn` and pause cleanly |
| **P2** | Phase A | `src/core/draft_repository.py` | `SQLiteDraftRepository.get()` and `list_by_report()` read without `self._lock` | Acquire `async with self._lock:` on SQLite reads for high-frequency SSE polling safety |
| **P3** | Phase D | `ui/components/ResultsPanel.tsx` | `GET /api/v1/reports/{report_id}/drafts` exists on backend but UI doesn't list/link existing drafts for a report | Fetch existing drafts in `ResultsPanel.tsx` so users can reopen existing drafts (`v1`/`v2`) directly from the report page |
