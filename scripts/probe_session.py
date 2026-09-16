"""Isolate: which ordering actually persists session_id on the trace?"""
import base64
import time
import sys
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from agent.config import settings

host = settings.langfuse_host.rstrip("/")
auth = base64.b64encode(f"{settings.langfuse_public_key}:{settings.langfuse_secret_key}".encode()).decode()

def fetch_trace(tid):
    for _ in range(6):
        r = httpx.get(f"{host}/api/public/traces/{tid}", headers={"Authorization": f"Basic {auth}"}, timeout=30)
        if r.status_code == 200:
            t = r.json()
            return t.get("sessionId"), t.get("name"), t.get("tags")
        time.sleep(2)
    return ("notfound", None, None)


def main():
    from langfuse import get_client, propagate_attributes

    lf = get_client()
    tids = []
    # A: propagate BEFORE span (attrs baked at span start)
    with propagate_attributes(trace_name="probe-A", session_id="sess-A", tags=["a"]):
        with lf.start_as_current_observation(as_type="span", name="probe-A") as span:
            pass
        tids.append(span.trace_id)
    # B: propagate INSIDE span after trace exists
    with lf.start_as_current_observation(as_type="span", name="probe-B") as span:
        with propagate_attributes(trace_name="probe-B", session_id="sess-B", tags=["b"]):
            pass
        tids.append(span.trace_id)
    lf.flush()
    for label, tid in (("A before-span", tids[0]), ("B inside-span", tids[1])):
        print(label, "| session=", fetch_trace(tid)[0], "| name=", fetch_trace(tid)[1], "| tags=", fetch_trace(tid)[2])


if __name__ == "__main__":
    main()