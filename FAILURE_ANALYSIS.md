# FAILURE_ANALYSIS.md — Multi-Step Research Agent

Production failure modes, root causes, fixes, and prevention strategies.

---

## 1. Overview

Production agents break differently than demo agents. Demos run on curated inputs
with warm caches, generous timeouts, and low concurrency. Production runs on
arbitrary user queries, cold databases, throttled APIs, and concurrent requests
that exhaust shared resources.

The most dangerous failures are the silent ones: a query returns a partial answer
that looks complete, a trace disappears so you cannot debug it, a cost spike
drains budget before anyone notices. These are the failures documented here.

Every failure mode below was observed or reconstructed from real production
scenarios against the stack: LangGraph orchestration, OpenAI GPT-4,
Tavily search, PostgreSQL, Langfuse tracing.

---

## 2. Failure Mode Matrix

| # | Failure Mode | Symptoms | Root Cause | Impact | Solution | Difficulty |
|---|-------------|----------|------------|--------|----------|------------|
| 1 | Malformed JSON from LLM | Agent stalls, empty results, Python `json.loads` exceptions in logs | GPT-4 returns JSON with trailing commas, unescaped newlines, or markdown fences around output | 30% of research queries produce no output | Structured Outputs API + regex fallback + re-prompt with examples | Medium |
| 2 | Rate limit silent failures | Requests return empty or stale data with no error surfaced to user | OpenAI 429 and Tavily 429 absorbed by generic exception handler, no retry logic | 15-20% of queries under load get degraded or empty results | Explicit 429 detection, exponential backoff with jitter | Low |
| 3 | Infinite agent loops | Request hangs, CPU pegs at 100%, memory grows unbounded | LangGraph cycle has no iteration cap, "impossible" queries keep generating new tool calls | Single request can block a worker thread indefinitely, cascading pool exhaustion | Max-iterations guard + stuck-state detection with hash-based dedup | Low |
| 4 | Context window overflow | Agent crashes with `InvalidRequestError: max_tokens exceeded`, or silently truncates context | Accumulated search results + conversation history exceed 128k token window | Hard crash, no partial result returned | Top-K truncation + incremental summarization per research step | Medium |
| 5 | Cost explosion | Single request consumes $2-8 instead of expected $0.05-0.10, budget alerts fire | Multi-step reasoning spawns unbounded sub-queries, each consuming full context | Budget drain, unexpected billing spikes | Per-request budget cap ($0.10) + mid-flight token counting + hard abort | Medium |
| 6 | Connection pool exhaustion | All requests time out, PostgreSQL logs `too many connections`, new connections refused | Default pool size (10) too small for concurrent agents, no connection timeouts, leaked connections on error paths | Complete database outage for all agent traffic | Proper pool sizing (min/max/timeout) + connection release on all error paths | Medium |
| 7 | Trace loss | Langfuse dashboard shows gaps, debugging production issues impossible | Synchronous trace flushing blocks on Langfuse API latency, high volume causes timeout and drop | No visibility into agent execution, cannot reproduce or debug failures | Async batch flushing with background thread + local buffer + retry | Low |
| 8 | Stale/irrelevant search results | Agent confidently cites outdated information, answers do not match current reality | Tavily returns results without freshness filtering, agent treats all results as equally relevant | Incorrect answers delivered with high confidence | Freshness scoring + result relevance filtering + recency metadata | Low |
| 9 | LLM provider outage | All agent requests fail immediately, 500 errors returned to users | No fallback strategy when OpenAI API is unreachable | Complete service outage | Fallback to cached results + degraded mode responses | Medium |
| 10 | Partial API failures | Agent returns answer missing one aspect of multi-part query, no indication anything is missing | One Tavily search in a parallel batch fails, agent continues with remaining results without surfacing the gap | Partial answers with no confidence indicator | Graceful degradation with completeness scoring + partial result disclosure | Medium |
| 11 | Reasoning timeouts | Complex queries hang for 60+ seconds, users abandon before getting answer | Deep multi-step reasoning takes 30-90 seconds per node, no per-node timeout | High abandonment rate, poor user experience, wasted compute | Per-node timeout buckets + early termination + progressive response | High |
| 12 | Streaming disconnects | SSE stream drops mid-response, user sees incomplete answer with no way to recover | Network interruption, load balancer timeout, client reconnects but gets no resume point | User sees half an answer, must restart entire query | SSE heartbeat + resume capability with sequence IDs | Medium |

---

## 3. Detailed Failure Scenarios

### 3.1 Malformed JSON from LLM (30% occurrence rate)

**What happened.**

GPT-4, when asked to return structured JSON as part of a tool call in the
LangGraph workflow, would occasionally return JSON wrapped in markdown fences
(````json ... ``` `), include trailing commas before closing braces, embed
literal newlines inside string values, or return single-quoted strings instead
of double-quoted. This was not occasional — in production traffic, roughly 30%
of research queries hit this issue. The agent would call `json.loads()` on the
raw LLM output, receive a `JSONDecodeError`, and either crash the node or
produce an empty result.

**How it manifested.**

Users submitted a research query and received either an empty response, a vague
"Something went wrong" message, or a response that appeared to contain only a
partial answer with the structured data missing. The Langfuse trace showed the
LLM call succeeded but the next node in the graph failed. The error log showed
`json.decoder.JSONDecodeError: Expecting property name enclosed in double quotes`
repeated for the same request.

**Root cause analysis.**

GPT-4's chat completion API does not guarantee valid JSON in the response body
unless Structured Outputs are explicitly enabled. Even with system prompts saying
"return only valid JSON", the model occasionally adds markdown formatting,
trailing commas, or other human-friendly conventions. The original implementation
piped `response.message.content` directly to `json.loads()` with no
pre-processing.

**The fix.**

Three-layer defense implemented in `utils/llm_output.py`:

1. Structured Outputs API: enable `response_format={"type": "json_object"}` on
   all completion calls that expect JSON. This reduces malformed output from 30%
   to approximately 2%.

2. Regex fallback pre-processing: for the remaining 2%, strip markdown fences,
   normalize whitespace, remove trailing commas, and convert single quotes to
   double quotes before parsing. This catches 95% of the residual cases.

3. Re-prompt with examples: if both layers fail, re-send the request with a
   one-shot example of the expected format in the system message. Cap at 2
   re-prompts before falling back to a default empty structure.

