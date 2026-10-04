"""Live end-to-end check: a real MCP client → `farm serve` (stdio) → router → LLM executor → Bifrost → model.

Run from the repo root after `farm db migrate` and `farm registry sync`:
    uv run python scripts/e2e_live.py [--local]

Needs Bifrost on 127.0.0.1:8080 and BIFROST_FARM_VK in the environment (or the repo .env). The value is passed to
the server process through its environment only and is never printed. Spends a few hundredths of a cent.
Exit code 0 only when the answer is valid AND the run's trajectory shows reserve → execute → success → commit.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from typing import Any

from fastmcp import Client
from fastmcp.client.transports import StdioTransport

TEXT = "Invoice #4471 from Acme Corp is overdue by 30 days; please pay $1,200 by Friday."
LABELS = ["billing", "support", "sales", "spam"]


def _payload(result: Any) -> dict[str, Any]:
    data = getattr(result, "structured_content", None) or getattr(result, "data", None)
    if isinstance(data, dict):
        return data
    text = "".join(getattr(c, "text", "") for c in getattr(result, "content", []))
    return json.loads(text)


async def main(local: bool) -> int:
    if not os.environ.get("BIFROST_FARM_VK"):
        print("BIFROST_FARM_VK is not set in this environment (the server may still read it from .env)")
    args = ["run", "farm", "serve"] + (["--local"] if local else [])
    transport = StdioTransport(command="uv", args=args, env=dict(os.environ), cwd=os.getcwd())
    async with Client(transport) as client:
        tools = sorted(t.name for t in await client.list_tools())
        print(f"tools: {len(tools)} ({', '.join(tools)})")
        out = _payload(await client.call_tool("classify", {"text": TEXT, "labels": LABELS}))
        ok, result, run_id = out.get("ok"), out.get("result"), out.get("run_id")
        print(f"classify ok={ok} source={out.get('source')} cost={out.get('cost')}")
        print(f"result={json.dumps(result)[:200]}")
        if not ok:
            print(f"error={json.dumps(out.get('error'))[:400]}")
        run = _payload(await client.call_tool("get_run", {"run_id": run_id})) if run_id else {}
        events = run.get("result", run).get("events", []) if isinstance(run, dict) else []
        kinds = [e.get("kind") for e in events]
        print(f"trajectory: {' → '.join(k for k in kinds if k)}")
        good = bool(ok) and isinstance(result, dict) and result.get("label") in LABELS
        needed = {"reserve", "execute", "success", "commit"}
        good = good and needed.issubset(set(kinds))
        print("E2E LIVE: PASS" if good else "E2E LIVE: FAIL")
        return 0 if good else 1


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--local", action="store_true", help="use the embedded local Postgres")
    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())  # client spawns a subprocess
    sys.exit(asyncio.run(main(parser.parse_args().local)))
