# Carthij — Autonomous Funding Discovery & Browser-Use Application Drafter

An end-to-end agentic funding intelligence and browser-assisted application pre-fill platform powered by **Gemini 3.8 Flash** (`google-genai` SDK), **Tavily Search & Extract**, **Playwright Chromium**, **FastAPI**, **Next.js 15**, and **Google Cloud / Firebase**.

Carthij helps startups and 501(c)(3) non-profits go from a single funding intake form to **100% citation-verified state, federal, and foundation opportunities**—and then launches a live **Browser-Use Agent** to inspect the grant portal, pre-fill grounded answers while streaming live screenshots over SSE, flag missing compliance IDs (`EIN`, `SAM.gov UEI`), and **hard-stop before Final Submit** for human sign-off.

---

## Architecture: Deterministic Backbone, Adaptive Agents

Carthij deliberately avoids unconstrained LLM hub-and-spoke loops. Instead, it uses a **Fixed DAG + Parallel Fan-Out / Fan-In** where deterministic Python code owns control flow, routing, tool budgets, citation verification, and browser safety, while **Gemini 3.8 Flash** owns query understanding, bounded follow-up research, structured extraction, and adaptive DOM-to-fact form mapping.

```text
[ Next.js UI ] ──POST /api/v1/reports (202)──▶ [ FastAPI + JobDispatcher (Local / Cloud Tasks) ]
       ▲                                                              │
       │ Live SSE (/stream)                                           ▼
       │                                          [ FundingPipelineOrchestrator (Fixed DAG) ]
       │                                                              │
       │   ┌──────────────────────────────────────────────────────────┘
       │   ▼
       │  STAGE 1: QueryPlanner (Gemini 3.8 Flash) ──▶ Parallel Tavily /search + /extract
       │           ──▶ Concurrency-safe RetrievedSourceRegistry ([SRC-N], .gov authority boost)
       │   │
       │   ▼
       │  STAGE 2: 4 Opinionated Specialists in Parallel (asyncio.gather, Two-Phase Tool → JSON)
       │           ├── StateLocalProgramsAgent (NY / NJ / SF, tool cap <= 2)
       │           ├── FederalFoundationAgent (SBIR / Grants.gov, tool cap <= 2)
       │           ├── CapitalStrategyAgent (Startup) OR PhilanthropicStrategyAgent (Non-Profit)
       │           └── EligibilityComplianceAgent (pure registry audit, 0 tools)
       │   │
       │   ▼
       │  STAGE 3: Programmatic Citation Verifier (drops unverified URLs, prefers .gov)
       │           ──▶ ReportSynthesizer (Readiness Score 0-100, Gaps, 7/30/90-day Action Plan)
       │           ──▶ ReportRepository (Cloud Firestore + firestore.rules / SQLite WAL)
       │   │
       │   ▼  (User clicks "Draft & Pre-Fill" on any verified opportunity)
       │  STAGE 4: BrowserDraftOrchestrator (Playwright Chromium + FormMapperAgent)
       │           ├── SSRF URL Guard & Auth/CAPTCHA Detector (halts cleanly if gated)
       │           ├── Live DOM Extraction & ApplicantFactLedger Mapping (0 fabricated EIN/UEI)
       │           ├── Streams compressed JPEG viewport frames over SSE (/api/v1/drafts/{id}/stream)
       │           └── HARD STOP before Final Submit (status="ready_for_review") + v2 /revise loop
```

---

## Key Capabilities & Safety Guardrails

