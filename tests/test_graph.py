"""Integration-ish tests for agent.graph — no network, no keys required."""

import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import agent.graph  # noqa: E402
import agent.nodes  # noqa: E402
from agent.config import settings  # noqa: E402
from agent.graph import build_graph, run_research  # noqa: E402

PLANNER_JSON = '{"sub_questions": ["Q1", "Q2"]}'
SUMMARY_JSON = '{"summary": "A research answer.", "confidence": "high"}'
SUMMARY_FAILED = "Sorry, I can't do that."


class FakeLLM:
    def __init__(self, responses=None):
        self._responses = list(responses) if responses else []
        self._calls = 0

    def invoke(self, messages):
        prompt = messages[0]["content"]
        idx = min(self._calls, len(self._responses) - 1)
        self._calls += 1
        content = PLANNER_JSON if "planner" in prompt.lower() else self._responses[idx]
        return SimpleNamespace(
            content=content,
            usage_metadata={"input_tokens": 100, "output_tokens": 50},
        )


def fake_search(*args, **kwargs):
    return [{"title": "T", "url": "https://t", "content": "c"}]


def test_build_graph_topology():
    graph = build_graph()
    nodes = set(graph.get_graph().nodes)
    assert {
        "planner",
        "searcher",
        "summarizer",
        "cite",
        "budget",
        "__start__",
        "__end__",
    }.issubset(nodes)


def test_run_research_returns_correct_shape(monkeypatch):
    monkeypatch.setattr(agent.nodes, "get_llm", lambda: FakeLLM([SUMMARY_JSON]))
    monkeypatch.setattr(agent.nodes, "tavily_search", fake_search)
    result = run_research("what is python?")
    assert result["summary"] == "A research answer."
    assert result["citations"]
    assert result["status"] == "ok"
    assert "trace_id" in result


def test_run_research_cost_accumulates(monkeypatch):
    monkeypatch.setattr(agent.nodes, "get_llm", lambda: FakeLLM([SUMMARY_JSON]))
    monkeypatch.setattr(agent.nodes, "tavily_search", fake_search)
    result = run_research("test")
    assert result["total_cost"] > 0


def test_max_iteration_guard(monkeypatch):
    orig_max = settings.max_iterations
    settings.max_iterations = 2

    def always_fail_llm():
        class LLM:
            def invoke(self, messages):
                return SimpleNamespace(
                    content=SUMMARY_FAILED,
                    usage_metadata={"input_tokens": 10, "output_tokens": 5},
                )

        return LLM()

    monkeypatch.setattr(agent.nodes, "get_llm", always_fail_llm)
    monkeypatch.setattr(agent.nodes, "tavily_search", fake_search)
    try:
        result = run_research("loop test")
        assert result["status"] in (
            "summary_failed",
            "budget_exceeded",
            "no_results",
            "ok",
        )
        assert result["iteration"] >= 1
    finally:
        settings.max_iterations = orig_max


def test_no_results_path(monkeypatch):
    monkeypatch.setattr(agent.nodes, "get_llm", lambda: FakeLLM([SUMMARY_JSON]))
    monkeypatch.setattr(agent.nodes, "tavily_search", lambda *a, **kw: [])
    result = run_research("obscure thing")
    assert result["status"] == "no_results"
    assert result["summary"] is not None
