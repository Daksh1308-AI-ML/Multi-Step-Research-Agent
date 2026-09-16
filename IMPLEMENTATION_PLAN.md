# Multi-Step Research Agent with Real Workflow — Implementation Plan

---

## Project Overview

This project delivers a production-grade multi-step research agent built on LangGraph that orchestrates complex research tasks through a defined state machine workflow. The agent accepts a research query, decomposes it into sub-questions, searches the web via Tavily, synthesizes findings with GPT-4, and produces a cited summary — all while persisting state to PostgreSQL, emitting real-time progress via SSE, and recording full traces through Langfuse. The goal is not a prototype but a deployable service with structured error handling, cost tracking, rate-limit resilience, and comprehensive test coverage.

---

## Tech Stack

| Component          | Technology                   | Purpose                                      |
|--------------------|------------------------------|----------------------------------------------|
| Language           | Python 3.14                  | Runtime                                      |
| Package Manager    | Poetry                       | Dependency management, virtual env           |
| Agent Orchestration| LangGraph                    | State-machine graph for multi-step reasoning |
| LLM                | OpenAI GPT-4                 | Planning, summarization, citation            |
| Vector / Search    | Tavily API                   | Real-time web search                         |
| Database           | PostgreSQL                   | State persistence, checkpointing             |
| DB ORM / Checkpoint| LangGraph PostgreSQL Saver   | Graph state checkpointing                    |
| Observability      | Langfuse                     | Tracing, cost tracking, prompt management    |
| API Framework      | FastAPI                      | REST endpoints, SSE streaming                |
| Containerization   | Docker (multi-stage)         | Reproducible builds                          |
| Orchestration      | docker-compose               | Local multi-service stack                    |
| Testing            | pytest + pytest-asyncio      | Unit, integration, and API tests             |
| HTTP Client        | httpx                        | Async HTTP for external API calls            |
| Validation         | Pydantic v2                  | Settings, request/response schemas           |

---

## Directory Structure

```
multi-step-research-agent/
├── src/
│   └── research_agent/
│       ├── __init__.py
│       ├── main.py                  # FastAPI app, lifespan, mounts
│       ├── config.py                # Pydantic Settings (env, secrets)
│       ├── state.py                 # ResearchState TypedDict
│       ├── graph.py                 # LangGraph StateGraph construction
│       ├── nodes/
│       │   ├── __init__.py
│       │   ├── planner.py           # Decompose query into sub-questions
│       │   ├── searcher.py          # Tavily web search per sub-question
│       │   ├── summarizer.py        # Synthesize search results
│       │   └── citer.py             # Format final output with citations
│       ├── utils/
│       │   ├── __init__.py
│       │   ├── llm.py               # OpenAI client wrapper, retry logic
│       │   ├── search.py            # Tavily client wrapper, retry logic
│       │   ├── cost_tracker.py      # Token + API cost accumulation
│       │   └── json_utils.py        # Structured output parse + fallback
│       └── api/
│           ├── __init__.py
│           ├── routes.py            # /research, /research/stream, /health
│           ├── schemas.py           # Request/response Pydantic models
│           └── streaming.py         # SSE generator for progress events
├── tests/
│   ├── __init__.py
│   ├── conftest.py                  # Shared fixtures, mocks
│   ├── unit/
│   │   ├── __init__.py
│   │   ├── test_planner.py
│   │   ├── test_searcher.py
│   │   ├── test_summarizer.py
│   │   ├── test_citer.py
│   │   ├── test_cost_tracker.py
│   │   └── test_json_utils.py
│   ├── integration/
│   │   ├── __init__.py
│   │   └── test_full_graph.py
│   └── api/
│       ├── __init__.py
│       └── test_endpoints.py
├── migrations/
│   └── README.md                    # LangGraph PostgreSQL setup notes
├── scripts/
│   └── seed_example_queries.py      # Demo query seeder
├── docker/
│   ├── Dockerfile                   # Multi-stage build
│   └── docker-compose.yml           # App + PostgreSQL + Langfuse
├── pyproject.toml                   # Poetry project, deps, tool config
├── alembic.ini                      # (optional) DB migrations
├── .env.example                     # Required env vars template
├── .gitignore
├── Makefile                         # dev shortcuts (test, lint, run)
└── README.md
```

---

## Phase 1: Core Agent (Week 1)

### Milestone 1.1 — Project Scaffolding

