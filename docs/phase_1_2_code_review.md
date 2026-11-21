# Phase 1 & Phase 2 Code Review

**Date:** 2026-11-21  
**Scope:** Review of Phase 1 (Retrieval & Canonical Models) and Phase 2 (Opinionated Specialists & Grounding) implementation against [backend_architecture_plan.md](./backend_architecture_plan.md) and [development_log.md](./development_log.md).

---

## 1. Executive Summary

Phase 1 and Phase 2 have been implemented with high fidelity to the architecture plan. All unit tests in `tests/test_harness.py` pass cleanly. The modular boundaries between retrieval (`src/core/tavily.py`, `src/pipeline/registry.py`, `src/agents/planner.py`), specialist execution (`src/core/agent.py`, `src/agents/specialists/`), grounding (`src/pipeline/grounding.py`), synthesis (`src/agents/synthesizer.py`), and orchestration (`src/pipeline/orchestrator.py`) are clean, readable, and well-typed.

Below is a file-by-file review covering **what was done well** and **actionable findings (P1/P2/P3)** to address as the codebase moves through Phase 3 and Phase 4.

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
  In `register_extract_response()` (`lines 168–183`), each item returned by Tavily `/extract` is upserted via `self._upsert_unlocked(url=item.url, extracted_content=item.raw_content)`. If Tavily `/extract` normalizes or resolves a redirect on `item.url` (e.g., `http://` -> `https://` is handled by `normalize_url`, but query parameter ordering, utm tracking params, or a trailing path redirect like `/grants` -> `/grants/overview` is not), `_upsert_unlocked()` will create a **new** `SourceRecord` with an empty `title`, `score=0.0`, and empty `matched_scopes` instead of enriching the existing `SourceRecord` that was selected for extraction.
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

## 4. Summary Priority Matrix

| Priority | Component | Issue | Recommended Fix |
| :--- | :--- | :--- | :--- |
| **P1** | `src/pipeline/grounding.py` | `source_id` fallback (`SRC-1`) can accidentally validate a hallucinated `source_url` if the LLM guesses `"SRC-1"` | Only fall back to `get_by_id(opp.source_id)` when `opp.source_url` is blank or shares the same domain as the `SRC-N` record |
| **P1** | `src/core/agent.py` | `self.history` persists on `BaseAgent` instance across multiple `run()` calls | Reset `self.history` per `run()` call (or default `reset_history: bool = True`) |
| **P1** | `src/pipeline/registry.py` | `register_extract_response()` can create a disconnected `score=0.0` record if `/extract` returns a redirected URL | Map extracted content back to the requested URL record when redirected, and boost `is_extracted` in `list_sources()` |
| **P2** | `src/pipeline/orchestrator.py` | `"searching"` and `"extracting"` SSE events fire back-to-back before `/search` runs; no fallback if `synthesizer` fails | Emit `"extracting"` between `/search` and `/extract`; fail fast if 0 specialists succeed; add fallback summary if only synthesizer fails |
| **P2** | `src/core/agent.py` | Unchecked `response.candidates[0]` access | Guard against empty `response.candidates` |
| **P2** | `src/pipeline/registry.py` | `normalize_url()` keeps `utm_*` and unsorted query params | Strip tracking params and sort query parameters |
| **P3** | `src/agents/specialists/roster.py` | `STATE_GUIDANCE` only matches exact `"NY"`, `"NJ"`, `"SF"` strings | Normalize full state/city names (`"California"`, `"CA"`, `"New York"`, `"New Jersey"`) |
| **P3** | `src/pipeline/orchestrator.py` | `readiness_gaps` from 4 specialists are concatenated without deduplication | Deduplicate overlapping gaps across specialists |
