"""Graph state shared across nodes."""

import operator
from typing import Annotated, Optional, TypedDict


class ResearchState(TypedDict):
    query: str
    plan: Optional[list[str]]
    results: Annotated[list[dict], operator.add]
    summary: Optional[str]
    confidence: Optional[str]
    citations: Optional[list[dict]]
    errors: Annotated[list[str], operator.add]
    iteration: int
    total_cost: float
    status: str  # ok | no_results | summary_failed


def initial_state(query: str) -> dict:
    return {
        "query": query,
        "plan": None,
        "results": [],
        "summary": None,
        "confidence": None,
        "citations": None,
        "errors": [],
        "iteration": 0,
        "total_cost": 0.0,
        "status": "",
    }
