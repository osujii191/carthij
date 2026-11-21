# Application Drafter — Draft Phase A & Draft Phase B Code Review

**Date:** 2026-11-21  
**Scope:** Review of **Draft Phase A** (Canonical Draft Models, `DraftRepository`, Firestore Rules & Indexes, and Built-In 3-Step Demo Grant Portal) and **Draft Phase B** (SSRF Security Guard, Playwright `BrowserDriver`, `FormMapperAgent`, and `BrowserDraftOrchestrator`) against [application_drafter_plan.md](./application_drafter_plan.md) and [development_log.md](./development_log.md).

---

## 1. Executive Summary

Draft Phase A and Draft Phase B are cleanly architected and already incorporate the 6 design refinements we discussed during planning (single `latest_screenshot_b64` thumbnail in `DraftRepository`, non-blocking Step 1 in `/demo-portal`, SSRF guard, `BrowserDriver` protocol with `SimulatedBrowserDriver` fallback, and `asyncio.Semaphore(2)` browser context pooling). All 25 unit + benchmark tests pass cleanly.

Below is a file-by-file review covering **strengths** and **actionable findings (P1/P2/P3)** to address as you implement **Draft Phase C** (FastAPI Endpoints & Live Screenshot SSE) and **Draft Phase D** (Live Browser-Watch UI).

---

## 2. Draft Phase A Review: Models, Repository, Security Rules & Demo Portal

### 2.1 `src/models/draft.py` — Canonical `ApplicationDraft` & `BrowserStepEvent` Models
**Strengths:**
- `BrowserStepEvent.to_metadata_dict()` strips `screenshot_b64` before appending to `step_log`, while `ApplicationDraft.latest_screenshot_b64` holds only the single latest frame (`<= 100 KB`), protecting Firestore's 1 MiB document limit while still enabling repository-backed SSE frame streaming.
- Clean request/response schemas (`DraftCreateRequest`, `DraftReviseRequest`, `DraftAcceptedResponse`) ready for Phase C API wiring.

**Review Comments:**
- **[P3 — Preserve `user_answer` on `MissingInputField` During Re-Fill (`revise`)]**:
  `MissingInputField` (`lines 57–61`) defines `user_answer: Optional[str] = None`, which is great for UI state binding. When Phase C implements `POST /api/v1/drafts/{draft_id}/revise`, make sure `user_supplied_facts` merges with any existing `draft.user_supplied_facts` (`{**draft.user_supplied_facts, **req.user_supplied_facts}`) so previously supplied facts aren't lost across multiple revision passes.

### 2.2 `src/core/draft_repository.py` — `DraftRepository` (`SQLiteDraftRepository` & `FirestoreDraftRepository`)
**Strengths:**
- Full protocol parity between `SQLiteDraftRepository` (WAL mode + `closing()` + `asyncio.Lock`) and `FirestoreDraftRepository` (`FieldFilter` + `firestore.ArrayUnion` on `step_log` + atomic overwrite on `latest_screenshot_b64`).
- Verified in `test_sqlite_draft_repository_single_thumbnail_and_step_log`.

**Review Comments:**
- **[P1 — Duplicate Timestamp Collision in `FirestoreDraftRepository.append_browser_step` (`firestore.ArrayUnion`)]**:
  In `FirestoreDraftRepository.append_browser_step()` (`lines 281–284`), `firestore.ArrayUnion([step.to_metadata_dict()])` is used to append steps. While `step_index` is unique per step inside a single run, when a user calls `POST /api/v1/drafts/{draft_id}/revise` (Phase C), `BrowserDraftOrchestrator.run()` starts `step_counter = 0` (`orchestrator.py:97`) and builds a new `step_log` that overwrites the document on final `save(draft)`.
  - **Impact for Phase C SSE Streaming**: During a `revise` re-run, before `orchestrator.run()` starts, `draft.step_log` in the database still has `N` steps from `version=1` (e.g., 16 steps). As `append_browser_step` appends new steps (`step_index=1, 2, ...`), the database `step_log` grows from `16` $\rightarrow$ `17, 18...`, and then at the very end of `orchestrator.run()`, `await draft_repository.save(draft)` overwrites `step_log` with only the new run's 16 steps! Just like the Phase 3 report SSE bug, if an SSE stream is tracking `sent_count < len(current.step_log)`, resetting `step_log` on `revise` or overwriting it at the end will break `sent_count`!
  - **Recommendation**: When starting a `revise` job in Phase C, reset `step_log = []` and `status = "in_progress"` in the database **before** dispatching the worker (so SSE starts cleanly from `sent_count = 0` for the new version).
