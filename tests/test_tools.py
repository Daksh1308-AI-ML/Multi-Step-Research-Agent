"""Unit tests for agent.tools — no network, no keys required."""

import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import httpx

import agent.tools  # noqa: E402
from agent.tools import RateLimitError, tavily_search  # noqa: E402


def _fake_response(status_code=200, json_data=None, headers=None):
    return SimpleNamespace(
        status_code=status_code,
        headers=headers or {},
        json=lambda: json_data or {},
        raise_for_status=lambda: (
            None
            if status_code < 400
            else (_ for _ in ()).throw(
                httpx.HTTPStatusError(
                    "error",
                    request=None,
                    response=SimpleNamespace(status_code=status_code),
                )
            )
        ),
    )


def _raise_for_status_error(self):
    raise httpx.HTTPStatusError(
        "error", request=None, response=SimpleNamespace(status_code=self.status_code)
    )


class FakeResponse:
    def __init__(self, status_code=200, json_data=None, headers=None):
        self.status_code = status_code
        self.headers = headers or {}
        self._json = json_data or {}
        self.request = SimpleNamespace()

    def json(self):
        return self._json

    def raise_for_status(self):
        if self.status_code >= 400:
            raise httpx.HTTPStatusError(
                f"{self.status_code}", request=self.request, response=self
            )


def test_200_returns_results(monkeypatch):
    resp = FakeResponse(200, {"results": [{"title": "T", "url": "U"}]})
    monkeypatch.setattr(agent.tools.httpx, "post", lambda *a, **kw: resp)
    results = tavily_search("q", "key")
    assert results == [{"title": "T", "url": "U"}]


def test_429_exhausted_retries(monkeypatch):
    resp = FakeResponse(429, headers={"retry-after": "0.01"})
    call_count = 0

    def post(*a, **kw):
        nonlocal call_count
        call_count += 1
        return resp

    monkeypatch.setattr(agent.tools.httpx, "post", post)
    monkeypatch.setattr(agent.tools.time, "sleep", lambda s: None)
    try:
        tavily_search("q", "key", max_retries=3)
        assert False, "should have raised"
    except RateLimitError as e:
        assert "3 attempts" in str(e)
    assert call_count == 3


def test_429_then_200_succeeds(monkeypatch):
    calls = [
        FakeResponse(429, headers={"retry-after": "0.01"}),
        FakeResponse(200, {"results": ["ok"]}),
    ]
    call_count = 0

    def post(*a, **kw):
        nonlocal call_count
        r = calls[call_count]
        call_count += 1
        return r

    monkeypatch.setattr(agent.tools.httpx, "post", post)
    monkeypatch.setattr(agent.tools.time, "sleep", lambda s: None)
    assert tavily_search("q", "key", max_retries=3) == ["ok"]


def test_httpx_error_raises_runtime_error(monkeypatch):
    def post(*a, **kw):
        raise httpx.ConnectError("boom")

    monkeypatch.setattr(agent.tools.httpx, "post", post)
    try:
        tavily_search("q", "key")
        assert False, "should have raised"
    except RuntimeError as e:
        assert "unreachable" in str(e)


def test_5xx_retries_then_succeeds(monkeypatch):
    calls = [
        FakeResponse(500, {}),
        FakeResponse(502, {}),
        FakeResponse(200, {"results": ["recovered"]}),
    ]
    call_count = 0

    def post(*a, **kw):
        nonlocal call_count
        r = calls[call_count]
        call_count += 1
        return r

    monkeypatch.setattr(agent.tools.httpx, "post", post)
    monkeypatch.setattr(agent.tools.time, "sleep", lambda s: None)
    assert tavily_search("q", "key", max_retries=3) == ["recovered"]
