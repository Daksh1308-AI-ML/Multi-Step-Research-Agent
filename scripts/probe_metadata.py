"""Probe: what metadata/tags does the Langfuse(=probe) callback see at root chain start?"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from langchain_core.callbacks import BaseCallbackHandler
from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel
from langchain_core.messages import AIMessage
from langchain_core.outputs import ChatGeneration, ChatResult

import agent.nodes as nodes
from agent.graph import build_graph, initial_state


class Probe(BaseCallbackHandler):
    def __init__(self):
        self.root_meta = None

    def on_chain_start(self, serialized, inputs, *, run_id, parent_run_id=None, tags=None, metadata=None, **kwargs):
        if parent_run_id is None:
            self.root_meta = (tags, metadata)


class FakeLLM(FakeMessagesListChatModel):
    responses: list = []

    def _generate(self, messages, stop=None, run_manager=None, **kwargs):
        payload = '{"sub_questions": ["Q1"], "summary": "s [1]", "confidence": "medium"}'
        msg = AIMessage(content=payload)
        msg.usage_metadata = {"input_tokens": 1, "output_tokens": 1}
        return ChatResult(generations=[ChatGeneration(message=msg)])


def main():
    nodes.get_llm = FakeLLM
    probe = Probe()
    g = build_graph()
    g.invoke(
        initial_state("q"),
        config={
            "configurable": {"thread_id": "t1"},
            "metadata": {"langfuse_session_id": "sess1"},
            "callbacks": [probe],
        },
    )
    print("ROOT tags:", probe.root_meta[0])
    print("ROOT meta:", probe.root_meta[1])


if __name__ == "__main__":
    main()