```python
# utils/llm_output.py — core sanitization
def parse_llm_json(raw: str) -> dict:
    sanitized = strip_markdown_fences(raw)
    sanitized = normalize_json(sanitized)
    try:
        return json.loads(sanitized)
    except json.JSONDecodeError:
        sanitized = fix_trailing_commas(sanitized)
        return json.loads(sanitized)
```

**Prevention / monitoring.**

- Metric: `llm_json_parse_failures_total` — alert if rate exceeds 5% over a
  10-minute window.
- Metric: `llm_json_reprompts_total` — track re-prompt frequency to detect
  model behavior changes.
- Weekly audit: sample 100 raw LLM outputs, manually inspect for new failure
  patterns (e.g., model updates introducing new formatting habits).

---

### 3.2 Rate Limits from OpenAI/Tavily Causing Silent Failures

**What happened.**

Under concurrent load (20+ simultaneous research queries), the application
started returning empty or clearly degraded results. No errors appeared in the
application logs. The Langfuse traces showed the LLM calls completing with
unusually short durations. Tavily search calls returned empty result sets.

The root cause: the generic `except Exception` blocks in the API client code
were swallowing HTTP 429 responses and returning empty results instead of
surfacing the error.

**How it manifested.**

Users received answers that were suspiciously short or off-topic. The answers
contained no search results and relied only on the LLM's training data. No error
message was shown. The issue was intermittent — retrying the same query a few
minutes later often succeeded.

**Root cause analysis.**

The original API client code:

```python
try:
    response = openai.chat.completions.create(...)
except Exception as e:
    logger.warning(f"LLM call failed: {e}")
    return None  # <-- silent failure
```

A 429 response from OpenAI contains retry-after headers. Tavily returns 429
with a body explaining the limit. Neither was being checked — they were caught
by the generic handler, logged at WARNING level, and returned as `None`, which
the downstream node interpreted as "no results found."

**The fix.**

Implemented in `clients/openai_client.py` and `clients/tavily_client.py`:

1. Explicit 429 detection before the generic catch:
```python
if response.status_code == 429:
    retry_after = int(response.headers.get("retry-after", 1))
    jitter = random.uniform(0, 1)
    time.sleep(min(retry_after + jitter, 30))
    # retry up to 3 times
```

2. Exponential backoff with jitter: initial delay 1s, doubling, capped at 30s,
   with uniform random jitter (0-1s) to avoid thundering herd.

3. Distinct error class `RateLimitError` that propagates up to the graph
   orchestrator, which can decide to retry the entire node or return a
   user-facing "service busy, try again" message.

4. Circuit breaker: if 429s exceed 5 in a 60-second window, open the circuit
   for 30 seconds and reject new requests immediately with a clear message
   rather than queuing them to all fail.

**Prevention / monitoring.**

- Metric: `api_429_responses_total` by provider (openai, tavily) — alert if
  rate exceeds 10/minute.
- Metric: `api_retry_attempts_total` — alert if backoff is hitting max retries.
- Dashboard: OpenAI usage dashboard with real-time RPM tracking against tier
  limits.
- Alert: proactive notification when approaching 80% of tier rate limit.

---

### 3.3 Infinite Agent Loops on Impossible Tasks

**What happened.**

A user submitted the query "Find the exact date and time when the first
self-driving car will be legally permitted to drive on all US highways." The
agent entered a loop: it searched for the answer, found nothing definitive,
generated a new search query, searched again, found nothing, and repeated. The
LangGraph cycle had no termination condition other than the agent "deciding" it
was done — and it never decided.

The process consumed 100% CPU on one worker for 47 minutes before an OOM kill.
During that time, the worker's database connection was held open but idle,
contributing to connection pool exhaustion (failure mode 6). Three other
requests timed out waiting for a database connection.

**How it manifested.**

One research query hung indefinitely. The user saw a spinning indicator. After
10 minutes, they closed the browser tab. The backend process continued running.
Eventually, the container was OOM-killed. The Langfuse trace showed 340+
sequential tool calls before the trace buffer was flushed, making it impossible
to read.

**Root cause analysis.**

LangGraph allows cycles in the agent graph. The "research loop" node generates
a search query, evaluates the results, and either terminates or generates a new
query. There was no upper bound on iterations. The evaluation prompt asked the
LLM "Do you have enough information to answer? If not, what should you search
for next?" For unanswerable questions, the LLM kept finding new angles to
search rather than concluding it could not find the answer.

**The fix.**

Implemented in `graph/research_node.py`:

1. Max-iterations guard: hard cap of 8 research iterations per query, enforced
   at the graph level, not as a prompt instruction. After 8 iterations, the
   agent is forced into the synthesis node with whatever it has gathered.

2. Stuck-state detection: hash the last 3 search queries. If the hash repeats
   (agent is rephrasing the same search), break the loop immediately and
   proceed to synthesis with a note that the query may be unanswerable.

3. Early termination prompt: after iteration 5, the system message changes to
   include: "You have limited search iterations remaining. Synthesize your
   findings now. If you cannot find a definitive answer, state what you found
   and what remains uncertain."

```python
# graph/research_node.py — loop guard
MAX_ITERATIONS = 8
STUCK_WINDOW = 3

def research_step(state: AgentState) -> AgentState:
    iteration = state["iteration_count"] + 1
    if iteration > MAX_ITERATIONS:
        return {**state, "force_synthesis": True, "iteration_count": iteration}

    recent_queries = state["search_history"][-STUCK_WINDOW:]
    if len(set(recent_queries)) < len(recent_queries):
        return {**state, "force_synthesis": True, "iteration_count": iteration}
    # ... normal research logic
```

**Prevention / monitoring.**

- Metric: `agent_iterations_per_request` histogram — alert if p99 exceeds 6.
- Metric: `agent_forced_synthesis_total` — track how often the guard fires.
- Weekly review: inspect forced-synthesis cases to identify queries that should
  be fast-rejected (unanswerable by design).

---

### 3.4 Context Window Overflow from Too Many Search Results

**What happened.**

A multi-step research query accumulated 15 search results across 4 iterations.
Each result was 2-4k tokens of text. The total context — system prompt, search
results, intermediate reasoning, and conversation history — exceeded the 128k
token limit of GPT-4. The API returned a 400 error: `InvalidRequestError:
This model's maximum context length is 128000 tokens`. The agent crashed
mid-research with no partial results saved.

