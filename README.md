# Multi-Step Research Agent

A production-grade research agent that decomposes a user query into sub-questions, searches the web via Tavily, synthesizes findings with DeepSeek V4 Flash (served at cost through OpenCode Zen), and returns a cited summary — all orchestrated by LangGraph with full Langfuse tracing. Built with FastAPI.

---

## Architecture

```
                         ┌─────────────┐
                         │   FastAPI    │
                         │ /research    │
                         │ /health      │
                         └──────┬───────┘
                                │
                         ┌──────▼───────┐
                         │   LangGraph   │
                         │   (StateGraph)│
                         └──────┬───────┘
                                │
    ┌───────────┬───────────┬───▼───┬───────────┐
    │           │           │       │           │
┌───▼───┐  ┌───▼───┐  ┌────▼───┐  │  ┌───────▼┐
│planner│─▶│searcher│─▶│summari-│──┼─▶│ budget │
│       │  │(Tavily)│  │  zer   │  │  └───┬────┘
└───────┘  └───────┘  └────────┘  │      │
                                  │  ┌───▼───┐
                        route_final ─▶│ cite  │──▶ END
                                  │  └───────┘
                        ┌─────────┤
                        │  trace  │  (Langfuse / no-op)
                        └─────────┘
```

The agent loops: **planner → searcher → summarizer** up to `max_iterations` times. If summarization succeeds or no results are found, it exits to **budget** (hard cost cap check) then **cite** (extract citations from results, no LLM call). See [ARCHITECTURE.md](ARCHITECTURE.md) for the full design.

---

## Quick Start

### 1. Clone and install

```bash
git clone <repo-url> && cd multi-step-agent
pip install -e ".[dev]"
```

### 2. Configure environment

```bash
cp .env.example .env
# Edit .env — set at minimum OPENAI_API_KEY and TAVILY_API_KEY
```

### 3. Run locally

```bash
uvicorn agent.main:app --reload --port 8000
```

### 4. Docker

```bash
docker build -t multi-step-agent .
docker compose up --build
```

### 5. Verify

```bash
curl http://localhost:8000/health
# → {"status":"ok"}
```

---

## API Reference

### `GET /health`

Returns service health.

```bash
curl http://localhost:8000/health
```

```json
{"status": "ok"}
```

---

### `POST /research`

Runs the full agent synchronously and returns the result.

**Request:**

```json
{"query": "What are the latest breakthroughs in fusion energy?"}
```

**Response:**

```json
{
  "summary": "Recent breakthroughs in fusion energy include...",
  "confidence": "high",
  "citations": [
    {"index": 1, "title": "Article Title", "url": "https://example.com"}
  ],
  "status": "ok",
  "iterations": 2,
  "cost_usd": 0.003421,
  "trace_id": "abc123",
  "errors": []
}
```

**Status values:** `ok`, `no_results`, `summary_failed`, `budget_exceeded`

```bash
curl -X POST http://localhost:8000/research \
  -H "Content-Type: application/json" \
  -d '{"query": "What are the latest breakthroughs in fusion energy?"}'
```

---

### `POST /research/stream`

Streams the agent execution as Server-Sent Events. Enforces a total timeout of `REQUEST_TIMEOUT` seconds (default 180).

**SSE event types:**

| Event | Payload | When |
|-------|---------|------|
| `status` | `{"node": "...", "stage": "..."}` | Each node starts (`planner`, `searcher`, `summarizer`, `cite`, `budget`) |
| `progress` | `{"sub_questions": [...]}` or `{"results_found": N}` | After planner/searcher produce output |
| `citation` | `{"index": 1, "title": "...", "url": "..."}` | Each citation extracted |
| `cost` | `{"total_cost": 0.001, "cost_usd": 0.001}` | After each node with cost data |
| `complete` | Full result (same shape as `POST /research`) | Agent finishes |
| `error` | `{"error": "...", "status": "..."}` | Exception or timeout |

```bash
curl -N -X POST http://localhost:8000/research/stream \
  -H "Content-Type: application/json" \
  -d '{"query": "What are the latest breakthroughs in fusion energy?"}'
```

---

## Configuration

All settings are read from environment variables (or `.env`). Defaults in `src/agent/config.py`.