- **[P2 — `SQLiteDraftRepository.get()` and `list_by_report()` Lock Consistency]**:
  Just as in `SQLiteReportRepository`, `get()` (`line 101`) and `list_by_report()` (`line 111`) read without `async with self._lock:`. Since WAL mode is enabled (`line 61`), concurrent reads are safe, but wrapping `get()` with `async with self._lock:` (or `busy_timeout=5000`) ensures zero `database is locked` errors during high-frequency 200ms–500ms screenshot SSE polling.

### 2.3 `firestore.rules` & `firestore.indexes.json` — Security Rules & Composite Indexes
**Strengths:**
- `firestore.rules` (`lines 231–297` & `317–329`) defines a thorough `isValidApplicationDraft(data)` validator (including `validStringLength(data.latest_screenshot_b64, 0, 150000)` to enforce the thumbnail size cap at the schema level), denies all client writes (`allow write: if false;`), and restricts client reads via `isDraftOwner()`.
- `firestore.indexes.json` defines both `(report_id ASC, created_at DESC)` and `(owner_uid ASC, created_at DESC)`.

### 2.4 `ui/app/demo-portal/page.tsx` — Built-In 3-Step Demo Grant Portal
**Strengths:**
- Every input has a deterministic `id` and `name` (`#org_legal_name`, `#org_type`, `#jurisdiction`, `#contact_email`, `#federal_ein`, `#sam_uei`, `#target_program_name`, `#requested_amount_usd`, `#project_title`, `#project_abstract`, `#budget_breakdown`, `#attest_eligibility`, `#attest_non_lobbying`, `#next_step_btn`, `#review_step_btn`, `#save_draft_btn`, `#final_submit_btn`).
- Step 1 explicitly allows clicking `#next_step_btn` when `#federal_ein` and `#sam_uei` are left blank by the agent on the first pass, surfacing them in the amber warning box on Step 3 (`missingForFinalSubmit`) and disabling `#final_submit_btn` until all required fields are present.
- Wrapped in `<Suspense>` so `useSearchParams()` builds cleanly in Next.js 15 static/SSR prerendering.

**Review Comments:**
- **[P2 — Syncing `targetProgramName` When `?program=` Query Param Hydrates]**:
  In `DemoPortalContent` (`lines 19–20` & `33`), `const initialProgram = searchParams.get("program") || ...` initializes `useState(initialProgram)`. That works on direct navigation, and since `#target_program_name` is also an editable `<input>` on Step 2 that the agent explicitly fills via `FormMapperAgent`, it will be populated either way.

---

## 3. Draft Phase B Review: SSRF Guard, Playwright Driver, Mapper & Orchestrator

### 3.1 `src/drafter/security.py` — SSRF & Portal URL Guard
**Strengths:**
- Blocks non-HTTP(S) schemes (`file://`, `javascript:`, `ftp://`), cloud metadata hosts (`169.254.169.254`, `metadata.google.internal`), `.internal` domains, and private/loopback/link-local IPs while explicitly whitelisting the configured `DEMO_PORTAL_BASE_URL` (`127.0.0.1:3000` / `localhost:3000`).