- [ ] Initialize Poetry project (`poetry init --no-interaction`)
- [ ] Set Python version to 3.14 in `pyproject.toml`
- [ ] Add all dependencies (see Dependencies Table below)
- [ ] Create `.env.example` with all required variables:
  - `OPENAI_API_KEY`
  - `TAVILY_API_KEY`
  - `DATABASE_URL`
  - `LANGFUSE_PUBLIC_KEY`
  - `LANGFUSE_SECRET_KEY`
  - `LANGFUSE_HOST`
- [ ] Create `src/research_agent/config.py` with Pydantic `BaseSettings` loading from `.env`
- [ ] Create `Makefile` with `test`, `lint`, `run`, `docker-up` targets
- [ ] Create `.gitignore` (Python, env, __pycache__, .venv, docker volumes)

**Acceptance Criteria:**
- `poetry install` succeeds with zero errors
- `poetry run python -c "from research_agent.config import Settings; print(Settings())"` prints loaded config
- All env vars documented in `.env.example`

---

### Milestone 1.2 — State Definition

- [ ] Create `src/research_agent/state.py`
- [ ] Define `ResearchState(TypedDict)` with fields:

| Field              | Type              | Description                                |
|--------------------|-------------------|--------------------------------------------|
| `query`            | `str`             | Original user research query               |
| `sub_questions`    | `list[str]`       | Decomposed sub-questions from planner      |
| `current_question` | `str`             | Active sub-question being processed        |
| `search_results`   | `list[dict]`      | Raw Tavily results per sub-question        |
| `summaries`        | `list[str]`       | Per-question summaries                     |
| `final_report`     | `str`             | Final synthesized report                   |
| `citations`        | `list[dict]`      | Structured citation list                   |
| `iteration`        | `int`             | Current graph iteration count              |
| `cost_usd`         | `float`           | Running cost accumulator                   |
| `error`            | `Optional[str]`   | Last error message (if any)                |
| `status`           | `str`             | Current status: planning/searching/etc.    |

**Acceptance Criteria:**
- `ResearchState` is importable and satisfies LangGraph's state requirements
- All fields have correct type annotations
- State can be serialized/deserialized for PostgreSQL persistence

---

### Milestone 1.3 — Node Implementations

#### 1.3a: Planner Node

- [ ] Create `src/research_agent/nodes/planner.py`
- [ ] Accept `query` from state, call GPT-4 with structured prompt
- [ ] Parse response into `list[str]` sub-questions (3-5 questions)
- [ ] Update state with `sub_questions` and `status = "planning_complete"`

**Acceptance Criteria:**
- Given "Compare React vs Vue for enterprise apps", returns 3-5 distinct sub-questions
- Each sub-question is a non-empty string
- Planner handles empty/malformed LLM responses gracefully

#### 1.3b: Searcher Node

- [ ] Create `src/research_agent/nodes/searcher.py`
- [ ] Pop next unsearched question from `sub_questions`
- [ ] Call Tavily search API with the sub-question
- [ ] Append results to `search_results` list
- [ ] Update `current_question` and `status = "searching"`

**Acceptance Criteria:**
- Each sub-question produces at least 1 search result (or error is recorded)
- Tavily API errors are caught and stored in `error` field without crashing
- Results contain `title`, `url`, `content` keys

#### 1.3c: Summarizer Node

- [ ] Create `src/research_agent/nodes/summarizer.py`
- [ ] Take search results for current question
- [ ] Call GPT-4 to produce a concise summary with key findings
- [ ] Append summary to `summaries` list
- [ ] Update `status = "summarizing"`

**Acceptance Criteria:**
- Summary is 2-4 paragraphs covering key findings
- Summary references specific sources from search results
- Handles case where search results are empty

#### 1.3d: Citer Node

- [ ] Create `src/research_agent/nodes/citer.py`
- [ ] Take all summaries and raw search results
- [ ] Call GPT-4 to produce final report with inline citations
- [ ] Extract structured citation list `[{title, url, relevance}]`
- [ ] Update `final_report`, `citations`, and `status = "complete"`

**Acceptance Criteria:**
- Final report contains at least one `[N]` citation per summary section
- All citations map to entries in the `citations` list
- Report is markdown-formatted

---

### Milestone 1.4 — LangGraph Workflow

- [ ] Create `src/research_agent/graph.py`
- [ ] Build `StateGraph(ResearchState)`
- [ ] Register all nodes: `planner`, `searcher`, `summarizer`, `citer`
- [ ] Define edges:

```
START → planner
planner → searcher
searcher → summarizer
summarizer → (search_next | citer)  # conditional
citer → END
```

