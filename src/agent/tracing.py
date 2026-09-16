"""Langfuse tracing for the research agent.

Uses the LangChain integration (``langfuse.langchain.CallbackHandler``) so the
LangGraph node spans, DeepSeek generations, model names, token usage and costs
are captured automatically, nested under a single ``research-agent`` trace per
run (``propagate_attributes`` + an enclosing span).

Baseline requirements are covered by the integration unless noted here:
- Input is set explicitly to the user query only (never internal state/args).
- Output is set to the public result shape (summary, status, cost).
- session_id groups runs by thread; a ``feature: research`` tag is added.
- Tracing is a NO-OP when keys are absent, so the agent never depends on
  Langfuse being available (graceful degradation).
"""

from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Any, Iterator, Optional

from .config import settings

TRACE_NAME = "research-agent"


def tracing_enabled() -> bool:
    return bool(settings.langfuse_public_key and settings.langfuse_secret_key)


@dataclass
class TracingContext:
    callbacks: list = field(default_factory=list)
    _span: Any = None
    _client: Any = None
    trace_id: Optional[str] = None

    def set_trace_output(self, output: dict) -> None:
        if self._span is not None:
            self._span.update(output=output)


def _client():
    from langfuse import Langfuse

    # Registers the singleton that CallbackHandler/get_client() will use.
    return Langfuse(
        public_key=settings.langfuse_public_key,
        secret_key=settings.langfuse_secret_key,
        base_url=settings.langfuse_host,
    )


@contextmanager
def research_trace(query: str, thread_id: str = "local") -> Iterator[TracingContext]:
    """Context manager wrapping one research run. No-op when tracing is disabled.

    Usage inside graph.run_research::

        with research_trace(query, thread_id) as ctx:
            result = graph.invoke(state, config={"configurable": {"thread_id": thread_id},
                                                  "callbacks": ctx.callbacks})
            ctx.set_trace_output(output_dict)
            result["trace_id"] = ctx.trace_id
    """
    ctx = TracingContext()
    if not tracing_enabled():
        yield ctx
        return

    # Import inside the function so Langfuse initializes AFTER settings loaded
    # from .env (avoids Langfuse picking up missing/empty credentials).
    try:
        from langfuse import get_client, propagate_attributes
        from langfuse.langchain import CallbackHandler
    except ImportError:
        yield ctx  # integration not installed -> degrade to no-op
        return

    try:
        _client()
        handler = CallbackHandler()
    except Exception:
        yield ctx  # auth/config failure -> degrade to no-op
        return

    ctx.callbacks = [handler]
    client = get_client()
    ctx._client = client

    with client.start_as_current_observation(as_type="span", name=TRACE_NAME) as span:
        with propagate_attributes(
            trace_name=TRACE_NAME,
            session_id=thread_id,
            tags=["feature: research"],
        ):
            span.update(input={"query": query})
            ctx._span = span
            ctx.trace_id = span.trace_id
            try:
                yield ctx
            finally:
                # Push the queued trace immediately so it's visible right away.
                client.flush()
