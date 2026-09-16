"""SSE streaming of the research graph via stream_mode="updates".

Each chunk from graph.stream(...) is {node_name: update_dict}; we translate
progress from those updates into typed SSE events.
"""

import json
import time

from .config import settings
from .graph import _checkpointer, build_graph
from .state import initial_state
from .tracing import research_trace

STAGES = {
    "planner": "planning",
    "searcher": "searching",
    "summarizer": "summarizing",
    "cite": "citing",
    "budget": "budgeting",
}


def format_sse(event: str, data: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(data)}\n\n"


async def stream_research(query: str, thread_id: str = "local"):
    """Async generator of SSE-formatted strings, traced like graph.run_research.

    Enforces one total deadline over the whole run (time-checking loop between
    chunks); when it fires, the graph iterator is closed so remaining nodes
    never execute and a final `error` event is emitted.
    """
    deadline = time.monotonic() + settings.request_timeout
    result: dict = {}
    timed_out = False

    with research_trace(query, thread_id) as ctx:
        config = {
            "configurable": {"thread_id": thread_id},
            "metadata": {"langfuse_session_id": thread_id},
            "callbacks": ctx.callbacks,
        }
        with _checkpointer() as checkpointer:
            graph = build_graph(checkpointer)
            it = graph.stream(initial_state(query), config, stream_mode="updates")
            try:
                for chunk in it:
                    if time.monotonic() >= deadline:
                        timed_out = True
                        break
                    for node, update in chunk.items():
                        yield format_sse(
                            "status", {"node": node, "stage": STAGES.get(node, node)}
                        )
                        if not update:
                            continue
                        result.update(update)
                        if node == "planner":
                            yield format_sse(
                                "progress", {"sub_questions": update.get("plan") or []}
                            )
                        elif node == "searcher":
                            yield format_sse(
                                "progress",
                                {"results_found": len(update.get("results") or [])},
                            )
                        elif node == "cite":
                            for citation in update.get("citations") or []:
                                yield format_sse("citation", citation)
                        if "total_cost" in update:
                            yield format_sse(
                                "cost",
                                {
                                    "total_cost": update["total_cost"],
                                    "cost_usd": round(update["total_cost"], 6),
                                },
                            )
            except Exception as e:
                yield format_sse("error", {"error": str(e), "status": "error"})
            finally:
                it.close()

        if timed_out:
            yield format_sse(
                "error",
                {
                    "error": f"timed out after {settings.request_timeout}s",
                    "status": "timed_out",
                },
            )
            return

        trace_id = ctx.trace_id or None
        complete = {
            "summary": result.get("summary"),
            "confidence": result.get("confidence"),
            "citations": result.get("citations") or [],
            "status": result.get("status"),
            "iterations": result.get("iteration", 0),
            "cost_usd": round(result.get("total_cost", 0.0), 6),
            "trace_id": trace_id,
            "errors": result.get("errors") or [],
        }
        ctx.set_trace_output(
            {
                "summary": complete["summary"],
                "status": complete["status"],
                "confidence": complete["confidence"],
                "cost_usd": complete["cost_usd"],
                "citations": len(complete["citations"]),
            }
        )
        yield format_sse("complete", complete)