- [ ] Implement `search_next` conditional edge:
  - If unsearched questions remain → `searcher`
  - If all questions searched → `citer`
- [ ] Compile graph with `compile()`

**Acceptance Criteria:**
- Graph processes a query end-to-end without manual intervention
- Conditional edge correctly routes between search iterations and citer
- Graph can be serialized via `get_graph().draw_mermaid()` for documentation

---

### Milestone 1.5 — Basic Error Handling

- [ ] Wrap each node in try/except with descriptive error messages
- [ ] Catch `json.JSONDecodeError` in planner/citer for malformed LLM output
- [ ] Catch `httpx.HTTPStatusError` in searcher for API failures
- [ ] Catch `openai.APIError` for LLM provider failures
- [ ] On error: set `state["error"]` and `state["status"] = "error"`
- [ ] Log errors via `logging` module with traceback

**Acceptance Criteria:**
- Any single node failure does not crash the entire graph
- Error message is captured in state and surfaced to caller
- Error is logged with full traceback at ERROR level

---

## Phase 2: Production Hardening (Week 2)

### Milestone 2.1 — Malformed JSON Handling

- [ ] Create `src/research_agent/utils/json_utils.py`
- [ ] Implement `parse_llm_json(text: str) -> dict | list`:
  1. Try `json.loads()` directly
  2. Try extracting JSON from markdown code fences (`` ```json ... ``` ``)
  3. Try regex extraction of first `{...}` or `[...]` block
  4. Raise `ValueError` if all methods fail
- [ ] Use OpenAI structured outputs (response_format) where available
- [ ] Apply fallback chain in planner and citer nodes

**Acceptance Criteria:**
- Handles LLM responses wrapped in markdown code fences
- Handles responses with leading/trailing text around JSON
- Raises clear error with original text on total failure

---

### Milestone 2.2 — Rate Limit Detection + Exponential Backoff

- [ ] Implement retry decorator in `src/research_agent/utils/llm.py` and `search.py`
- [ ] Detect HTTP 429 status codes from OpenAI and Tavily
- [ ] Parse `Retry-After` header when present
- [ ] Apply exponential backoff: `min(base * 2^attempt + jitter, max_delay)`
- [ ] Default: base=1s, max_delay=30s, max_retries=3
- [ ] Log each retry attempt with attempt number and delay

**Acceptance Criteria:**
- 429 responses trigger automatic retry (not immediate failure)
- Backoff delay increases with each attempt
- After max retries, error propagates with clear message

---

### Milestone 2.3 — API Failure Graceful Degradation

- [ ] If Tavily fails for a sub-question, record error and continue with remaining questions
- [ ] If GPT-4 summarizer fails, store raw search results as fallback summary
- [ ] If GPT-4 citer fails, concatenate existing summaries as bare report
- [ ] Never return empty response — always return partial results when possible
- [ ] Set `status = "partial_complete"` when degraded

**Acceptance Criteria:**
- Partial results are returned when one sub-question's search fails
- Output clearly indicates degraded mode in `status` field
- All partial data is preserved in state

---

### Milestone 2.4 — Cost Tracking Per Request

- [ ] Create `src/research_agent/utils/cost_tracker.py`
- [ ] Track tokens consumed per node: `input_tokens`, `output_tokens`
- [ ] Calculate cost using current OpenAI pricing for GPT-4:
  - Input: $30 / 1M tokens
  - Output: $60 / 1M tokens
- [ ] Track Tavily API calls (count per request)
- [ ] Accumulate running total in `state["cost_usd"]`
- [ ] Log cost breakdown per node at completion

**Acceptance Criteria:**
- `cost_usd` is accurate within 5% of manual calculation
- Cost breakdown is logged for every completed request
- Tavily call count is tracked separately

---

### Milestone 2.5 — Timeout Handling

- [ ] Set per-node timeout: 30s for planner/summarizer/citer, 15s for searcher
- [ ] Set per-request timeout: 180s total for entire graph execution
- [ ] Implement timeouts via `asyncio.wait_for()` wrapping node calls
- [ ] On timeout: set `error` field with "Node X timed out after Ys"
- [ ] On total timeout: stop graph and return partial state

**Acceptance Criteria:**
- Individual node hangs do not block entire request
- Total request never exceeds 180s (configurable)
- Timeout errors are distinguishable from API errors

---

### Milestone 2.6 — Infinite Loop Prevention