- **Hybrid Tavily Retrieval**: Parallel `POST /search` (OpenAPI-compliant parameter constraints + `Retry-After` backoff) followed by selective `POST /extract` on top authoritative `.gov` / agency URLs.
- **Zero-Hallucination Citation Verification**: Every `FundingOpportunity` is programmatically checked against `RetrievedSourceRegistry`; unverified URLs are dropped and cross-specialist duplicates prefer official `.gov` sources.
- **Two-Phase `BaseAgent` Execution**: Tool-calling turns run with `tools` enabled and `response_schema=None`, followed by a final structured extraction turn with `response_schema` enabled for Gemini Developer API & Vertex AI parity.
- **Live Browser-Use Pre-Fill + Human-in-the-Loop**:
  - Uses Playwright's native `.fill()`, `.select_option()`, and `.check()` on live DOM controls (React 19 compatible), guarded by a shared `Browser` + `asyncio.Semaphore(2)` and SSRF blocklist.
  - Refuses to fabricate missing compliance identifiers (`Federal EIN`, `SAM.gov UEI`), surfacing them in an interactive sidebar so users can supply verified values and trigger a v2 re-fill.
  - Ships with a realistic 3-step **Demo Grant Portal** (`/demo-portal`) alongside support for any public unauthenticated grant/accelerator form.
- **Google Cloud & Firebase Native**:
  - `ReportRepository` & `DraftRepository` support both **Cloud Firestore** (with locked-down `firestore.rules`, composite `firestore.indexes.json`, and 1 MiB document size guards) and **SQLite (WAL mode)** for zero-setup local dev.
  - Production **Cloud Run** `Dockerfile` + **Cloud Tasks** dispatcher + **Firebase App Hosting** (`ui/apphosting.yaml`).

---

## Quick Start (Local Development)

### 1. Environment Setup
```bash
cp .env.example .env
```
Set your API keys in `.env`:
```env
GEMINI_API_KEY=your_gemini_api_key_here
TAVILY_API_KEY=tvly-your_tavily_api_key_here
```

### 2. Backend Setup (FastAPI + Playwright)
```bash
uv sync
uv pip install playwright
uv run --no-sync playwright install chromium

# Start FastAPI backend on http://127.0.0.1:8000
uv run --no-sync uvicorn src.api.app:app --host 127.0.0.1 --port 8000 --reload
```

### 3. Frontend Setup (Next.js 15)
```bash
cd ui
npm install
npm run dev
```
Open **http://127.0.0.1:3000** to submit an intake, view `/reports`, or test `/demo-portal`.

---

## API Overview

| Method & Endpoint | Description |
| :--- | :--- |
| `GET /api/v1/health` | Service health probe |
| `POST /api/v1/reports` | Queue async funding readiness workflow (`202 Accepted`) |
| `GET /api/v1/reports` | List canonical `FundingReport` records |
| `GET /api/v1/reports/{id}` | Fetch full `FundingReport` with verified opportunities & citations |
| `GET /api/v1/reports/{id}/stream` | Multi-instance-safe SSE stream of live pipeline stages |
| `POST /api/v1/reports/{id}/drafts` | Launch Playwright Browser-Use pre-fill job (`202 Accepted`) |
| `GET /api/v1/drafts/{draft_id}` | Fetch canonical `ApplicationDraft` |
| `GET /api/v1/drafts/{draft_id}/stream` | Live SSE stream of browser actions + JPEG viewport screenshots |
| `POST /api/v1/drafts/{draft_id}/revise` | Submit answers to `missing_inputs` and re-run browser pre-fill |
| `GET /api/v1/drafts/{draft_id}/export` | Download formatted Markdown application package |

---

## Testing, Architecture Docs & Pitch Slides

```bash
# Run backend unit tests & evaluation benchmarks
uv run --no-sync python -m unittest discover -s tests -v

# Verify Next.js TypeScript & production build
cd ui && npm run build
```

- **Backend Architecture Plan**: [docs/backend_architecture_plan.md](./docs/backend_architecture_plan.md)
- **Browser-Use Drafter Plan**: [docs/application_drafter_plan.md](./docs/application_drafter_plan.md)
- **Development Log**: [docs/development_log.md](./docs/development_log.md)
- **Hackathon Pitch Slides (Reveal.js)**: [docs/hackathon_pitch.html](./docs/hackathon_pitch.html)
- **Evaluation Benchmark Slides (Reveal.js)**: [docs/hackathon_eval_slides.html](./docs/hackathon_eval_slides.html)
