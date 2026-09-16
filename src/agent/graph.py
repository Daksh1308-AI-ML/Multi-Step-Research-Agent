"""LangGraph assembly: plan -> search -> summarize -> cite, with retry routing.

route_final is the loop guard (the agent can never loop more than max_iterations),
and the budget node is the last safety net before returning.
"""

from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, START, StateGraph

from .config import settings
from .nodes import budget_node, cite_node, planner_node, searcher_node, summarizer_node
from .state import ResearchState, initial_state
from .tracing import research_trace


def route_final(state: ResearchState) -> str:
    if state["status"] == "ok" or state["status"] == "no_results":
        return "budget"
    if state["iteration"] >= settings.max_iterations:
        # forced termination -> graceful degradation output
        return "budget"
    return "planner"  # summary_failed and budget allows another pass


def _checkpointer():
    # ponytail: MemorySaver for now; swap to PostgresSaver when DATABASE_URL exists.
    return MemorySaver()


def build_graph():
    g = StateGraph(ResearchState)
    g.add_node("planner", planner_node)
    g.add_node("searcher", searcher_node)
    g.add_node("summarizer", summarizer_node)
    g.add_node("cite", cite_node)
    g.add_node("budget", budget_node)

    g.add_edge(START, "planner")
    g.add_edge("planner", "searcher")
    g.add_edge("searcher", "summarizer")
    g.add_conditional_edges(
        "summarizer", route_final, {"planner": "planner", "budget": "budget"}
    )
    g.add_edge("budget", "cite")
    g.add_edge("cite", END)
    return g.compile(checkpointer=_checkpointer())


def run_research(query: str, thread_id: str = "local") -> dict:
    """Run the agent end to end. Returns the final state (dict) plus trace_id."""
    graph = build_graph()
    with research_trace(query, thread_id) as ctx:
        result = graph.invoke(
            initial_state(query),
            config={
                "configurable": {"thread_id": thread_id},
                # Must be top-level metadata (NOT configurable): only a whitelist
                # of configurable keys is copied into run metadata, and the
                # Langfuse callback reads these keys to set the trace session.
                "metadata": {"langfuse_session_id": thread_id},
                "callbacks": ctx.callbacks,  # empty when tracing disabled -> no-op
            },
        )
        if ctx.trace_id:
            result["trace_id"] = ctx.trace_id
        ctx.set_trace_output(
            {
                "summary": result.get("summary"),
                "status": result.get("status"),
                "confidence": result.get("confidence"),
                "cost_usd": round(result.get("total_cost", 0.0), 6),
                "citations": len(result.get("citations") or []),
            }
        )
    return result
