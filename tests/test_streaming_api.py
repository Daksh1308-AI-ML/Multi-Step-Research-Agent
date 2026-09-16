"""SSE streaming + endpoint tests — no network, no keys required."""

import json
import sys
import time
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import agent.graph  # noqa: E402
import agent.nodes  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from agent.config import settings  # noqa: E402
from agent.main import app  # noqa: E402

client = TestClient(app)

PLANNER_JSON = '{"sub_questions": ["Q1", "Q2"]}'
SUMMARY_JSON = '{"summary": "A research answer.", "confidence": "high"}'


class FakeLLM:
    def invoke(self, messages):
        prompt = messages[0]["content"]
        content = PLANNER_JSON if "research planner" in prompt else SUMMARY_JSON
        return SimpleNamespace(
            content=content, usage_metadata={"input_tokens": 10, "output_tokens": 20}
        )


def fake_search(*args, **kwargs):
    return [{"title": "T", "url": "https://t", "content": "c"}]


def sleeping_planner(state):
    time.sleep(0.5)
    return {"plan": [state["query"]], "total_cost": 0.0}


def read_sse(resp):
    events = []
    name = None
    data = []
    for line in resp.iter_lines():
        if line == "":
            events.append((name, json.loads("\n".join(data))))
            name, data = None, []
        elif line.startswith("event: "):
            name = line[len("event: ") :]
        elif line.startswith("data: "):
            data.append(line[len("data: ") :])
    return events


def test_stream_emits_status_and_complete(monkeypatch):
    monkeypatch.setattr(agent.nodes, "get_llm", FakeLLM)
    monkeypatch.setattr(agent.nodes, "tavily_search", fake_search)
    resp = client.post("/research/stream", json={"query": "test"})
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/event-stream")
    events = read_sse(resp)
    names = [n for n, _ in events]
    assert "status" in names
    assert "complete" in names
    complete = dict(events)["complete"]
    assert complete["citations"] and complete["status"] == "ok"


def test_stream_emits_error_on_timeout(monkeypatch):
    monkeypatch.setattr(settings, "request_timeout", 0.001)
    monkeypatch.setattr(agent.graph, "planner_node", sleeping_planner)
    monkeypatch.setattr(agent.nodes, "get_llm", FakeLLM)
    monkeypatch.setattr(agent.nodes, "tavily_search", fake_search)
    resp = client.post("/research/stream", json={"query": "test"})
    events = read_sse(resp)
    names = [n for n, _ in events]
    assert names[-1] == "error"
    assert "timed out after" in events[-1][1]["error"]


def test_health():
    resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}


def test_empty_query_rejected():
    assert client.post("/research", json={"query": ""}).status_code == 422
    assert client.post("/research", json={}).status_code == 422