- [ ] Increment `state["iteration"]` each time `searcher` node is entered
- [ ] Add `max_iterations` config (default: 10)
- [ ] If `iteration >= max_iterations`, force route to `citer`
- [ ] Log warning when max iterations approaching (iteration >= max - 2)

**Acceptance Criteria:**
- Graph always terminates — no infinite loops possible
- Warning logged before hitting limit
- Configurable via `Settings`

---

## Phase 3: Deployment & API (Week 3)

### Milestone 3.1 — FastAPI Endpoints

- [ ] Create `src/research_agent/main.py` with FastAPI app + lifespan
- [ ] Create `src/research_agent/api/routes.py` with endpoints:

| Endpoint            | Method | Description                          |
|---------------------|--------|--------------------------------------|
| `/health`           | GET    | DB connectivity, LLM availability   |
| `/research`         | POST   | Full synchronous research           |
| `/research/stream`  | POST   | SSE streaming research              |

- [ ] Create `src/research_agent/api/schemas.py` with Pydantic models:

| Model             | Purpose                              |
|-------------------|--------------------------------------|
| `ResearchRequest` | `query: str`, `max_questions: int`  |
| `ResearchResponse`| `report`, `citations`, `cost_usd`   |
| `HealthResponse`  | `status`, `db`, `llm`               |

- [ ] Implement `POST /research` → run graph, return `ResearchResponse`
- [ ] Implement `GET /health` → check DB ping + LLM ping

**Acceptance Criteria:**
- `POST /research` returns complete report with status 200
- `GET /health` returns 200 with component status
- Request validation returns 422 with clear error message
- Response matches `ResearchResponse` schema exactly

---

### Milestone 3.2 — SSE Streaming

- [ ] Create `src/research_agent/api/streaming.py`
- [ ] Implement SSE event types:
  - `status` — node status changes
  - `progress` — sub-question completion
  - `citation` — individual citation found
  - `cost` — running cost update
  - `complete` — final report
  - `error` — error occurred
- [ ] Stream events as graph progresses through nodes
- [ ] Use `text/event-stream` content type

**Acceptance Criteria:**
- SSE stream connects and delivers events in real-time
- Each event has `event:` and `data:` fields per SSE spec
- Client can reconstruct full progress from events
- Connection closes gracefully on completion

---

### Milestone 3.3 — Docker Multi-Stage Build

- [ ] Create `docker/Dockerfile`:

| Stage         | Base Image           | Purpose                    |
|---------------|----------------------|----------------------------|
| builder       | `python:3.14-slim`   | Install deps, compile      |
| production    | `python:3.14-slim`   | Copy built artifacts only  |

- [ ] Install Poetry in builder, copy `pyproject.toml` + `poetry.lock`
- [ ] Export requirements via `poetry export -f requirements.txt`
- [ ] Install deps in production stage from requirements.txt
- [ ] Copy `src/` into production image
- [ ] Set `CMD ["uvicorn", "research_agent.main:app", "--host", "0.0.0.0"]`
- [ ] Run as non-root user

**Acceptance Criteria:**
- `docker build` completes without errors
- Final image size < 300MB
- Container starts and serves API on port 8000
- No dev dependencies in production image

---

### Milestone 3.4 — Docker Compose Stack

- [ ] Create `docker/docker-compose.yml`:

| Service     | Image / Build          | Ports    | Depends On |
|-------------|------------------------|----------|------------|
| `app`       | Build from Dockerfile  | 8000:8000| postgres   |
| `postgres`  | `postgres:16-alpine`   | 5432:5432| —          |
| `langfuse`  | `langfuse/langfuse`    | 3000:3000| postgres   |

- [ ] Define shared `postgres_data` volume
- [ ] Configure env vars for inter-service communication
- [ ] Health checks for all services
- [ ] `.env` file mounted for secrets

**Acceptance Criteria:**
- `docker-compose up` starts all three services
- App connects to PostgreSQL successfully
- Langfuse UI accessible at `localhost:3000`
- Data persists across restarts via named volume

---

### Milestone 3.5 — Langfuse Integration

- [ ] Initialize Langfuse client in `src/research_agent/config.py`
- [ ] Create Langfuse trace for each research request
- [ ] Log spans for each graph node (planner, searcher, summarizer, citer)
- [ ] Record input/output for each node
- [ ] Record token usage and cost per span
- [ ] Add trace ID to API response headers for debugging
- [ ] Flush Langfuse events on request completion

