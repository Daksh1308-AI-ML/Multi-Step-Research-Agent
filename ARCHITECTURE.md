# Multi-Step Research Agent - System Architecture

## Table of Contents

1. [System Overview](#1-system-overview)
2. [LangGraph Workflow](#2-langgraph-workflow)
3. [Error Recovery Flow](#3-error-recovery-flow)
4. [Data Flow](#4-data-flow)
5. [Deployment Architecture](#5-deployment-architecture)
6. [Component Details](#6-component-details)
7. [State Management](#7-state-management)
8. [Node Specifications](#8-node-specifications)
9. [Security Considerations](#9-security-considerations)
10. [Performance Characteristics](#10-performance-characteristics)

---

## 1. System Overview

High-level architecture showing all components and their connections.

```mermaid
graph TB
    User["User / Client"]
    FastAPI["FastAPI<br/>(API Layer)"]
    LG["LangGraph<br/>(Orchestrator)"]
    PG["PostgreSQL<br/>(State Persistence)"]
    LF["Langfuse<br/>(Tracing & Observability)"]
    OAI["OpenAI GPT-4<br/>(Reasoning)"]
    TV["Tavily API<br/>(Web Search)"]

    User -->|"HTTP Request"| FastAPI
    FastAPI -->|"Submit Query"| LG
    LG -->|"Persist State"| PG
    LG -->|"Ingest Traces"| LF
    LG -->|"LLM Calls"| OAI
    LG -->|"Search Queries"| TV

    subgraph "LangGraph Nodes"
        P["planner"]
        S["searcher"]
        SM["summarizer"]
        C["cite"]
    end

    LG --- P
    LG --- S
    LG --- SM
    LG --- C

    P -->|"Plan"| S
    S -->|"Results"| SM
    SM -->|"Draft"| C
```

**Request lifecycle:**

1. Client sends a research query to FastAPI.
2. FastAPI validates the request and invokes the LangGraph workflow.
3. The workflow progresses through planner, searcher, summarizer, and cite nodes.
4. Each node makes LLM calls (GPT-4) and, where needed, calls Tavily for search.
5. State is persisted to PostgreSQL after every node transition.
6. Langfuse spans are attached to every node for full observability.
7. The final answer is returned to the client.

---

## 2. LangGraph Workflow

The agent state machine with conditional edges for error recovery.

```mermaid
graph TD
    START((START))
    PLANNER["planner"]
    SEARCHER["searcher"]
    SUMMARIZER["summarizer"]
    CITE["cite"]
    END((END))

    ERR["Error Router"]
    RETRY["Retry Counter"]
    FORCE_EXIT["Force Termination"]

    START --> PLANNER
    PLANNER -->|"success"| SEARCHER
    PLANNER -->|"error"| ERR
    SEARCHER -->|"success"| SUMMARIZER
    SEARCHER -->|"error"| ERR
    SUMMARIZER -->|"success"| CITE
    SUMMARIZER -->|"error"| ERR
    CITE -->|"success"| END
    CITE -->|"error"| ERR

    ERR -->|"retryable & under max"| RETRY
    ERR -->|"max iterations exceeded"| FORCE_EXIT
    ERR -->|"non-retryable"| FORCE_EXIT
    FORCE_EXIT -->|"return partial results"| END

    RETRY -->|"back to planner"| PLANNER

    style ERR fill:#f59e0b,stroke:#d97706,color:#000
    style FORCE_EXIT fill:#ef4444,stroke:#dc2626,color:#fff
    style RETRY fill:#3b82f6,stroke:#2563eb,color:#fff
```

**Edge conditions:**

| Source | Condition | Target |
|--------|-----------|--------|
| planner | valid plan produced | searcher |
| planner | parse failure or invalid plan | error router |
| searcher | results returned | summarizer |
| searcher | no results or API error | error router |
| summarizer | coherent summary produced | cite |
| summarizer | LLM error or empty summary | error router |
| cite | citations attached | END |
| cite | citation injection failed | error router |
| error router | retryable and iterations < max | planner (retry) |
| error router | non-retryable or max reached | force termination |

**Max iteration guard:** The workflow enforces a configurable `max_iterations` (default: 3). Each pass through planner increments a counter. Once exceeded, the router forces termination with the best partial results available.

---

## 3. Error Recovery Flow

Detailed error handling paths for each failure mode.

```mermaid
graph TD
    NODE_EXEC["Node Execution"]
    ERR_DETECT["Error Detection"]

    NODE_EXEC --> ERR_DETECT

    subgraph "JSON Parse Failure"
        JP1["LLM returns malformed JSON"]
        JP2["Re-prompt with stricter schema"]
        JP3["Regex fallback extraction"]
        JP4["Graceful Degradation"]
        JP1 --> JP2
        JP2 -->|"still invalid"| JP3
        JP3 -->|"still invalid"| JP4
    end

    subgraph "Rate Limit"
        RL1["429 Response"]
        RL2["Exponential Backoff<br/>(1s, 2s, 4s)"]
        RL3["Retry"]
        RL1 --> RL2
        RL2 --> RL3
        RL3 -->|"max 3 retries"| RL4["Fail with 429"]
    end

    subgraph "API Failure"
        AF1["HTTP 5xx or Timeout"]
        AF2["Fallback to Cached Results"]
        AF3["Return Partial Answer"]
        AF1 --> AF2
        AF2 -->|"no cache"| AF3
    end

    subgraph "Max Iterations"
        MI1["counter >= max_iterations"]
        MI2["Collect Best Partial Results"]
        MI3["Return with Warning Flag"]
        MI1 --> MI2
        MI2 --> MI3
    end

    ERR_DETECT -->|"parse error"| JP1
    ERR_DETECT -->|"rate limit"| RL1
    ERR_DETECT -->|"api failure"| AF1
    ERR_DETECT -->|"max iterations"| MI1
```

**Recovery strategies per error type:**

| Error Type | First Attempt | Fallback | Final State |
|---|---|---|---|
| JSON parse failure | Re-prompt with schema reminder | Regex extraction from raw text | Partial plan or skip to next step |
| Rate limit (429) | Wait `2^attempt` seconds | Retry up to 3 times | Graceful failure with retry-after header |
| API timeout | Retry once after 2s | Use cached/stale data if available | Return partial results with warning |
| API 5xx error | Retry once after 3s | Skip current operation | Return results from preceding nodes |
| Max iterations exceeded | N/A | Collect best available state | Return partial answer with `partial: true` flag |

---

## 4. Data Flow

What data flows through the system and how each node transforms it.

```mermaid
graph LR
    subgraph "Input"
        IQ["query: str"]
        IO["options: QueryOptions"]
    end

    subgraph "planner"
        P_IN["Input:<br/>query + options"]
        P_OUT["Output:<br/>search_queries: list[str]<br/>subtopics: list[str]<br/>strategy: str"]
    end

    subgraph "searcher"
        S_IN["Input:<br/>search_queries"]
        S_OUT["Output:<br/>search_results: list[SearchResult]<br/>raw_docs: list[str]"]
    end

    subgraph "summarizer"
        SM_IN["Input:<br/>search_results + raw_docs"]
        SM_OUT["Output:<br/>summary: str<br/>key_findings: list[str]"]
    end

    subgraph "cite"
        C_IN["Input:<br/>summary + key_findings + search_results"]
        C_OUT["Output:<br/>answer: str<br/>citations: list[Citation]"]
    end

    subgraph "Final Output"
        FO["Response:<br/>answer + citations + metadata"]
    end

    IQ --> P_IN
    IO --> P_IN
    P_IN --> P_OUT
    P_OUT --> S_IN
    S_IN --> S_OUT
    S_OUT --> SM_IN
    SM_IN --> SM_OUT
    SM_OUT --> C_IN
    C_OUT --> FO
```

**Node-to-node data mapping:**

| Node | Consumes | Produces | Transform |
|------|----------|----------|-----------|
| planner | `query`, `options` | `search_queries`, `subtopics`, `strategy` | Decomposes a single query into targeted search strategies |
| searcher | `search_queries` | `search_results`, `raw_docs` | Executes searches, fetches pages, extracts text |
| summarizer | `search_results`, `raw_docs` | `summary`, `key_findings` | Synthesizes findings into a coherent narrative |
| cite | `summary`, `key_findings`, `search_results` | `answer`, `citations` | Annotates the summary with inline citations |

---

## 5. Deployment Architecture

Docker-based deployment with PostgreSQL.

```mermaid
graph TB
    subgraph "Docker Network: agent-net"
        API["api-container<br/>FastAPI<br/>:8000"]
        WORKER["worker-container<br/>LangGraph Runtime"]
        PG_CONTAINER["postgres-container<br/>PostgreSQL 16<br/>:5432"]
        LF_CONTAINER["langfuse-container<br/>Langfuse<br/>:3000"]
    end

    EXTERNAL["External APIs"]
    HOST["Host Machine"]

    HOST -->|"port 8000"| API
    HOST -->|"port 3000"| LF_CONTAINER
    API -->|"gRPC / HTTP"| WORKER
    WORKER -->|"SQL over network"| PG_CONTAINER
    WORKER -->|"OTLP traces"| LF_CONTAINER
    WORKER -->|"HTTPS"| EXTERNAL

    subgraph "Volumes"
        PG_VOL["pgdata<br/>PostgreSQL data"]
        LF_VOL["langfuse-data<br/>Langfuse uploads"]
        LOG_VOL["app-logs<br/>Application logs"]
    end

    PG_CONTAINER --- PG_VOL
    LF_CONTAINER --- LF_VOL
    WORKER --- LOG_VOL
```

**Container specifications:**

| Container | Image | Ports | Dependencies |
|---|---|---|---|
| api-container | `python:3.12-slim` (custom build) | 8000:8000 | worker-container |
| worker-container | `python:3.12-slim` (custom build) | internal only | pg, langfuse |
| postgres-container | `postgres:16-alpine` | 5432:5432 | none |
| langfuse-container | `langfuse/langfuse:2` | 3000:3000 | pg |

**Volume mounts:**

| Volume | Container | Path | Purpose |
|---|---|---|---|
| pgdata | postgres-container | `/var/lib/postgresql/data` | Persistent database storage |
| langfuse-data | langfuse-container | `/app/data` | Uploaded traces and assets |
| app-logs | worker-container | `/app/logs` | Application-level logs |

**Environment variables (required):**

```
OPENAI_API_KEY=sk-...
TAVILY_API_KEY=tvly-...
DATABASE_URL=postgresql://user:pass@postgres:5432/agent_db
LANGFUSE_SECRET_KEY=sk-lf-...
LANGFUSE_PUBLIC_KEY=pk-lf-...
LANGFUSE_HOST=http://langfuse:3000
```

---

## 6. Component Details

| Component | Responsibility | Failure Modes | Recovery Strategy |
|---|---|---|---|
| **FastAPI** | Request validation, authentication, rate limiting, routing | Port conflict, invalid request body, auth failure | Configurable port, Pydantic validation with clear error messages, 401/403 responses |
| **LangGraph** | Workflow orchestration, state machine management, conditional routing | Node timeout, state corruption, infinite loops | Per-node timeout (30s default), state snapshots to PostgreSQL, max iteration guard |
| **OpenAI GPT-4** | Reasoning, planning, summarization, citation generation | Rate limit, API outage, token limit exceeded, malformed output | Exponential backoff, retry with capped attempts, structured output mode, regex fallback extraction |
| **PostgreSQL** | State persistence, checkpointing, audit trail | Connection pool exhaustion, disk full, corruption | Connection pooling (pgbouncer pattern), health checks, WAL-based recovery |
| **Langfuse** | Distributed tracing, latency tracking, cost attribution | Trace ingestion failure, storage exhaustion | Fire-and-forget with local buffer, graceful degradation (workflow continues without tracing) |
| **Tavily** | Web search, content extraction | Rate limit, empty results, stale content | Retry with backoff, query reformulation, return empty results for summarizer to handle |

---

## 7. State Management

The `ResearchState` TypedDict is the single source of truth passed through every node.

```python
class ResearchState(TypedDict):
    # --- Input ---
    query: str                          # Original user query
    options: QueryOptions               # User-specified options (max_results, depth, etc.)

    # --- Planner output ---
    search_queries: list[str]           # Decomposed search queries
    subtopics: list[str]                # Identified subtopics for deep dive
    strategy: str                       # Chosen research strategy (broad/narrow/deep)

    # --- Searcher output ---
    search_results: list[SearchResult]  # Ranked search results
    raw_docs: list[str]                 # Extracted full-text content

    # --- Summarizer output ---
    summary: str                        # Synthesized narrative
    key_findings: list[str]             # Bullet-point findings

    # --- Cite output ---
    answer: str                         # Final answer with inline citations
    citations: list[Citation]           # Structured citation objects

    # --- Metadata ---
    iteration: int                      # Current iteration counter
    max_iterations: int                 # Configurable cap (default 3)
    error_log: list[ErrorEntry]         # Accumulated errors for diagnostics
    partial_answer: str                 # Best available answer on early termination
    warnings: list[str]                 # Non-fatal warnings for the response
    latency_ms: float                   # Total wall-clock time
    token_usage: TokenUsage             # Prompt/completion/total tokens
    trace_id: str                       # Langfuse trace identifier
```

**State lifecycle:**

1. Initialized by FastAPI with `query` and `options` fields only.
2. Each node reads its required inputs and writes its outputs. Nodes never modify fields belonging to other nodes.
3. State is serialized to PostgreSQL after every successful node transition as a checkpoint.
4. On error, the router reads the current state and decides whether to retry or terminate.
5. At termination, the response is assembled from either the full `answer` or `partial_answer`.

**Checkpoint strategy:**

- Every node transition triggers a `state_snapshot()` call that writes the full state to the `checkpoints` table.
- On retry, the state is restored from the most recent checkpoint.
- Checkpoints older than 24 hours are pruned by a background task.

---

## 8. Node Specifications

### planner

| Property | Value |
|---|---|
| **Input** | `query`, `options` |
| **Output** | `search_queries`, `subtopics`, `strategy` |
| **LLM prompt strategy** | System prompt instructs GPT-4 to decompose the query into 3-5 targeted search queries. Uses structured JSON output mode (`response_format: { type: "json_object" }`). Few-shot examples in system prompt for consistency. |
| **Error handling** | JSON parse failure triggers re-prompt with original output appended and a stricter schema reminder. Regex fallback extracts any JSON array from raw text. If still invalid, generates a single search query from the raw query string. |
| **Timeout** | 30 seconds |

### searcher

| Property | Value |
|---|---|
| **Input** | `search_queries` |
| **Output** | `search_results`, `raw_docs` |
| **LLM prompt strategy** | No LLM call. Executes Tavily search API for each query in parallel using `asyncio.gather`. Deduplicates results by URL. Extracts full text from top N results (configurable, default 5). |
| **Error handling** | Per-query retry (up to 2 retries with 1s/2s backoff). If Tavily returns empty results for all queries, returns an empty list and logs a warning. Partial results from successful queries are kept. |
| **Timeout** | 45 seconds (20s per Tavily call + extraction) |

### summarizer

| Property | Value |
|---|---|
| **Input** | `search_results`, `raw_docs` |
| **Output** | `summary`, `key_findings` |
| **LLM prompt strategy** | System prompt directs GPT-4 to synthesize findings into a coherent 300-500 word summary with 3-7 key findings as bullet points. Uses `temperature: 0.3` for factual consistency. Prompt includes all raw document text as context. |
| **Error handling** | If the LLM returns an empty or incoherent summary (detected via length check and basic heuristics), the node re-prompts once. On second failure, returns the concatenated raw text as a fallback summary with a warning. |
| **Timeout** | 60 seconds (longest due to large context window) |

### cite

| Property | Value |
|---|---|
| **Input** | `summary`, `key_findings`, `search_results` |
| **Output** | `answer`, `citations` |
| **LLM prompt strategy** | System prompt instructs GPT-4 to insert inline citation markers `[1]`, `[2]`, etc. into the summary text and return a structured JSON object with the annotated answer and a citations array mapping each number to its source URL, title, and snippet. Uses `temperature: 0.1`. |
| **Error handling** | If citation injection fails (malformed JSON or missing citations), falls back to appending a plain citation list at the end of the summary. Never blocks the final response. |
| **Timeout** | 30 seconds |

---

## 9. Security Considerations

**API Keys and Secrets:**

- All secrets are loaded from environment variables. No secrets are hardcoded in source.
- `OPENAI_API_KEY`, `TAVILY_API_KEY`, `LANGFUSE_SECRET_KEY` are never logged, serialized, or returned in API responses.
- Secrets are injected via Docker secrets or a `.env` file with restricted permissions (`chmod 600`).

**Authentication:**

- FastAPI endpoint requires a bearer token validated against a pre-configured hash.
- Rate limiting is applied per-token using a sliding window (default: 20 requests/minute).

**Input Validation:**

- All user input is validated and sanitized by Pydantic models before reaching any node.
- Query length is capped at 5,000 characters.
- Search results are filtered to exclude domains on a configurable blocklist.

**Network Security:**

- All outbound API calls (OpenAI, Tavily) use HTTPS with certificate verification.
- Internal container communication is over an isolated Docker network (`agent-net`). No service is exposed except FastAPI on port 8000 and Langfuse on port 3000 (bound to localhost only in production).

**Data Retention:**

- Checkpoint data in PostgreSQL is automatically purged after 24 hours.
- Langfuse traces are retained for 30 days (configurable).
- No user query data is stored beyond the checkpoint TTL unless explicitly enabled for analytics.

---

## 10. Performance Characteristics

**Expected Latency (per request):**

| Phase | P50 | P95 | P99 |
|---|---|---|---|
| planner | 2.5s | 5.0s | 8.0s |
| searcher | 3.0s | 6.0s | 10.0s |
| summarizer | 4.0s | 8.0s | 14.0s |
| cite | 2.0s | 4.0s | 7.0s |
| **Total** | **11.5s** | **23.0s** | **39.0s** |

**Token Usage (per request):**

| Phase | Prompt Tokens | Completion Tokens |
|---|---|---|
| planner | 800 - 1,200 | 200 - 500 |
| summarizer | 3,000 - 8,000 | 400 - 800 |
| cite | 3,500 - 9,000 | 500 - 1,000 |
| **Total** | **7,300 - 18,200** | **1,100 - 2,300** |

Note: The searcher node does not call the LLM; its token usage is zero.

**Cost Estimate (GPT-4 as of this writing):**

| Component | Cost per Request |
|---|---|
| LLM tokens (avg) | $0.30 - $0.75 |
| Tavily search (avg 3 queries) | $0.015 - $0.045 |
| PostgreSQL writes | negligible |
| Langfuse ingestion | negligible |
| **Total per request** | **$0.32 - $0.80** |

**Throughput:**

- Sustained: 5-10 concurrent requests (bottlenecked by OpenAI rate limits).
- Burst: up to 20 concurrent requests with exponential backoff handling.
- PostgreSQL connection pool: 10 connections (sufficient for state persistence writes).

**Scalability Notes:**

- Horizontal scaling is limited by OpenAI API rate limits. The `OPENAI_API_KEY` tier determines the ceiling.
- PostgreSQL can be replaced with a managed service (e.g., AWS RDS) for higher availability.
- Langfuse can be deployed in a separate cluster for production workloads.
- For sub-10s response times, consider caching frequent queries with a TTL-based Redis layer (not currently implemented).