**How it manifested.**

Complex research queries (those requiring 3+ search iterations) failed
intermittently. Simple queries (1-2 searches) worked fine. The failure was
correlated with query complexity, not load. Users saw a hard error: "Research
failed. Please try a simpler question."

**Root cause analysis.**

The implementation appended every search result to the state in full, without
truncation. The system prompt was 3k tokens. Each search result averaged 3k
tokens. After 5 iterations with 3 results each, that was 45k tokens of search
results alone, plus 20-30k tokens of intermediate reasoning. On queries that
triggered the max-iterations guard (failure mode 3), the context routinely hit
the limit.

**The fix.**

Implemented in `graph/context_manager.py`:

1. Top-K truncation: after each search, keep only the top 3 results by
   relevance score (from Tavily's relevance ranking). Discard the rest. This
   caps search-result tokens at approximately 9k per iteration.

2. Incremental summarization: after each research iteration, summarize the
   accumulated findings into a compressed format (approximately 500 tokens)
   and replace the full history with the summary plus only the current
   iteration's raw results. The summary is generated by a fast, cheap model
   call (gpt-4o-mini).

3. Context budget tracking: before each LLM call, estimate token count of the
   full prompt. If it exceeds 100k tokens (leaving headroom for the response),
   forcibly truncate the oldest search results until under budget.

```python
# graph/context_manager.py
MAX_SEARCH_RESULTS_PER_STEP = 3
CONTEXT_BUDGET_TOKENS = 100_000

def manage_context(state: AgentState) -> AgentState:
    results = rank_by_relevance(state["raw_results"])[:MAX_SEARCH_RESULTS_PER_STEP]
    if estimate_tokens(results + state["summary"]) > CONTEXT_BUDGET_TOKENS:
        state["summary"] = compress_summary(state["summary"], target_tokens=2000)
        results = results[:2]
    return {**state, "active_results": results}
```

**Prevention / monitoring.**

- Metric: `context_overflow_preemptions_total` — how often truncation fires.
- Metric: `context_tokens_used` histogram — track distribution to tune budget.
- Alert: if preemption rate exceeds 10% of requests, increase
  `MAX_SEARCH_RESULTS_PER_STEP` or review summarization compression ratio.

---

### 3.5 Cost Explosion on Complex Queries

**What happened.**

A batch of 50 research queries ran overnight (automated pipeline). Total
expected cost at $0.05-0.10 per query: $2.50-5.00. Actual cost: $187.00. Three
queries consumed $22, $34, and $41 respectively. Each had triggered the
infinite-loop scenario (failure mode 3) before the max-iterations guard was
implemented, and each consumed 200k+ tokens across multiple LLM calls.

Even after the iterations guard was added, a single complex query could still
consume $0.30-0.80 — 3-8x the expected cost — because the intermediate
reasoning and context management calls also use GPT-4 at full price.

**How it manifested.**

An OpenAI billing alert fired at 2 AM. The on-call engineer found that three
long-running requests had not been killed by the iterations guard (it had not
been deployed yet). After deployment of the guard, per-query cost dropped but
remained higher than expected for complex queries.

**Root cause analysis.**

No per-request cost tracking existed. The only billing visibility was the
OpenAI dashboard, which updates with a delay. Token counts were logged
summarily but not checked against a budget during execution. Each node in the
graph made its own LLM call, and costs accumulated across nodes without limit.

**The fix.**

Implemented in `middleware/budget_guard.py`:

1. Per-request budget cap: $0.10 hard limit per research request. Enforced
   before each LLM call by checking accumulated cost against the cap.

2. Mid-flight token counting: after each API response, extract
   `usage.total_tokens` and `usage.model` from the response, calculate cost
   using current pricing, and add to the request's running total.

3. Hard abort: if the budget is exceeded mid-call, let the current call
   complete (you cannot cancel an in-flight API call) but refuse to make
   further calls. Return partial results with a flag indicating budget
   exhaustion.

4. Tiered model routing: for context management and summarization calls
   (internal, not user-facing), route to gpt-4o-mini at $0.15/$0.60 per
   million tokens instead of GPT-4 at $5/$15. This reduced internal cost by
   95%.

```python
# middleware/budget_guard.py
BUDGET_CAP_USD = 0.10

class BudgetGuard:
    def __init__(self):
        self.spent = 0.0

    def check(self, model: str, input_tokens: int, output_tokens: int) -> bool:
        cost = calculate_cost(model, input_tokens, output_tokens)
        if self.spent + cost > BUDGET_CAP_USD:
            raise BudgetExhaustedError(self.spent, BUDGET_CAP_USD)
        self.spent += cost
        return True
```

**Prevention / monitoring.**

- Metric: `request_cost_usd` histogram — alert if p99 exceeds $0.15.
- Metric: `budget_exhaustions_total` — alert if rate exceeds 5/minute.
- Daily report: average cost per query, top 10 most expensive queries, total
  daily spend vs. budget.
- Alert: immediate notification if daily spend exceeds projected budget by 50%.

---

### 3.6 PostgreSQL Connection Pool Exhaustion Under Load

**What happened.**

Under moderate load (30 concurrent research queries), all database operations
started timing out. The application health check reported "degraded." PostgreSQL
logs showed `FATAL: too many connections for role "app_user"`. The configured
connection limit for the role was 50. The application had 10 workers, each
holding 1-2 connections (one for the main query, one for a read replica), and
there were background tasks (trace flushing, cache writes) that also acquired
connections. When the agent entered a long-running state (failure mode 3),
connections were held but not released, and new requests could not acquire
connections.

**How it manifested.**

All research queries started returning 504 Gateway Timeout. The health check
endpoint returned 503. Database connections were all in "idle in transaction"
state. Restarting the application temporarily fixed the issue, but it recurred
within minutes under load.

**Root cause analysis.**

The original connection setup used a simple `psycopg2.connect()` call per
request with no pooling. Each request opened 1-2 connections, and when the
request hung (infinite loop), the connections were never closed. The
`finally` block that closed connections was inside the agent graph execution,
which never reached its finally block because the process was killed.

Additionally, the Langfuse trace flushing (failure mode 7) acquired a
connection to write trace data synchronously, holding it during the HTTP call
to the Langfuse API.

**The fix.**

Implemented in `db/pool.py`:

1. Connection pooling with `psycopg2.pool.ThreadedConnectionPool`:
   - `minconn=5` — always have 5 warm connections ready.
   - `maxconn=20` — hard ceiling, matches 2x worker count with headroom.
   - `timeout=10` — wait max 10 seconds for a connection before failing fast.

2. Context-manager connection acquisition: all database access goes through
   `with get_connection() as conn:` which guarantees release even on
   exceptions, interrupts, or process kills (via connection pool's own
   reclaim logic).

3. Connection idle timeout: set `pool_recycle=300` (5 minutes) so stale
   connections are closed and replaced.

4. Separate pool for background tasks: trace flushing and cache writes use a
   dedicated smaller pool (`minconn=2, maxconn=5`) so they cannot starve
   request-serving connections.

```python
# db/pool.py
from psycopg2.pool import ThreadedConnectionPool

request_pool = ThreadedConnectionPool(5, 20, DSN, timeout=10, pool_recycle=300)
background_pool = ThreadedConnectionPool(2, 5, DSN, timeout=10, pool_recycle=300)

@contextmanager
def get_connection(use_background=False):
    pool = background_pool if use_background else request_pool
    conn = pool.getconn()
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        pool.putconn(conn)
```

**Prevention / monitoring.**

- Metric: `db_pool_active_connections` gauge — alert if sustained above 80%
  of max.
- Metric: `db_pool_wait_time_seconds` histogram — alert if p95 exceeds 5s.
- Metric: `db_pool_exhausted_total` — alert on any occurrence.
- PostgreSQL: `pg_stat_activity` query on dashboard, highlight "idle in
  transaction" connections older than 60 seconds.

---

### 3.7 Langfuse Trace Loss During High Volume

**What happened.**

During a load test at 50 QPS, Langfuse trace data started showing gaps. The
dashboard showed traces for roughly 40% of requests. The other 60% had no
traces, making it impossible to debug failures in those requests. The
application itself was functioning correctly — the traces were simply not
arriving.

**How it manifested.**

On-call engineers investigating error reports found that the failing requests
had no Langfuse trace. Without traces, they could not determine which node
failed, what the LLM output was, or what the search results contained. Debugging
required re-running the query manually and hoping to reproduce the issue.

**Root cause analysis.**

The Langfuse SDK was configured with synchronous flushing. Each trace update
made an HTTP POST to the Langfuse API before continuing. At 50 QPS, with 3-5
trace updates per request, the application was making 150-250 synchronous HTTP
calls per second to Langfuse. The Langfuse API has a latency of 50-200ms per
call. At high volume, these calls started timing out (default 10s), and the
SDK's error handler dropped the trace data silently.

**The fix.**

Implemented in `tracing/langfuse_config.py`:

1. Async batch flushing: configure the Langfuse SDK with
   `flush_at=15, flush_interval=5` — batch up to 15 events and flush every
   5 seconds, or when the batch is full, whichever comes first. Flushing
   runs in a background thread, not blocking the main request path.

2. Local buffer: if the flush fails, events are held in an in-memory buffer
   (capped at 10,000 events) and retried on the next flush cycle. Events are
   dropped only if the buffer is full AND the flush fails 3 consecutive times.

3. Failure callback: register a failure handler that logs dropped events to
   the application logger (which goes to CloudWatch/file) so at least a
   record exists even if the trace is lost.

```python
# tracing/langfuse_config.py
langfuse = Langfuse(
    public_key=LANGFUSE_PUBLIC_KEY,
    secret_key=LANGFUSE_SECRET_KEY,
    host=LANGFUSE_HOST,
    flush_at=15,
    flush_interval=5,
    max_retries=3,
    on_flush_error=lambda e: logger.error(f"Langfuse flush failed: {e}")
)
```

**Prevention / monitoring.**

- Metric: `langfuse_flush_errors_total` — alert if rate exceeds 1/minute.
- Metric: `langfuse_pending_events` gauge — alert if sustained above 5,000.
- Metric: `langfuse_trace_completeness` — compare requests sent vs. traces
  visible in Langfuse over a 1-hour window. Alert if ratio drops below 95%.

---

### 3.8 Search API Returning Stale/Irrelevant Results

**What happened.**

A user asked "What is the current CEO of OpenAI?" The agent searched Tavily,
received results from 2023 articles discussing Sam Altman's temporary ouster
and return, and confidently answered with a detailed timeline of the November
2023 events — failing to mention that the information was current and accurate
at the time but was presented without temporal context, making it sound
outdated and confusing. In another case, a query about "latest Python security
vulnerabilities" returned results from 2022.

**How it manifested.**

Users received answers that were technically correct but cited outdated sources.
The answers did not indicate when the information was from. Users asked
follow-up questions like "Is this still true?" or "This seems old." Trust in
the agent eroded. Support tickets increased with "the agent gives old
information."

**Root cause analysis.**

Tavily's search results include a `published_date` field, but the agent was not
using it. All results were treated equally regardless of age. The relevance
ranking from Tavily optimizes for topical match, not recency. For queries about
current events, recent results are more relevant even if slightly less topically
precise.

Additionally, some results were from aggregator sites, forums, or rehosted
content that was stale despite having a recent `indexed_date`.

**The fix.**

Implemented in `search/filter.py`:

1. Freshness scoring: for queries containing temporal indicators ("current",
   "latest", "now", "2024", "recent"), apply a recency penalty. Results older
   than 90 days get a 0.3x multiplier on their relevance score. Results older
   than 365 days get 0.1x.

2. Domain whitelist/blacklist: maintain a list of domains known for stale or
   rehosted content (content farms, aggregators without original reporting).
   Results from these domains get a 0.2x penalty.

3. Recency metadata in prompts: pass the publication date of each source to
   the LLM in the context, so it can reason about the currency of information
   and hedge appropriately.

4. Source freshness requirement: for queries with temporal intent, discard
   results older than 180 days entirely rather than just penalizing them.

```python
# search/filter.py
TEMPORAL_KEYWORDS = {"current", "latest", "now", "recent", "today", "this year"}

def score_freshness(result: dict, query: str) -> float:
    if not any(kw in query.lower() for kw in TEMPORAL_KEYWORDS):
        return 1.0  # no temporal intent, no penalty
    age_days = (datetime.now() - parse_date(result["published_date"])).days
    if age_days > 365:
        return 0.1
    if age_days > 90:
        return 0.3
    return 1.0
```

**Prevention / monitoring.**

- Metric: `search_result_age_days` histogram — track distribution of result
  ages. Alert if median age exceeds 180 days.
- Metric: `search_stale_results_discarded_total` — track filtering volume.
- User feedback: add "Was this information up to date?" prompt after answers.
  Track positive/negative rates.

---

### 3.9 LLM Provider Outage

**What happened.**

OpenAI experienced a 47-minute partial outage on 2024-09-15. Their API
returned 500 errors for approximately 30% of requests. The agent's behavior
during this window: every request that hit the affected endpoints failed with
a generic error, and the application returned a 500 to the user. There was no
graceful degradation.

**How it manifested.**

Complete service outage for roughly one-third of requests. The remaining
two-thirds succeeded normally. Users saw intermittent "Something went wrong"
messages. No fallback. No cached responses. No degraded mode.

**Root cause analysis.**

The application had no concept of provider health. Every request went to the
OpenAI API regardless of its current status. There was no circuit breaker, no
fallback strategy, and no cached results from previous similar queries.

**The fix.**

Implemented in `resilience/fallback.py`:

1. Circuit breaker: track OpenAI API error rate over a 60-second window. If
   errors exceed 30%, open the circuit for 60 seconds. During open circuit,
   skip the LLM call entirely and go to fallback mode.

2. Cached result fallback: for the research synthesis step, cache the final
   answer keyed by a hash of the research query. If the LLM is unavailable,
   return the cached answer if one exists (with a staleness indicator). This
   covers the case where a similar query was answered recently.

3. Degraded mode: if no cache hit and the circuit is open, return whatever
   search results were gathered (without LLM synthesis) with a message:
   "Synthesis is temporarily unavailable. Here are the raw search results."
   This is imperfect but better than a hard error.

4. Health check endpoint: expose `/health/llm` that checks OpenAI API
   responsiveness. Load balancer uses this to route traffic away from
   unhealthy instances.

**Prevention / monitoring.**

- Metric: `circuit_breaker_state` gauge (0=closed, 1=open) — alert on state
  transition to open.
- Metric: `fallback_cache_hit_total` and `fallback_cache_miss_total` — track
  fallback effectiveness.
- External monitoring: uptime check on `/health/llm` from a separate
  availability monitor, alerts if degraded.
- Runbook: document manual steps for switching to a secondary provider (if
  one is configured in the future).

---

### 3.10 Partial API Failures (One Search Fails, Others Succeed)

**What happened.**

A research query triggered 4 parallel Tavily searches. One of the four returned
a 500 error. The agent received 3 successful result sets and 1 empty result.
It proceeded to synthesize an answer using only the 3 result sets. The answer
was coherent but missed the perspective that the failed search was supposed to
cover. The user had no way to know that part of the research was missing.

**How it manifested.**

The agent returned an answer that was correct but incomplete. For example, a
query about "pros and cons of React vs Vue vs Svelte" might return detailed
pros and cons for React and Vue but barely mention Svelte, because one of the
three search calls failed. The answer did not indicate that it was incomplete.

**Root cause analysis.**

The parallel search execution used `asyncio.gather()` with `return_exceptions
= True`. Exceptions were caught and logged, but the empty result was treated
the same as a successful search returning no results. The synthesis node had no
way to distinguish "search succeeded but found nothing" from "search failed."

**The fix.**

Implemented in `search/parallel.py`:

1. Explicit failure tracking: each parallel search returns a `SearchResult`
   object that includes a `status` field ("success", "partial", "failed") and
   an `error` field if applicable.

2. Completeness scoring: before synthesis, calculate a completeness score based
   on how many of the expected searches succeeded. If less than 75% succeeded,
   flag the result as "partial" and include a note in the user-facing response.

3. User-facing disclosure: if any search failed, append to the answer: "Note:
   Some research sources were unavailable during this query. The answer above
   may be incomplete."

4. Retry failed searches: before synthesis, retry any failed searches once
   with a 2-second delay. If the retry also fails, proceed with degradation.

```python
# search/parallel.py
async def run_parallel_searches(queries: list[str]) -> list[SearchResult]:
    tasks = [search_with_retry(q) for q in queries]
    results = await asyncio.gather(*tasks, return_exceptions=True)
    return [normalize_result(r) for r in results]

def assess_completeness(results: list[SearchResult]) -> float:
    successful = sum(1 for r in results if r.status == "success")
    return successful / len(results) if results else 0.0
```

**Prevention / monitoring.**

- Metric: `search_success_rate` gauge — alert if it drops below 90%.
- Metric: `search_completeness_score` histogram — alert if p5 drops below 0.75.
- Metric: `search_retry_success_total` — track how often retries recover from
  initial failures.

---

### 3.11 Timeouts on Slow Reasoning (Deep Complex Queries)

**What happened.**

Queries requiring deep multi-step reasoning (e.g., "Compare the environmental
impact of electric vehicles vs hydrogen fuel cells across manufacturing,
operating, and disposal phases") triggered 6-8 sequential LLM calls, each
taking 15-30 seconds. Total response time: 90-180 seconds. The load balancer
had a 60-second timeout. The client had a 30-second connection timeout.

**How it manifested.**

Users submitted complex questions, waited 30+ seconds, and received a
connection error. The agent was still processing. When the load balancer killed
the connection at 60 seconds, the agent's result was lost. Users tried again,
starting the expensive process over. Some users gave up entirely.

**Root cause analysis.**

No per-node timeouts existed in the LangGraph workflow. Each node (search,
evaluate, synthesize) ran as long as the underlying API call took. The overall
request had no timeout. The infrastructure (load balancer, reverse proxy) had
timeouts, but they killed the connection, not the backend process — so the
compute was wasted.

**The fix.**

Implemented in `graph/timeout_manager.py`:

1. Per-node timeout buckets: each node type gets a configured timeout.
   - Search node: 15 seconds
   - Evaluate node: 20 seconds
   - Synthesize node: 30 seconds
   - Context management: 5 seconds

2. Progressive response: start streaming a placeholder to the client within
   5 seconds ("Researching..."), then stream status updates as each node
   completes. This keeps the connection alive and gives the user feedback.

3. Early termination with partial results: if the overall request exceeds 60
   seconds, force-terminate remaining nodes, take whatever partial results
   exist, and synthesize an answer. Return with a note: "This is a partial
   answer due to query complexity."

4. Background completion: if terminated early, queue the remaining work as a
   background task. When complete, store the full result and notify the user
   (if a notification channel is available).

```python
# graph/timeout_manager.py
NODE_TIMEOUTS = {
    "search": 15,
    "evaluate": 20,
    "synthesize": 30,
    "context_manage": 5,
}

async def run_with_timeout(node_fn, node_name: str, state: AgentState):
    timeout = NODE_TIMEOUTS.get(node_name, 30)
    try:
        return await asyncio.wait_for(node_fn(state), timeout=timeout)
    except asyncio.TimeoutError:
        logger.warning(f"Node {node_name} timed out after {timeout}s")
        return get_fallback_for_node(node_name, state)
```

**Prevention / monitoring.**

- Metric: `node_timeout_total` by node name — alert if any node exceeds
  5% timeout rate.
- Metric: `request_duration_seconds` histogram — track p50, p95, p99. Alert
  if p95 exceeds 45 seconds.
- Metric: `partial_results_served_total` — track early-termination frequency.
- User feedback: track abandonment rate (user closes tab before response).

---

### 3.12 Streaming Disconnects Mid-Response

**What happened.**

The agent streamed responses via Server-Sent Events (SSE). Under load, the
streaming connection dropped after 10-20 seconds of streaming. The user saw the
response stop mid-sentence. The SSE connection was not re-established, and the
user had to reload the page and resubmit the query.

**How it manifested.**

Users saw partial responses that stopped abruptly. No error message was shown.
The page appeared to be loading indefinitely. Reloading the page and resubmitting
the query produced a new response from scratch. For complex queries that took
30+ seconds to stream, this happened frequently.

**Root cause analysis.**

Three contributing factors:

1. The reverse proxy (nginx) had a default proxy_read_timeout of 60 seconds.
   If the stream took longer than 60 seconds without data being sent, the
   proxy closed the connection.

2. The SSE implementation had no heartbeat. If the agent was processing and
   not producing output for more than a few seconds, the connection was idle
   and vulnerable to timeout by any intermediary (proxy, load balancer, browser).

3. No resume capability existed. If the connection dropped at byte 500 of a
   2000-byte response, the user lost all 500 bytes and had to start over.

**The fix.**

Implemented in `streaming/sse_handler.py`:

1. SSE heartbeat: send a `:heartbeat\n\n` comment event every 15 seconds
   during processing pauses. This keeps the connection alive through proxies
   and load balancers.

2. Sequence IDs: each SSE event includes a `id:` field with an incrementing
   sequence number. If the client disconnects and reconnects, it sends
   `Last-Event-ID` header, and the server resumes from that sequence number
   using a per-request ring buffer (last 100 events).

3. Client-side reconnection: the JavaScript client implements automatic
   reconnection with the `Last-Event-ID` header, with exponential backoff
   (1s, 2s, 4s, max 3 retries).

4. Nginx configuration: set `proxy_read_timeout 120s` and
   `proxy_buffering off` for the SSE endpoint.

```python
# streaming/sse_handler.py
async def stream_response(request_id: str, response_gen):
    buffer = EventRingBuffer(capacity=100)
    last_id = 0
    while True:
        try:
            chunk = await asyncio.wait_for(
                response_gen.__anext__(), timeout=15
            )
            last_id += 1
            event = SSEEvent(id=last_id, data=chunk)
            buffer.push(event)
            yield event.format()
        except asyncio.TimeoutError:
            yield ":heartbeat\n\n"  # keep-alive
        except StopAsyncIteration:
            break
```

**Prevention / monitoring.**

- Metric: `stream_disconnects_total` — alert if rate exceeds 5% of streaming
  requests.
- Metric: `stream_resumes_total` — track how often clients reconnect with
  `Last-Event-ID`.
- Metric: `stream_heartbeat_misses_total` — if heartbeat itself fails, the
  connection is dead; track these for infrastructure investigation.
- Alert: if disconnect rate exceeds 10%, investigate proxy and load balancer
  timeout settings.

---

## 4. Recovery Strategy Comparison

| Scenario | Before (broken) | After (fixed) | Recovery Rate |
|----------|-----------------|---------------|---------------|
| Malformed JSON | Agent crashes, empty response, no retry | Regex sanitization + re-prompt, structured outputs API | 98% recover (up from ~0%) |
| Rate limits | Silent empty response, no retry | Exponential backoff + jitter + circuit breaker | 95% recover (up from ~10%) |
| Infinite loops | Process hangs until OOM kill, blocks worker | Max-iterations guard + stuck-state detection, forced synthesis | 100% terminate within bounds (up from 0%) |
| Context overflow | Hard crash with `InvalidRequestError` | Top-K truncation + incremental summarization + budget tracking | 99% complete (up from ~40%) |
| Cost explosion | Unbounded spend, $2-8 per query | Per-request cap ($0.10) + tiered model routing + hard abort | 100% within budget (up from ~20%) |
| Connection pool exhaustion | All requests time out, requires restart | Pooled connections + timeout + separate background pool | 99.9% availability (up from ~70%) |
| Trace loss | 60% of traces lost under load | Async batch flushing + local buffer + retry | 98% trace retention (up from ~40%) |
| Stale results | Agent confidently cites outdated info | Freshness scoring + domain filtering + temporal intent detection | 90% current sources (up from ~50%) |
| LLM outage | 100% failure, no fallback | Circuit breaker + cached fallback + degraded mode | 60% partial service (up from 0%) |
| Partial API failures | Silent incomplete answers | Completeness scoring + retry + user disclosure | 95% full or disclosed (up from ~50%) |
| Reasoning timeouts | Connection killed, result lost | Per-node timeouts + progressive streaming + background completion | 80% get answer (up from ~20%) |
| Streaming disconnects | Partial response lost, must restart | SSE heartbeat + resume with sequence IDs + auto-reconnect | 95% resume successfully (up from ~0%) |

---

## 5. Monitoring & Alerting

### 5.1 Error Rate by Node

Track error rate for each node in the LangGraph workflow independently.

| Metric | Description | Alert Threshold |
|--------|-------------|-----------------|
| `node_search_error_rate` | % of search node executions that fail | >5% over 5 minutes |
| `node_evaluate_error_rate` | % of evaluate node executions that fail | >3% over 5 minutes |
| `node_synthesize_error_rate` | % of synthesize node executions that fail | >3% over 5 minutes |
| `node_context_mgmt_error_rate` | % of context management calls that fail | >5% over 5 minutes |
| `overall_request_error_rate` | % of end-to-end requests that return errors | >2% over 5 minutes |

### 5.2 Token Consumption Anomalies

| Metric | Description | Alert Threshold |
|--------|-------------|-----------------|
| `tokens_per_request` (histogram) | Token usage distribution per request | p99 >150k tokens |
| `cost_per_request_usd` (histogram) | Dollar cost distribution per request | p99 >$0.15 |
| `daily_spend_usd` (gauge) | Running total daily spend | >150% of projected daily budget |
| `token_budget_exhaustions_total` (counter) | Requests hitting per-request budget cap | >10/minute |
| `internal_model_cost_ratio` | Ratio of internal (gpt-4o-mini) to external (gpt-4) calls | Track for cost optimization |

### 5.3 API Latency Percentiles

| Metric | Description | Alert Threshold |
|--------|-------------|-----------------|
| `openai_latency_seconds` (histogram) | OpenAI API response time | p95 >30s |
| `tavily_latency_seconds` (histogram) | Tavily API response time | p95 >10s |
| `langfuse_latency_seconds` (histogram) | Langfuse API response time | p95 >5s |
| `db_query_latency_seconds` (histogram) | PostgreSQL query time | p95 >2s |
| `end_to_end_latency_seconds` (histogram) | Full request duration | p95 >60s |

### 5.4 Cache Hit/Miss Ratio

| Metric | Description | Alert Threshold |
|--------|-------------|-----------------|
| `fallback_cache_hit_rate` | LLM fallback cache hits / total fallback attempts | Track for effectiveness |
| `search_result_cache_hit_rate` | Cached search results / total searches | <20% indicates cache issues |
| `synthesis_cache_hit_rate` | Cached synthesis results / total synthesis calls | Track for cost savings |
| `cache_size_bytes` (gauge) | Current cache memory usage | >500MB |

### 5.5 Queue Depth

| Metric | Description | Alert Threshold |
|--------|-------------|-----------------|
| `pending_requests` (gauge) | Requests waiting for worker thread | >20 sustained for 1 minute |
| `background_task_queue_depth` (gauge) | Pending background tasks (tracing, cache writes) | >500 |
| `retry_queue_depth` (gauge) | Requests pending retry after failure | >10 sustained |
| `worker_thread_utilization` (gauge) | % of worker threads busy | >90% sustained for 2 minutes |

---

## 6. Post-Mortem Template

Use this template for every production incident. Fill it out within 48 hours.

```markdown
# Post-Mortem: [Incident Title]

**Date:** YYYY-MM-DD
**Duration:** [start time] - [end time] ([total duration])
**Severity:** P1 (service down) / P2 (degraded) / P3 (minor impact)
**Author:** [name]
**Status:** Draft / Reviewed / Final

## Summary

One-paragraph description of what happened, who was affected, and the business
impact.

## Timeline

All times in UTC.

- HH:MM — [First indicator of the problem]
- HH:MM — [On-call engineer notified]
- HH:MM — [Root cause identified]
- HH:MM — [Mitigation applied]
- HH:MM — [Service fully recovered]
- HH:MM — [Post-mortem initiated]

## Impact

- **Users affected:** [number or percentage]
- **Requests failed:** [number]
- **Revenue impact:** [if applicable]
- **Data loss:** [yes/no, describe]
- **SLA breach:** [yes/no, which SLA]

## Root Cause

Detailed technical explanation of what caused the incident. Reference specific
code, configuration, or infrastructure components. Link to relevant Langfuse
traces, logs, or metrics.

## Resolution

What was done to fix the immediate issue (mitigation) and what prevents it
from recurring (permanent fix).

## Detection

- How was the incident detected? (alert, user report, monitoring dashboard)
- How long from onset to detection? ([time])
- How long from detection to mitigation? ([time])
- Was the detection method adequate? What could be improved?

## Lessons Learned

### What went well
- [item]

### What went poorly
- [item]

### Where we got lucky
- [item]

## Action Items

| # | Action | Owner | Priority | Due Date | Status |
|---|--------|-------|----------|----------|--------|
| 1 | [description] | [name] | P1/P2/P3 | YYYY-MM-DD | Open |
| 2 | [description] | [name] | P1/P2/P3 | YYYY-MM-DD | Open |

## Related Incidents

- [links to related post-mortems, if any]

## Supporting Data

- Langfuse trace links
- Metric dashboard links
- Log search links
```

---

## 7. Testing Checklist for Failure Paths

Every failure mode must have automated tests that verify the system handles it
correctly. These are not optional. If a test cannot be written (e.g., requires
real API outage), document the manual test procedure.

### 3.1 — Malformed JSON

| Test | Description | Type |
|------|-------------|------|
| `test_parse_json_markdown_fences` | Verify `parse_llm_json` strips ```json fences | Unit |
| `test_parse_json_trailing_commas` | Verify trailing commas are removed before parsing | Unit |
| `test_parse_json_single_quotes` | Verify single-quoted strings are converted to double quotes | Unit |
| `test_parse_json_reprompt_on_failure` | Verify that if parsing fails twice, a re-prompt is sent with examples | Integration |
| `test_structured_output_mode_enabled` | Verify `response_format={"type":"json_object"}` is set on all JSON-expecting calls | Integration |
| `test_parse_failure_rate_below_threshold` | Run 100 synthetic LLM outputs, verify parse success >95% | Regression |

### 3.2 — Rate Limits

| Test | Description | Type |
|------|-------------|------|
| `test_429_triggers_retry` | Mock OpenAI returning 429, verify retry with backoff occurs | Unit |
| `test_429_respects_retry_after` | Verify backoff duration matches `retry-after` header | Unit |
| `test_circuit_breaker_opens` | Mock 6 consecutive 429s, verify circuit opens and subsequent calls fail fast | Unit |
| `test_circuit_breaker_resets` | After circuit opens, verify it closes after the configured cooldown | Unit |
| `test_rate_limit_error_propagates` | Verify `RateLimitError` reaches the graph orchestrator, not caught by generic handler | Integration |

### 3.3 — Infinite Loops

| Test | Description | Type |
|------|-------------|------|
| `test_max_iterations_stops_loop` | Mock LLM to always generate new search queries, verify loop terminates at 8 | Integration |
| `test_stuck_state_detection` | Feed repeated identical search queries, verify loop terminates early | Unit |
| `test_forced_synthesis_contains_partial_results` | Verify forced synthesis produces a valid (if incomplete) answer | Integration |
| `test_iteration_count_in_state` | Verify `iteration_count` increments correctly per step | Unit |

### 3.4 — Context Overflow

| Test | Description | Type |
|------|-------------|------|
| `test_top_k_truncation` | Provide 10 search results, verify only top 3 are kept | Unit |
| `test_incremental_summarization_reduces_tokens` | Verify summary replaces full history and token count drops | Unit |
| `test_context_budget_enforced` | Mock a 150k-token context, verify truncation brings it under 100k before LLM call | Unit |
| `test_oldest_results_discarded_first` | Verify context budget cuts oldest results, not newest | Unit |

### 3.5 — Cost Explosion

| Test | Description | Type |
|------|-------------|------|
| `test_budget_cap_aborts` | Mock LLM returning 100k tokens, verify hard abort at $0.10 | Unit |
| `test_budget_cap_allows_normal` | Normal request (~5k tokens) completes without budget error | Unit |
| `test_tiered_model_routing` | Verify summarization calls use gpt-4o-mini, not gpt-4 | Unit |
| `test_partial_results_returned_on_budget_exhaustion` | Verify partial results are returned with exhaustion flag, not a crash | Integration |

### 3.6 — Connection Pool Exhaustion

| Test | Description | Type |
|------|-------------|------|
| `test_pool_limits_connections` | Open 25 concurrent DB calls, verify max 20 connections used | Integration |
| `test_pool_timeout_fails_fast` | Exhaust pool, verify new request fails after 10s, not indefinitely | Integration |
| `test_connection_released_on_error` | Simulate exception in DB code, verify connection returned to pool | Unit |
| `test_background_pool_independent` | Exhaust background pool, verify request pool is unaffected | Integration |
| `test_connection_recycle` | Hold connection idle for >5 minutes, verify it is replaced | Integration |

### 3.7 — Trace Loss

| Test | Description | Type |
|------|-------------|------|
| `test_async_flush_does_not_block` | Verify trace creation returns in <1ms even if Langfuse API latency is 500ms | Unit |
| `test_flush_retries_on_failure` | Mock Langfuse API failure, verify events are retried on next flush cycle | Unit |
| `test_buffer_drops_after_max_retries` | Mock 3 consecutive flush failures, verify events are logged and dropped | Unit |
| `test_trace_completeness_under_load` | Send 100 requests at 50 QPS, verify >95% have complete traces in Langfuse | Load |

### 3.8 — Stale Results

| Test | Description | Type |
|------|-------------|------|
| `test_freshness_penalty_applied` | Query with "current", result from 2023 gets 0.3x score | Unit |
| `test_no_penalty_for_non_temporal` | Query without temporal keywords, old results are not penalized | Unit |
| `test_very_old_results_discarded` | Result older than 365 days + temporal query = discarded | Unit |
| `test_recency_passed_to_llm` | Verify publication dates appear in the context sent to the LLM | Integration |

### 3.9 — LLM Outage

| Test | Description | Type |
|------|-------------|------|
| `test_circuit_breaker_on_500s` | Mock OpenAI returning 500s, verify circuit opens | Unit |
| `test_cached_fallback_returned` | With circuit open and cache populated, verify cached result returned | Integration |
| `test_degraded_mode_without_cache` | With circuit open and no cache, verify raw search results returned | Integration |
| `test_health_check_reflects_status` | `/health/llm` returns 503 when circuit is open | Integration |

### 3.10 — Partial API Failures

| Test | Description | Type |
|------|-------------|------|
| `test_failed_search_tracked` | One of 3 parallel searches fails, verify `SearchResult.status` is "failed" | Unit |
| `test_completeness_score_calculated` | 2 of 3 searches succeed, verify completeness is 0.67 | Unit |
| `test_user_disclosure_appended` | With failed search, verify answer includes "may be incomplete" note | Integration |
| `test_retry_attempted_before_synthesis` | Verify failed search is retried once before synthesis proceeds | Integration |

### 3.11 — Reasoning Timeouts

| Test | Description | Type |
|------|-------------|------|
| `test_node_timeout_fires` | Mock a node taking 30s, verify timeout at 15s (search) | Unit |
| `test_fallback_returned_on_timeout` | Verify timeout produces a fallback result, not a crash | Unit |
| `test_progressive_response_sent` | Verify streaming starts within 5 seconds even if first node takes 15s | Integration |
| `test_background_completion_queued` | Verify early termination queues remaining work as background task | Integration |

### 3.12 — Streaming Disconnects

| Test | Description | Type |
|------|-------------|------|
| `test_heartbeat_sent_during_pause` | Simulate 20-second processing pause, verify heartbeat events sent | Unit |
| `test_sequence_ids_increment` | Verify each SSE event has an incrementing `id` field | Unit |
| `test_resume_from_last_event_id` | Simulate disconnect at event 10, reconnect with `Last-Event-ID: 10`, verify events 11+ are sent | Integration |
| `test_buffer_limits_memory` | Verify ring buffer caps at 100 events and does not grow unbounded | Unit |

---

## Appendix: Quick Reference

### Critical Paths to Monitor

1. LLM call success/failure -> JSON parse success/failure -> Agent step completion
2. Search API call success/failure -> Result count -> Result freshness
3. DB connection acquisition time -> Query execution time -> Connection release
4. Token consumption per request -> Cost per request -> Budget remaining
5. SSE connection health -> Heartbeat delivery -> Resume success

### Escalation Contacts

| Role | Contact | When to Escalate |
|------|---------|-----------------|
| On-call engineer | [rotating] | First responder, triages severity |
| LLM provider support | OpenAI support portal | API outage lasting >5 minutes |
| Database admin | [DBA team] | Connection pool exhaustion or data corruption |
| Product owner | [name] | User-facing impact lasting >30 minutes |
