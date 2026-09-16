"""FastAPI surface for the research agent."""

from fastapi import FastAPI
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from .graph import run_research
from .streaming import stream_research

app = FastAPI(title="Multi-Step Research Agent", version="0.1.0")


class ResearchRequest(BaseModel):
    query: str = Field(min_length=1)


@app.get("/health")
def health():
    return {"status": "ok"}


@app.post("/research/stream")
async def research_stream(req: ResearchRequest):
    return StreamingResponse(stream_research(req.query), media_type="text/event-stream")


@app.post("/research")
def research(req: ResearchRequest):
    out = run_research(req.query)
    return {
        "summary": out.get("summary"),
        "confidence": out.get("confidence"),
        "citations": out.get("citations") or [],
        "status": out.get("status"),
        "iterations": out.get("iteration", 0),
        "cost_usd": round(out.get("total_cost", 0.0), 6),
        "trace_id": out.get("trace_id"),
        "errors": out.get("errors") or [],
    }