**Review Comments:**
- **[P1 — DNS Rebinding / Hostname Resolution Bypass & Redirect SSRF in Playwright]**:
  1. **Hostname Resolution**: In `validate_portal_url()` (`lines 71–78`), `ipaddress.ip_address(host)` checks literal IP strings (like `10.0.0.5`), and `line 68` blocks `localhost` and `.internal`. However, `0.0.0.0`, `[::1]`, `127.0.0.1.nip.io`, or a hostname that resolves via DNS to `127.0.0.1` or `169.254.169.254` is not caught by `ipaddress.ip_address(host)` because `host` is a domain name or `0.0.0.0` (`ipaddress.ip_address("0.0.0.0").is_unspecified` is `True`).
     - **Fix**: Also check `ip.is_unspecified` (`0.0.0.0`, `::`) in `validate_portal_url()`, and resolve `socket.getaddrinfo(host, None)` (for non-demo-portal hosts when running live) or block `0.0.0.0`.
  2. **HTTP Redirect SSRF**: Even if `initial_url` is `https://public-site.example.com/apply`, if that URL responds with a `302 Redirect` to `http://169.254.169.254/latest/meta-data/`, Playwright's `page.goto()` will follow the redirect!
     - **Fix**: After `await page.goto(url, ...)` in `PlaywrightBrowserDriver.open_session()` (`browser.py:226`), validate the post-navigation `page.url` with `validate_portal_url(page.url)` before inspecting or capturing screenshots!

### 3.2 `src/drafter/browser.py` — `PlaywrightBrowserDriver` & `SimulatedBrowserDriver`
**Strengths:**
- `PlaywrightBrowserDriver` shares a single Chromium instance behind `self._init_lock` and bounds concurrent tabs via `self._semaphore = asyncio.Semaphore(2)`, releasing the semaphore reliably in `_PlaywrightSession.close()`.
- `_DOM_INSPECT_JS` cleanly detects visible password fields and CAPTCHA iframes (`recaptcha`, `hcaptcha`, `turnstile`), extracts `<label for="...">` via `CSS.escape(el.id)`, and classifies buttons into `next`, `save_draft`, `submit`, and `other`.
- Highlights each element with a blue outline (`3px solid #2563eb`) right before filling and captures a compressed JPEG screenshot (`quality=65`).

