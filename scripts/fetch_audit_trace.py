"""Fetch the audit trace from Langfuse's public API and dump its structure."""
import base64
import json
import sys
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from agent.config import settings

TRACE_ID = "dd186f37a6a34f196dbbe291eb559551"


def main():
    host = settings.langfuse_host.rstrip("/")
    auth = base64.b64encode(
        f"{settings.langfuse_public_key}:{settings.langfuse_secret_key}".encode()
    ).decode()
    r = httpx.get(f"{host}/api/public/traces/{TRACE_ID}", headers={"Authorization": f"Basic {auth}"}, timeout=30)
    r.raise_for_status()
    trace = r.json()
    print(f"name       : {trace.get('name')}")
    print(f"session_id : {trace.get('session_id')}")
    print(f"tags       : {trace.get('tags')}")
    print(f"input      : {json.dumps(trace.get('input'))}")
    print(f"output     : {json.dumps(trace.get('output'))}")
    print(f"metadata   : {json.dumps(trace.get('metadata'))}")
    print()
    obs = trace.get("observations") or []
    print(f"=== OBSERVATIONS ({len(obs)}) ===")
    for o in obs:
        usage = o.get("usage") or {}
        print(f"  [{o.get('type'):10}] {o.get('name',''):25} "
              f"model={o.get('model')} "
              f"in={usage.get('input')} out={usage.get('output')}")
        parent = o.get("parent_observation_id")
        if parent:
            print(f"    parent id: {parent}")


if __name__ == "__main__":
    main()