# Carthij Agentic Workflow

A lightweight, modern multi-agent orchestration harness powered by the Google Gemini API (`google-genai` SDK) and FastAPI.

## Architecture

```
[ User Form ] ──(POST / Webhook)──▶ [ FastAPI Service ]
                                             │
                                             ▼
                                  [ Coordinator Agent ]
                            (Gemini 2.5 Flash: Planner)
                                             │
                       ┌─────────────────────┼─────────────────────┐
                       ▼                     ▼                     ▼
               [ Subagent A ]          [ Subagent B ]        [ Subagent C ]
             (e.g., Researcher)       (e.g., Validator)     (e.g., Analyst)
                       │                     │                     │
                       └─────────────────────┼─────────────────────┘
                                             ▼
                                   (asyncio.gather / parallel)
                                             ▼
                                  [ Synthesis & Output ]
                               (Coordinator finalizes result)
                                             │
                                             ▼
                            [ Webhook Response / DB Save / PubSub ]
```

## Features

- **Direct Gemini Integration**: Uses the official `google-genai` SDK with native tool/function calling and structured JSON outputs (`response_schema`).
- **Parallel Subagent Execution**: The Coordinator breaks down incoming requests and fans out subtasks simultaneously using Python's `asyncio.gather()`.
- **Hybrid Packaging**: Managed via `uv` and `pyproject.toml`, with an auto-generated, clean `requirements.txt` for standard `pip` and Docker/Cloud Run builds.
- **Webhook & API Ready**: Built-in FastAPI endpoints for form ingestion with synchronous and asynchronous (background task) modes.

## Getting Started

### 1. Environment Setup

Copy `.env.example` to `.env`:

```bash
cp .env.example .env
```

Set your Gemini API key (or Vertex AI credentials):

```env
GEMINI_API_KEY=your_api_key_here
```

### 2. Virtual Environment & Installation

#### Option A: Using `uv` (Recommended)

`uv` automatically creates and manages a virtual environment in `.venv/`:

```bash
# Create/sync virtual environment and install dependencies
uv sync

# (Optional) Activate the virtual environment
source .venv/bin/activate
```

> **Note**: When using `uv run <command>`, `uv` automatically executes within `.venv` without needing manual activation.

#### Option B: Standard Python `venv` + `pip`

If you are not using `uv`, create and activate a standard virtual environment:

```bash
# 1. Create a virtual environment
python3 -m venv .venv

# 2. Activate the virtual environment
source .venv/bin/activate

# 3. Upgrade pip and install dependencies
pip install --upgrade pip
pip install -r requirements.txt
```

### 3. Run the Services

**Backend (FastAPI):**
```bash
uv run api
# or
uv run uvicorn src.api.app:app --reload --port 8000
```
API Documentation: [http://localhost:8000/docs](http://localhost:8000/docs)

**Frontend (Next.js):**
```bash
cd ui
npm run dev
```
Open [http://localhost:3000](http://localhost:3000) to view the funding intake application.

### 4. Triggering the Workflow via Webhook

Send a POST request to `/api/v1/webhook/submit`:

```bash
curl -X POST http://localhost:8000/api/v1/webhook/submit \
  -H "Content-Type: application/json" \
  -d '{
    "form_id": "client_intake",
    "submitter_email": "user@example.com",
    "data": {
      "company_name": "Apex Global",
      "target_market": "European Union",
      "business_model": "SaaS B2B"
    },
    "context": "Evaluate market readiness and regulatory compliance."
  }'
```

### Running Tests

```bash
uv run python -m unittest discover -s tests
```