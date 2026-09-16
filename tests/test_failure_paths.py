"""Runnable failure-path tests — no network, no keys required."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from agent.graph import build_graph, route_final  # noqa: E402
from agent.nodes import budget_node, cite_node, parse_json  # noqa: E402
from agent.state import ResearchState  # noqa: E402


def test_graph_has_expected_topology():
    graph = build_graph()
    nodes = set(graph.get_graph().nodes)
    assert {"planner", "searcher", "summarizer", "cite", "budget"}.issubset(nodes)


def test_parse_json_handles_malformed_output():
    assert parse_json('{"a": 1}') == {"a": 1}
    assert parse_json('```json\n{"a": 2}\n```') == {"a": 2}
    # prose around JSON -> first {...} block
    assert parse_json('Sure! Here it is: {"a": 3} Thanks!') == {"a": 3}
    assert parse_json("total garbage") is None
    assert parse_json("") is None


def test_route_final_loops_only_until_max_iterations():
    assert route_final({"status": "ok", "iteration": 1}) == "budget"
    assert route_final({"status": "summary_failed", "iteration": 1}) == "planner"
    assert route_final({"status": "summary_failed", "iteration": 99}) == "budget"
    assert route_final({"status": "no_results", "iteration": 0}) == "budget"


def test_budget_node_aborts_over_cost_cap():
    rich = {"total_cost": 50.0}
    assert budget_node(rich)["status"] == "budget_exceeded"
    cheap = {"total_cost": 0.001}
    assert budget_node(cheap) == {}


def test_cite_extracts_deduplicated_sources():
    results = [
        {"title": "A", "url": "https://a"},
        {"title": "A2", "url": "https://a"},  # dup url
        {"title": "B", "url": "https://b"},
    ]
    state: ResearchState = {"results": results}
    citations = cite_node(state)["citations"]
    assert [c["url"] for c in citations] == ["https://a", "https://b"]
    assert [c["index"] for c in citations] == [1, 2]