**Acceptance Criteria:**
- Every `/research` request creates a trace in Langfuse dashboard
- Each node execution appears as a span with timing
- Token counts match cost tracker values
- Trace ID is in response header `X-Trace-ID`

---

## Phase 4: Testing & Documentation (Week 3)

### Milestone 4.1 — Unit Tests (Per Node)

- [ ] Create `tests/conftest.py` with shared fixtures:
  - Mock OpenAI responses
  - Mock Tavily responses
  - Sample `ResearchState` fixtures
- [ ] Create `tests/unit/test_planner.py`:
  - Test sub-question extraction from valid JSON
  - Test fallback when LLM returns malformed JSON
  - Test query decomposition quality (3-5 questions)
- [ ] Create `tests/unit/test_searcher.py`:
  - Test successful search result parsing
  - Test API error handling
  - Test result appending to state
- [ ] Create `tests/unit/test_summarizer.py`:
  - Test summary generation from search results
  - Test handling of empty search results
- [ ] Create `tests/unit/test_citer.py`:
  - Test citation format extraction
  - Test final report generation
- [ ] Create `tests/unit/test_cost_tracker.py`:
  - Test token accumulation
  - Test cost calculation accuracy
- [ ] Create `tests/unit/test_json_utils.py`:
  - Test direct JSON parse
  - Test code fence extraction
  - Test regex fallback
  - Test error on invalid input

**Acceptance Criteria:**
- All unit tests pass with `pytest tests/unit/ -v`
- Every node function has at least 3 test cases
- Mocked LLM/API calls (no real API calls in unit tests)
- Coverage > 85% for `src/research_agent/`

---

### Milestone 4.2 — Integration Tests

- [ ] Create `tests/integration/test_full_graph.py`
- [ ] Test end-to-end flow with mocked OpenAI + Tavily
- [ ] Test conditional routing (search_next logic)
- [ ] Test state persistence round-trip
- [ ] Test cost accumulation across full run
- [ ] Test timeout enforcement
- [ ] Test max iteration limit

**Acceptance Criteria:**
- Full graph completes with mocked APIs in < 5s
- State transitions follow expected path
- Final report is non-empty with citations

---

### Milestone 4.3 — API Endpoint Tests

- [ ] Create `tests/api/test_endpoints.py`
- [ ] Test `GET /health` returns 200 with component status
- [ ] Test `POST /research` with valid query returns 200 + `ResearchResponse`
- [ ] Test `POST /research` with empty query returns 422
- [ ] Test SSE streaming delivers events
- [ ] Test concurrent requests (3 parallel)
- [ ] Test request timeout returns 504

**Acceptance Criteria:**
- All endpoint tests pass with `pytest tests/api/ -v`
- Response schemas validated with Pydantic
- No real API calls made (all external services mocked)

---

### Milestone 4.4 — Error Scenario Tests

- [ ] Test OpenAI API key invalid → graceful error response
- [ ] Test Tavily API key invalid → partial results with error noted
- [ ] Test PostgreSQL down → health check returns 503
- [ ] Test malformed LLM response at each node → retry/fallback works
- [ ] Test rate limit (429) → retry with backoff succeeds
- [ ] Test total timeout → partial state returned

**Acceptance Criteria:**
- Every error scenario produces a structured response (never stack trace to client)
- Error responses include actionable error messages
- All error paths covered in tests

---

### Milestone 4.5 — README with Failure Analysis

- [ ] Write `README.md` with sections:
  - Project overview and motivation
  - Quick start (local dev + Docker)
  - Architecture diagram (ASCII)
  - API documentation (curl examples)
  - Configuration reference
  - **Failure analysis** — detailed breakdown of every failure mode:
    - What can fail at each node
    - How the system handles it
    - What the user sees
    - How to debug it
  - Cost estimation guide
  - Development guide (running tests, linting)

**Acceptance Criteria:**
- README is comprehensive enough to onboard a new developer
- Failure analysis covers all error scenarios tested in 4.4
- All curl examples are copy-pasteable and working

---

## Dependencies Table