**Review Comments:**
- **[P1 — Graceful Fallback When Playwright Package Is Installed But Chromium Binary Fails to Launch]**:
  In `get_browser_driver()` (`lines 337–346`), it checks `import playwright` and returns `PlaywrightBrowserDriver()`. However, if the `playwright` Python wheel is installed in `.venv` **without** `playwright install chromium` having been run (or inside a container/sandbox where Chromium cannot launch), `import playwright` succeeds, and then `open_session()` crashes at runtime with `playwright._impl._errors.Error: Executable doesn't exist`!
  - **Recommendation**: In `PlaywrightBrowserDriver.open_session()` (`lines 220–230`), if `await self._ensure_browser()` raises an exception (or if `page.goto` fails when targeting the local `/demo-portal` while Next.js dev server isn't running in unit/API tests), log a warning and automatically fall back to `_SimulatedSession(url=url)` unless `CARTHIJ_BROWSER_STRICT=true` is set! That guarantees the API and live UI demo **never** crash even if Chromium binaries or the local port 3000 server aren't reachable in a test/cloud environment.
- **[P2 — `wait_until="networkidle"` Timeout on Heavy Third-Party Portals]**:
  In `PlaywrightBrowserDriver.open_session()` (`line 226`), `await page.goto(url, wait_until="networkidle", timeout=30000)` waits for `networkidle`. Many modern web apps (including Next.js dev mode with HMR websockets, or public portals with analytics polling) never reach `networkidle` and will time out after 30 seconds!
  - **Recommendation**: Use `wait_until="domcontentloaded"` followed by a short `await page.wait_for_timeout(500)` so pages with active WebSockets/HMR or analytics load in `< 1 second` instead of hanging for 30s.
- **[P2 — Button Classification Order in `_DOM_INSPECT_JS`]**:
  In `_DOM_INSPECT_JS` (`lines 129–133`):
  ```javascript
  if (lower.includes("submit")) kind = "submit";
  else if (lower.includes("save draft")) kind = "save_draft";
  else if (lower.includes("next") || lower.includes("continue")) kind = "next";
  ```
  This order is great because `"Continue to Submit"` or `"Submit Application"` gets classified as `"submit"` (safe hard-stop). However, in `/demo-portal` Step 2 (`ui/app/demo-portal/page.tsx:386`), the button label is `"Continue to Review"`. If a real portal has a button labeled `"Review & Submit"` on Step 2 (which actually navigates to the Review step), `lower.includes("submit")` will classify it as `"submit"` and stop on Step 2, which is also the safe conservative behavior. Consider also matching `lower.includes("review")` in the `"next"` branch (`lower.includes("next") || lower.includes("continue") || lower.includes("review")`) so buttons labeled `"Review Application"` advance to the review screen.

### 3.3 `src/drafter/mapper.py` — `ApplicantFactLedger` & `FormMapperAgent`
**Strengths:**
- Clear `FormFillPlan` and `FieldFillAction` Pydantic schema.
- Strict anti-fabrication prompt rules that explicitly forbid inventing `EIN`, `SAM.gov UEI`, `DUNS`, revenue, or headcount.

**Review Comments:**
- **[P1 — Programmatic Post-Filter on `FormMapperAgent` to Guarantee `user_supplied_facts` Override & Prevent Hallucinated Compliance IDs]**:
  Right now, `FormMapperAgent` relies purely on the LLM prompt (`lines 71–75`) to set `action_type="skip_missing"` for missing compliance IDs (like `#federal_ein` and `#sam_uei`) and to use `user_supplied_facts` when the user provides them during `revise`.
  - **Catch**:
    1. What if the LLM accidentally invents a placeholder string like `"12-3456789"` or `"TBD"` or `"XX-XXXXXXX"` for `#federal_ein` or `#sam_uei` when `user_supplied_facts` does not contain an EIN or UEI?
    2. Conversely, when the user submits `DraftReviseRequest(user_supplied_facts={"federal_ein_tax_id_required_before_final_submit": "12-3456789"})` (keyed by `field_key` from `MissingInputField` or by selector `#federal_ein`), how does `FormMapperAgent` or `BrowserDraftOrchestrator` guarantee that the user's supplied answer is deterministically filled into that field?
  - **Recommendation**: Add a deterministic post-processing pass in `FormMapperAgent.plan_fill()` (or `BrowserDraftOrchestrator.run()`):
    - If a field's selector or `field_key(field.label)` (or `ein` / `uei` / `duns` keyword) matches a non-empty entry in `user_supplied_facts`, **deterministically set** `action_type="fill"` and `value=user_supplied_value`.
    - Conversely, if a field's selector/label is a sensitive compliance identifier (`ein`, `tax id`, `uei`, `sam.gov`, `duns`, `ssn`) and is **not** present in `user_supplied_facts` or `intake_data`, **programmatically force** `action_type="skip_missing"` and `value=None` even if the LLM tried to guess `"TBD"` or `"00-0000000"`! (Just like Stage 3 programmatic citation verification in `grounding.py`, programmatic enforcement of the `ApplicantFactLedger` makes your "Zero Fabricated Facts" guardrail 100% bulletproof).

### 3.4 `src/drafter/orchestrator.py` — `BrowserDraftOrchestrator`
**Strengths:**
- Clean multi-step wizard loop (`for wizard_page in range(1, self.max_wizard_steps + 1):`).
- Checks `schema.auth_or_captcha_detected` first (Safety Gate 1 $\rightarrow$ `status="awaiting_human_action"`), checks `if not schema.fields:` on the Review page (Safety Gate 2 $\rightarrow$ clicks `save_draft` if available and hard-stops with `status="ready_for_review"`), and never clicks `kind == "submit"`.
- Deduplicates `missing_inputs` by `_field_key(action.field_label)` and records accurate `fields_filled` and `fields_skipped_missing` counts in `metadata`.

**Review Comments:**
- **[P2 — Infinite Loop Guard if Clicking `next_btn` Fails to Advance the Wizard Page]**:
  In `orchestrator.py` (`lines 259–276`), if a page has a `next_btn` (`kind == "next"`), the orchestrator clicks it and loops to `wizard_page + 1`. On a real external form, if a required field on Step 1 failed client-side validation when clicking `"Next"`, the browser stays on Step 1! On the next iteration (`wizard_page = 2`), `inspect_form()` will extract the **exact same Step 1 fields** and re-fill Step 1 up to `max_wizard_steps=4` times.
  - **Recommendation**: Track the previous step's `(schema.page_url, schema.heading, tuple(f.selector for f in schema.fields))` signature. If clicking `next_btn` results in the exact same form signature on the next iteration, stop advancing and pause with `status="ready_for_review"` (noting that client-side validation prevented advancing to the next page).

---

## 4. Summary Priority Matrix (Draft Phase A & Draft Phase B)

| Priority | Phase | Component | Issue | Recommended Fix |
| :--- | :--- | :--- | :--- | :--- |
| **P1** | Phase B | `src/drafter/mapper.py` / `orchestrator.py` | Anti-fabrication of `EIN`/`UEI` and application of `user_supplied_facts` during `revise` rely solely on the LLM prompt | Add programmatic enforcement: deterministically apply `user_supplied_facts` by `field_key`/`selector`, and programmatically force `skip_missing` on `EIN`/`UEI`/`DUNS` fields when absent from `ApplicantFactLedger` |
| **P1** | Phase B | `src/drafter/browser.py` | If `playwright` package is installed without Chromium binaries or `/demo-portal` server isn't running, `open_session()` raises at runtime | Catch browser launch / connection errors in `PlaywrightBrowserDriver.open_session()` and gracefully fall back to `_SimulatedSession` (unless `CARTHIJ_BROWSER_STRICT=true`) |
| **P1** | Phase B | `src/drafter/security.py` & `browser.py` | `validate_portal_url()` doesn't block `0.0.0.0` (`ip.is_unspecified`) or validate post-redirect `page.url` after `page.goto()` | Check `ip.is_unspecified` in `validate_portal_url()` and re-validate `page.url` after `page.goto()` |
| **P1** | Phase A/C | `src/core/draft_repository.py` | When re-running a draft via `revise`, old `step_log` entries in DB will cause SSE `sent_count` mismatch when final `save(draft)` overwrites `step_log` | Clear `step_log = []` in DB at the start of a `revise` run before dispatching the worker |
| **P2** | Phase B | `src/drafter/browser.py` | `page.goto(..., wait_until="networkidle")` times out on Next.js dev mode (HMR WebSocket) or portals with analytics | Use `wait_until="domcontentloaded"` + short 400ms settle delay |
| **P2** | Phase B | `src/drafter/orchestrator.py` | Clicking `next_btn` when client-side validation blocks navigation causes the loop to re-fill the same page `max_wizard_steps` times | Detect unchanged form signature `(heading, field_selectors)` after clicking `next_btn` and pause cleanly |
| **P2** | Phase A | `src/core/draft_repository.py` | `SQLiteDraftRepository.get()` and `list_by_report()` read without `self._lock` | Acquire `async with self._lock:` on SQLite reads for high-frequency SSE polling safety |
| **P3** | Phase A/C | `src/models/draft.py` | Merging `user_supplied_facts` across multiple `revise` calls | Merge `{**draft.user_supplied_facts, **req.user_supplied_facts}` in Phase C `revise` endpoint |