| Variable | Default | Purpose |
|----------|---------|---------|
| `OPENAI_API_KEY` | `""` | API key for OpenCode Zen (DeepSeek V4 Flash) |
| `LLM_BASE_URL` | `https://opencode.ai/zen/v1` | LLM API base URL |
| `LLM_MODEL` | `deepseek-v4-flash` | Model name |
| `TAVILY_API_KEY` | `""` | Tavily search API key |
| `MAX_ITERATIONS` | `3` | Max planner→searcher→summarizer loops per request |
| `PER_REQUEST_BUDGET` | `0.05` | Hard USD cost cap per request |
| `REQUEST_TIMEOUT` | `180` | Total timeout (seconds) for streaming runs |
| `SEARCH_DEPTH` | `basic` | Tavily search depth (`basic` or `advanced`) |
| `MAX_SEARCH_RESULTS` | `5` | Max results per search query / max results passed to summarizer |
| `LANGFUSE_PUBLIC_KEY` | `""` | Langfuse public key (optional; leave blank to disable tracing) |
| `LANGFUSE_SECRET_KEY` | `""` | Langfuse secret key |
| `LANGFUSE_HOST` | `https://cloud.langfuse.com` | Langfuse host URL |

---

## Failure Handling

| Failure Mode | How the Code Handles It |
|---|---|
| Malformed JSON from LLM | `parse_json` strips markdown fences, extracts first `{...}` block. On failure, `call_llm_json` re-prompts up to 2 times with an example. Final fallback: planner degrades to searching the raw query (`nodes.py:108`). |
| Tavily 429 rate limit | `tools.py` respects `Retry-After` header, retries up to 3 times with exponential backoff. Raises `RateLimitError` when exhausted. |
| Tavily 5xx | Retries with exponential backoff (2^attempt seconds). |
| LLM 429 / retry | `ChatOpenAI` configured with `max_retries=1`. Planner/summarizer re-prompt on parse failure adds additional attempts. |
| Search failure (all queries) | Error recorded in `state["errors"]`, partial results from successful queries kept, status set to `no_results`. |
| Summarizer failure | Returns `summary_failed` status. `route_final` loops back to planner until `max_iterations` is reached. |
| Budget exceeded | `budget_node` checks `total_cost > per_request_budget`. Returns `budget_exceeded` status with abort message. |
| SSE stream timeout | `streaming.py` enforces a deadline. On timeout: iterator closed, `error` event emitted with `"timed out after Ns"`. |
| Langfuse unavailable | `tracing.py` catches import/auth failures silently. Callbacks list stays empty — agent runs as a no-op with no tracing. |

For the full failure-mode analysis with root causes and prevention strategies, see [FAILURE_ANALYSIS.md](FAILURE_ANALYSIS.md).

---

## Testing

```bash
# Run all tests (no API keys needed — tests use fakes/mocks)
python -m pytest

# With coverage
python -m pytest --cov=src/agent --cov-report=term-missing
```

**Test suite:** 45 tests across 6 files covering graph topology, JSON parsing, routing logic, budget enforcement, citation extraction, SSE streaming, timeout behavior, Tavily retry/backoff, node units, cost accumulation, the max-iteration guard, config defaults/env overrides, and health endpoint + input validation. All tests run without network access or API keys.

**Lint and type check:**

```bash
ruff check .
mypy src
```

---

## Cost Estimation

DeepSeek V4 Flash via OpenCode Zen is priced at cost:

| Token type | Price (per 1M tokens) |
|---|---|
| Input | $0.14 |
| Output | $0.28 |

Per-request cost is tracked after every LLM call. The hard cap is `PER_REQUEST_BUDGET` (default $0.05). With `MAX_ITERATIONS=3`, a typical request makes up to 4 LLM calls (planner + up to 3 summarizers).

**Worst-case single request** (3 iterations, 4 LLM calls, ~5k input + ~1k output tokens each):

```
4 × (5000 × $0.14/M + 1000 × $0.28/M) ≈ $0.004
```

Actual costs are typically lower. The budget cap prevents runaway spending.

---

## Documentation

| Document | Contents |
|----------|----------|
| [SETUP.md](SETUP.md) | Environment setup, API keys, running locally |
| [ARCHITECTURE.md](ARCHITECTURE.md) | Full system design, state management, node specs, security |
| [DEPLOYMENT.md](DEPLOYMENT.md) | Docker, Railway, Render deployment guides |
| [FAILURE_ANALYSIS.md](FAILURE_ANALYSIS.md) | Production failure modes, root causes, fixes, monitoring |

---

## License

See repository for license details.
