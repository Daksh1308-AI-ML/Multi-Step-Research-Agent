"""Unit tests for agent.config — no network, no keys required."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from agent.config import Settings  # noqa: E402


def test_defaults():
    s = Settings(_env_file=None)
    assert s.max_iterations == 3
    assert s.per_request_budget == 0.05
    assert s.request_timeout == 180
    assert s.search_depth == "basic"
    assert s.max_search_results == 5
    assert s.llm_model == "deepseek-v4-flash"
    assert s.llm_base_url == "https://opencode.ai/zen/v1"


def test_env_overrides(monkeypatch):
    monkeypatch.setenv("MAX_ITERATIONS", "7")
    monkeypatch.setenv("REQUEST_TIMEOUT", "300")
    monkeypatch.setenv("PER_REQUEST_BUDGET", "1.5")
    monkeypatch.setenv("SEARCH_DEPTH", "advanced")
    monkeypatch.setenv("MAX_SEARCH_RESULTS", "10")
    s = Settings(_env_file=None)
    assert s.max_iterations == 7
    assert s.request_timeout == 300
    assert s.per_request_budget == 1.5
    assert s.search_depth == "advanced"
    assert s.max_search_results == 10


def test_api_key_defaults():
    s = Settings(_env_file=None)
    assert s.openai_api_key == ""
    assert s.tavily_api_key == ""
    assert s.langfuse_public_key == ""
    assert s.langfuse_secret_key == ""
