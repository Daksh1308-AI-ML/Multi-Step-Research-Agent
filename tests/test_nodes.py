"""Unit tests for agent.nodes — no network, no keys required."""

import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import agent.nodes  # noqa: E402
from agent.nodes import (
    add_cost,
    budget_node,
    cite_node,
    parse_json,
    planner_node,
    searcher_node,
    summarizer_node,
    usage_tokens,
)  # noqa: E402
from agent.state import ResearchState  # noqa: E402

PLANNER_JSON = '{"sub_questions": ["Q1", "Q2"]}'
SUMMARY_JSON = '{"summary": "A research answer.", "confidence": "high"}'
MALFORMED = "Sorry, I can't do that."


def _state(**overrides) -> ResearchState:
    base = {
        "query": "test query",
        "plan": None,
        "results": [],
        "summary": None,
        "confidence": None,
        "citations": None,
        "errors": [],
        "iteration": 0,
        "total_cost": 0.0,
        "status": "",
    }
    base.update(overrides)
    return base


class FakeLLM:
    def __init__(self, responses=None):
        self._responses = list(responses) if responses else []
        self._calls = 0

    def invoke(self, messages):
        idx = min(self._calls, len(self._responses) - 1)
        self._calls += 1
        return SimpleNamespace(
            content=self._responses[idx],
            usage_metadata={"input_tokens": 100, "output_tokens": 50},
        )


def fake_search(*args, **kwargs):
    return [{"title": "T", "url": "https://t", "content": "c"}]


# --- parse_json ---


def test_parse_json_plain():
    assert parse_json('{"a": 1}') == {"a": 1}


def test_parse_json_fenced():
    assert parse_json('```json\n{"a": 2}\n```') == {"a": 2}


def test_parse_json_prose_wrapped():
    assert parse_json('Sure! Here: {"a": 3} Thanks!') == {"a": 3}


def test_parse_json_garbage():
    assert parse_json("total garbage no braces") is None


def test_parse_json_empty():
    assert parse_json("") is None


def test_parse_json_nested_fenced():
    text = 'blah ```json\n{"key": "val"}\n``` end'
    assert parse_json(text) == {"key": "val"}


def test_parse_json_multiple_objects_greedy_match():
    text = '{"a": 1} and {"b": 2}'
    assert parse_json(text) is None


# --- usage_tokens & add_cost ---


def test_usage_tokens_extracts():
    resp = SimpleNamespace(usage_metadata={"input_tokens": 100, "output_tokens": 200})
    assert usage_tokens(resp) == (100, 200)


def test_usage_tokens_missing_meta():
    assert usage_tokens(SimpleNamespace(usage_metadata=None)) == (0, 0)
    assert usage_tokens(SimpleNamespace()) == (0, 0)


def test_add_cost_accumulates():
    state = _state(total_cost=0.01)
    resp = SimpleNamespace(
        usage_metadata={"input_tokens": 1_000_000, "output_tokens": 1_000_000}
    )
    cost = add_cost(state, resp)
    expected = 0.01 + (1_000_000 / 1e6) * 0.14 + (1_000_000 / 1e6) * 0.28
    assert abs(cost - expected) < 1e-9


# --- planner_node ---


def test_planner_node_valid(monkeypatch):
    monkeypatch.setattr(agent.nodes, "get_llm", lambda: FakeLLM([PLANNER_JSON]))
    state = _state()
    result = planner_node(state)
    assert result["plan"] == ["Q1", "Q2"]


def test_planner_node_malformed_degrades(monkeypatch):
    monkeypatch.setattr(
        agent.nodes, "get_llm", lambda: FakeLLM([MALFORMED, MALFORMED, MALFORMED])
    )
    state = _state()
    result = planner_node(state)
    assert result["plan"] == ["test query"]
    assert any("planner:" in e for e in state["errors"])


def test_planner_node_non_list_sub_questions(monkeypatch):
    monkeypatch.setattr(
        agent.nodes, "get_llm", lambda: FakeLLM(['{"sub_questions": "not a list"}'])
    )
    state = _state()
    result = planner_node(state)
    assert result["plan"] == ["test query"]


# --- searcher_node ---


def test_searcher_node_appends_results(monkeypatch):
    monkeypatch.setattr(agent.nodes, "tavily_search", fake_search)
    state = _state(plan=["Q1", "Q2"])
    result = searcher_node(state)
    assert len(result["results"]) == 2
    assert result["iteration"] == 1
    assert result["status"] == "ok"


def test_searcher_node_tavily_error_records(monkeypatch):
    def bad_search(*a, **kw):
        raise ConnectionError("down")

    monkeypatch.setattr(agent.nodes, "tavily_search", bad_search)
    state = _state(plan=["Q1"])
    result = searcher_node(state)
    assert result["results"] == []
    assert result["status"] == "no_results"
    assert any("searcher:" in e for e in state["errors"])


def test_searcher_node_increments_iteration():
    state = _state(plan=["Q1"], iteration=5)
    import agent.nodes as _m

    orig = _m.tavily_search
    _m.tavily_search = fake_search
    try:
        result = searcher_node(state)
        assert result["iteration"] == 6
    finally:
        _m.tavily_search = orig


# --- summarizer_node ---


def test_summarizer_node_valid(monkeypatch):
    monkeypatch.setattr(agent.nodes, "get_llm", lambda: FakeLLM([SUMMARY_JSON]))
    state = _state(results=[{"title": "T", "url": "U", "content": "C"}])
    result = summarizer_node(state)
    assert result["status"] == "ok"
    assert result["summary"] == "A research answer."
    assert result["confidence"] == "high"


def test_summarizer_node_empty_results():
    state = _state(results=[])
    result = summarizer_node(state)
    assert result["status"] == "no_results"


def test_summarizer_node_llm_failure(monkeypatch):
    monkeypatch.setattr(
        agent.nodes, "get_llm", lambda: FakeLLM([MALFORMED, MALFORMED, MALFORMED])
    )
    state = _state(results=[{"title": "T", "url": "U", "content": "C"}])
    result = summarizer_node(state)
    assert result["status"] == "summary_failed"


# --- cite_node ---


def test_cite_dedup():
    results = [
        {"title": "A", "url": "https://a"},
        {"title": "A2", "url": "https://a"},
        {"title": "B", "url": "https://b"},
    ]
    state = _state(results=results)
    cits = cite_node(state)["citations"]
    assert [c["url"] for c in cits] == ["https://a", "https://b"]


def test_cite_empty():
    state = _state(results=[])
    assert cite_node(state)["citations"] == []


# --- budget_node ---


def test_budget_exceeded():
    assert budget_node({"total_cost": 50.0})["status"] == "budget_exceeded"


def test_budget_under():
    assert budget_node({"total_cost": 0.001}) == {}
