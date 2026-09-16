"""Emit a real Langfuse trace without paying for LLM/search calls.

Runs the instrumented run_research path against your real Langfuse project
(.env LANGFUSE_* keys) using a FakeMessagesListChatModel so the graph can
execute end-to-end for trace-shape verification.
"""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel
from langchain_core.messages import AIMessage
from langchain_core.outputs import ChatGeneration, ChatResult

import agent.nodes as nodes
from agent.graph import run_research


class FakeLLM(FakeMessagesListChatModel):
    """Override generate entirely; pass a dummy list so pydantic validation passes."""
    responses: list = []

    def _generate(self, messages, stop=None, run_manager=None, **kwargs):
        # Same JSON shape satisfies both the planner (sub_questions) and the
        # summarizer (summary/confidence) nodes.
        payload = '{"sub_questions": ["Q1", "Q2"], "summary": "Fake summary for trace-shape verification. [1]", "confidence": "medium"}'
        message = AIMessage(content=payload)
        message.usage_metadata = {"input_tokens": 50, "output_tokens": 40}
        return ChatResult(generations=[ChatGeneration(message=message)])


def main(query: str, thread_id: str) -> None:
    nodes.get_llm = FakeLLM  # replace real LLM with the fake for this audit run
    result = run_research(query, thread_id=thread_id)
    print("RUN OK")
    print("status   :", result.get("status"))
    print("plan     :", result.get("plan"))
    print("summary  :", result.get("summary"))
    print("trace_id :", result.get("trace_id"))
    if result.get("trace_id"):
        print("TRACE_ID_FILE:", result["trace_id"])


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--query", default="What is best practice for tracing AI agents?")
    parser.add_argument("--thread-id", default="audit-thread-1")
    args = parser.parse_args()
    main(args.query, args.thread_id)