"""Graph nodes: planner, searcher, summarizer, cite.

The interesting production bits live here:
- malformed-JSON handling: parse, then re-prompt with an example (up to N retries)
- malformed-JSON fallback: regex extraction of the first {...} block
- per-request token cost tracking against a hard budget cap
"""

import json
import re

from langchain_openai import ChatOpenAI

from .config import settings
from .state import ResearchState
from .tools import tavily_search

# DeepSeek V4 Flash via OpenCode Zen, sold at cost (per 1M tokens)
PRICES = {"input": 0.14, "output": 0.28}

_llm = None


def get_llm() -> ChatOpenAI:
    global _llm
    if _llm is None:
        _llm = ChatOpenAI(
            base_url=settings.llm_base_url,
            api_key=settings.openai_api_key or None,  # type: ignore[arg-type]
            model=settings.llm_model,
            temperature=0,
            max_retries=1,
            timeout=60,
        )
    return _llm


def parse_json(text: str) -> dict | None:
    """Tolerant JSON parse: strips ```fences, falls back to first {...} block."""
    if not text:
        return None
    t = text.strip()
    m = re.search(r"```(?:json)?\s*(.*?)```", t, re.DOTALL)
    if m:
        t = m.group(1)
    try:
        return json.loads(t)
    except json.JSONDecodeError:
        m = re.search(r"\{.*\}", t, re.DOTALL)
        if m:
            try:
                return json.loads(m.group(0))
            except json.JSONDecodeError:
                return None
    return None


def usage_tokens(response) -> tuple[int, int]:
    meta = getattr(response, "usage_metadata", None) or {}
    return meta.get("input_tokens", 0), meta.get("output_tokens", 0)


def add_cost(state: ResearchState, response) -> float:
    """Accumulate token cost into state. Returns new running total."""
    inp, out = usage_tokens(response)
    return (
        state["total_cost"]
        + (inp / 1e6) * PRICES["input"]
        + (out / 1e6) * PRICES["output"]
    )


def call_llm_json(
    messages: list[dict], example: str, state: ResearchState, max_retries: int = 2
):
    """Invoke the LLM expecting JSON; on parse failure, re-prompt with an example.

    Returns (parsed, error). parsed is None after retries are exhausted.
    """
    for attempt in range(max_retries + 1):
        response = get_llm().invoke(messages)
        state["total_cost"] = add_cost(state, response)
        parsed = parse_json(str(response.content or ""))
        if parsed is not None:
            return parsed, None
        if attempt < max_retries:
            # re-prompt: show the raw output and an exact example (expects a str, then a user msg)
            messages = [
                *messages,
                {"role": "user", "content": response.content},
                {
                    "role": "user",
                    "content": f"Return ONLY valid JSON matching this exact shape, no prose:\n{example}",
                },
            ]
    return None, "llm returned malformed JSON after re-prompting"


def planner_node(state: ResearchState) -> dict:
    feedback = ""
    if state["errors"]:
        feedback = (
            f"\nPrevious attempt errors to account for: {'; '.join(state['errors'])}"
        )
    example = '{"sub_questions": ["sub-question 1", "sub-question 2"]}'
    messages = [
        {
            "role": "user",
            "content": (
                "You are a research planner. Break the user's question into at most "
                f"{settings.max_iterations} specific, searchable sub-questions.\n"
                "Return ONLY valid JSON, no prose.\n"
                f"Question: {state['query']}{feedback}"
            ),
        }
    ]
    parsed, err = call_llm_json(messages, example, state)
    if err:
        state["errors"].append(f"planner: {err}")
        plan = [state["query"]]  # graceful degradation: search the raw query
    else:
        plan = parsed.get("sub_questions") or [state["query"]]
        if not isinstance(plan, list):
            plan = [state["query"]]
    return {
        "plan": list(plan)[: settings.max_iterations],
        "total_cost": state["total_cost"],
    }


def searcher_node(state: ResearchState) -> dict:
    found: list[dict] = []
    for q in state["plan"] or [state["query"]]:
        try:
            found.extend(
                tavily_search(
                    q,
                    settings.tavily_api_key,
                    settings.search_depth,
                    settings.max_search_results,
                )
            )
        except Exception as e:
            state["errors"].append(f"searcher: search failed for '{q}': {e}")
    status = "ok" if found or state["status"] == "ok" else "no_results"
    state["iteration"] += 1
    return {
        "results": found,
        "status": status,
        "iteration": state["iteration"],
        "errors": state["errors"],
        "total_cost": state["total_cost"],
    }


def summarizer_node(state: ResearchState) -> dict:
    if not state["results"]:
        # Nothing to summarize: degrade with a clear message instead of pretending.
        return {
            "summary": "No search results were returned, so no summary could be produced.",
            "confidence": "low",
            "status": "no_results",
            "total_cost": state["total_cost"],
        }
    context = "\n".join(
        f"[{i + 1}] {r.get('title', 'untitled')}: {r.get('content', '')[:800]}"
        for i, r in enumerate(state["results"][: settings.max_search_results])
    )
    example = '{"summary": "concise answer text", "confidence": "high|medium|low"}'
    messages = [
        {
            "role": "user",
            "content": (
                "Answer the question using ONLY the search results. Cite sources inline as [1], [2]. "
                "Note uncertainty where the sources are unclear. Return ONLY valid JSON, no prose.\n"
                f"Question: {state['query']}\n\nSearch results:\n{context}"
            ),
        }
    ]
    parsed, err = call_llm_json(messages, example, state)
    if err:
        state["errors"].append(f"summarizer: {err}")
        return {
            "summary": "Summary generation failed after retries. See errors.",
            "confidence": "low",
            "status": "summary_failed",
            "total_cost": state["total_cost"],
        }
    return {
        "summary": str(parsed.get("summary", "")),
        "confidence": str(parsed.get("confidence", "medium")),
        "status": "ok",
        "total_cost": state["total_cost"],
    }


def cite_node(state: ResearchState) -> dict:
    """Extract sources natively from results — no LLM call needed."""
    seen = set()
    citations: list[dict] = []
    for r in state["results"][: settings.max_search_results]:
        url = r.get("url")
        if url and url not in seen:
            seen.add(url)
            citations.append(
                {"index": len(citations) + 1, "title": r.get("title", url), "url": url}
            )
    return {"citations": citations}


def budget_node(state: ResearchState) -> dict:
    """Abort politely when a task exceeds the per-request cost budget."""
    if state["total_cost"] > settings.per_request_budget:
        return {
            "summary": "Task aborted: per-request cost budget exceeded.",
            "confidence": "low",
            "status": "budget_exceeded",
        }
    return {}