| Package                   | Version约束   | Purpose                                     |
|---------------------------|---------------|---------------------------------------------|
| `langgraph`               | `>=0.2.0`     | Agent orchestration framework               |
| `langchain-openai`        | `>=0.2.0`     | OpenAI integration for LangChain ecosystem  |
| `openai`                  | `>=1.50.0`    | Direct OpenAI API client                    |
| `tavily-python`           | `>=0.5.0`     | Tavily search API client                    |
| `fastapi`                 | `>=0.115.0`   | API framework                               |
| `uvicorn[standard]`       | `>=0.30.0`    | ASGI server                                 |
| `httpx`                   | `>=0.27.0`    | Async HTTP client                           |
| `pydantic`                | `>=2.9.0`     | Data validation, settings management        |
| `pydantic-settings`       | `>=2.5.0`     | Environment-based settings                  |
| `sqlalchemy`              | `>=2.0.0`     | PostgreSQL async driver                     |
| `asyncpg`                 | `>=0.30.0`    | PostgreSQL async driver (for SQLAlchemy)    |
| `psycopg2-binary`         | `>=2.9.0`     | PostgreSQL sync driver (for migrations)     |
| `langfuse`                | `>=2.50.0`    | Observability and tracing                   |
| `python-dotenv`           | `>=1.0.0`     | .env file loading                           |
| `tenacity`                | `>=9.0.0`     | Retry logic with backoff                    |
| `pytest`                  | `>=8.3.0`     | Test framework                              |
| `pytest-asyncio`          | `>=0.24.0`    | Async test support                          |
| `pytest-cov`              | `>=5.0.0`     | Coverage reporting                          |
| `ruff`                    | `>=0.7.0`     | Linting and formatting                      |
| `mypy`                    | `>=1.12.0`    | Static type checking                        |

**Dev-only dependencies** (under `[tool.poetry.group.dev.dependencies]`):

| Package       | Purpose                          |
|---------------|----------------------------------|
| `ruff`        | Linting + formatting             |
| `mypy`        | Type checking                    |
| `pytest-cov`  | Coverage reports                 |

---

## Expected Results

| Metric                      | Baseline (prototype) | Target (production) | Current |
|-----------------------------|----------------------|---------------------|---------|
| Query-to-result latency     | 45-60s               | < 30s               | —       |
| Sub-question accuracy       | 60% relevant         | > 90% relevant      | —       |
| JSON parse success rate     | 70%                  | > 99%               | —       |
| Successful request rate     | 80%                  | > 98%               | —       |
| Cost per research request   | $0.15-0.25           | < $0.12             | —       |
| Unit test coverage          | 0%                   | > 85%               | —       |
| Integration test coverage   | 0%                   | > 80% (happy paths) | —       |
| Error scenarios tested      | 0                    | > 15                | —       |
| Docker image size           | N/A                  | < 300MB             | —       |
| Max concurrent requests     | 1                    | > 20                | —       |
| P99 latency (API)           | N/A                  | < 45s               | —       |

---

## Acceptance Criteria Summary

The project is **done** when ALL of the following are true:

| #  | Criterion                                           | Verified By          |
|----|-----------------------------------------------------|----------------------|
| 1  | `poetry install && poetry run make test` passes     | CI / manual          |
| 2  | `docker-compose up` starts full stack               | Manual               |
| 3  | `POST /research` returns valid report + citations   | API test             |
| 4  | SSE streaming delivers all event types              | API test             |
| 5  | `GET /health` reports all components healthy        | API test             |
| 6  | Any single node failure returns partial results     | Error scenario test  |
| 7  | Rate limit (429) triggers retry, not failure        | Error scenario test  |
| 8  | Malformed LLM JSON falls back gracefully            | JSON utils test      |
| 9  | Cost tracked per request, logged at completion      | Cost tracker test    |
| 10 | Total request timeout < 180s enforced               | Timeout test         |
| 11 | Max iterations enforced, no infinite loops          | Integration test     |
| 12 | Langfuse trace created for every request            | Manual + dashboard   |
| 13 | Unit test coverage > 85%                            | pytest-cov report    |
| 14 | All error scenarios tested and passing              | Full test suite      |
| 15 | README complete with failure analysis               | Manual review        |
| 16 | No secrets in source code                           | Manual + gitignore   |
| 17 | Linter (ruff) passes with zero warnings             | CI / make lint       |
| 18 | Type checker (mypy) passes                          | CI / make typecheck  |

---

## Timeline Summary

| Phase   | Focus                      | Duration | Dependencies        |
|---------|----------------------------|----------|---------------------|
| Phase 1 | Core agent                 | Week 1   | —                   |
| Phase 2 | Production hardening       | Week 2   | Phase 1 complete    |
| Phase 3 | Deployment & API           | Week 3   | Phase 2 complete    |
| Phase 4 | Testing & documentation    | Week 3   | Phase 2 complete    |

Phases 3 and 4 run in parallel during Week 3.

---

*Last updated: 2026-09-16*
