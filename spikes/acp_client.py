"""Tiny headless ACP (Agent Client Protocol) client for the S0 spikes.

JSON-RPC 2.0, newline-delimited, over the stdio of an agent process. Steps: initialize ->
(authenticate if the agent requires it) -> session/new with the Farm MCP endpoint -> one or more
session/prompt turns in the SAME session. Records latency per turn, every session/update kind, the
permission requests the agent sent (and what this client answered) and any usage the agent reports.

The Farm token is read from the FARM_TOKEN environment variable and only ever sent in the MCP
``Authorization`` header handed to the agent; it is never printed or written to the report.

    python spikes/acp_client.py --out report.json \
        --prompt "Call get_capacity, then say OK." --prompt "Say it again." \
        --allow-tool get_capacity -- npx -y @zed-industries/claude-agent-acp
"""

from __future__ import annotations

import argparse
import json
import os
import queue
import re
import shutil
import subprocess
import sys
import threading
import time
from typing import Any

FARM_URL = "http://127.0.0.1:8787/mcp"
PROTOCOL_VERSION = 1


class AcpError(RuntimeError):
    """The agent answered a request with a JSON-RPC error."""

    def __init__(self, method: str, error: dict[str, Any]):
        super().__init__(f"{method}: {error}")
        self.method = method
        self.error = error


class AcpClient:
    def __init__(
        self, argv: list[str], allow_tool: re.Pattern[str] | None, env: dict[str, str] | None = None
    ):
        exe = shutil.which(argv[0]) or argv[0]
        self.proc = subprocess.Popen(
            [exe, *argv[1:]],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env={**os.environ, **(env or {})},
        )
        self._next_id = 0
        self._pending: dict[int, queue.Queue[dict[str, Any]]] = {}
        self._lock = threading.Lock()
        self.allow_tool = allow_tool
        self.updates: list[dict[str, Any]] = []
        self.permission_requests: list[dict[str, Any]] = []
        self.agent_requests: list[str] = []
        self.stderr_tail: list[str] = []
        self.text_chunks: list[str] = []
        threading.Thread(target=self._read_stdout, daemon=True).start()
        threading.Thread(target=self._read_stderr, daemon=True).start()

    # -- transport -------------------------------------------------------------------------
    def _send(self, msg: dict[str, Any]) -> None:
        assert self.proc.stdin is not None
        self.proc.stdin.write((json.dumps(msg) + "\n").encode())
        self.proc.stdin.flush()

    def _read_stderr(self) -> None:
        assert self.proc.stderr is not None
        for raw in self.proc.stderr:
            self.stderr_tail = (self.stderr_tail + [raw.decode(errors="replace").rstrip()])[-15:]

    def _read_stdout(self) -> None:
        assert self.proc.stdout is not None
        for raw in self.proc.stdout:
            line = raw.decode(errors="replace").strip()
            if not line.startswith("{"):
                continue
            try:
                msg = json.loads(line)
            except json.JSONDecodeError:
                continue
            if "method" in msg:
                self._on_agent_message(msg)
            elif msg.get("id") in self._pending:
                self._pending[msg["id"]].put(msg)

    def _on_agent_message(self, msg: dict[str, Any]) -> None:
        method = msg["method"]
        if method == "session/update":
            update = msg["params"].get("update", {})
            self.updates.append({"t": time.monotonic(), "kind": update.get("sessionUpdate"), "raw": update})
            if update.get("sessionUpdate") == "agent_message_chunk":
                content = update.get("content") or {}
                if content.get("type") == "text":
                    self.text_chunks.append(content.get("text", ""))
            return
        if "id" not in msg:
            return
        self.agent_requests.append(method)
        if method == "session/request_permission":
            self._answer_permission(msg)
        else:  # fs/*, terminal/* : we declared no such capability
            self._send(
                {"jsonrpc": "2.0", "id": msg["id"], "error": {"code": -32601, "message": "not supported"}}
            )

    def _answer_permission(self, msg: dict[str, Any]) -> None:
        params = msg["params"]
        call = params.get("toolCall", {})
        label = json.dumps({k: call.get(k) for k in ("title", "kind", "rawInput")}, default=str)
        options = params.get("options", [])
        allowed = bool(self.allow_tool and self.allow_tool.search(label))
        wanted = ("allow_once", "allow_always") if allowed else ("reject_once", "reject_always")
        choice = next((o for o in options if o.get("kind") in wanted), None)
        self.permission_requests.append(
            {
                "tool": call.get("title"),
                "kind": call.get("kind"),
                "options": [o.get("kind") for o in options],
                "answered": "allow" if allowed and choice else "reject",
            }
        )
        outcome = (
            {"outcome": "selected", "optionId": choice["optionId"]} if choice else {"outcome": "cancelled"}
        )
        self._send({"jsonrpc": "2.0", "id": msg["id"], "result": {"outcome": outcome}})

    def request(self, method: str, params: dict[str, Any], timeout: float = 180.0) -> dict[str, Any]:
        with self._lock:
            self._next_id += 1
            rid = self._next_id
        box: queue.Queue[dict[str, Any]] = queue.Queue()
        self._pending[rid] = box
        self._send({"jsonrpc": "2.0", "id": rid, "method": method, "params": params})
        try:
            msg = box.get(timeout=timeout)
        except queue.Empty:
            raise TimeoutError(
                f"{method}: no answer in {timeout}s; stderr tail: {self.stderr_tail[-3:]}"
            ) from None
        if "error" in msg:
            raise AcpError(method, msg["error"])
        return msg.get("result", {})

    def close(self) -> None:
        try:
            self.proc.terminate()
            self.proc.wait(timeout=5)
        except Exception:
            self.proc.kill()


def run(
    argv: list[str],
    prompts: list[str],
    allow_tool: str | None,
    cwd: str,
    model: str | None,
    farm_url: str,
    with_farm: bool,
    auth_method: str | None = None,
) -> dict[str, Any]:
    report: dict[str, Any] = {"agent": argv, "turns": []}
    pattern = re.compile(allow_tool) if allow_tool else None
    t0 = time.monotonic()
    client = AcpClient(argv, pattern)
    try:
        init = client.request(
            "initialize",
            {
                "protocolVersion": PROTOCOL_VERSION,
                "clientCapabilities": {},
                "clientInfo": {"name": "farm-s0", "version": "0"},
            },
        )
        report["initialize_s"] = round(time.monotonic() - t0, 2)
        report["agent_info"] = init.get("agentInfo")
        report["agent_capabilities"] = init.get("agentCapabilities")
        report["auth_methods"] = [m.get("id") for m in init.get("authMethods", [])]

        if auth_method:
            client.request("authenticate", {"methodId": auth_method})
        mcp_servers: list[dict[str, Any]] = []
        token = os.environ.get("FARM_TOKEN")
        if with_farm and token:
            mcp_servers = [
                {
                    "type": "http",
                    "name": "harness-farm",
                    "url": farm_url,
                    "headers": [{"name": "Authorization", "value": f"Bearer {token}"}],
                }
            ]
        t1 = time.monotonic()
        session = client.request("session/new", {"cwd": cwd, "mcpServers": mcp_servers})
        report["session_new_s"] = round(time.monotonic() - t1, 2)
        sid = session["sessionId"]
        report["session_modes"] = [
            m.get("id") for m in (session.get("modes") or {}).get("availableModes", [])
        ]
        report["session_models"] = [
            m.get("modelId") for m in (session.get("models") or {}).get("availableModels", [])
        ][:12]
        if model:
            try:
                client.request("session/set_model", {"sessionId": sid, "modelId": model})
                report["model_set"] = model
            except AcpError as e:
                report["model_set_error"] = e.error

        for text in prompts:
            client.text_chunks.clear()
            start_idx = len(client.updates)
            start_perm = len(client.permission_requests)
            t = time.monotonic()
            result = client.request(
                "session/prompt", {"sessionId": sid, "prompt": [{"type": "text", "text": text}]}
            )
            kinds = [u["kind"] for u in client.updates[start_idx:]]
            tools = [
                u["raw"].get("title") or u["raw"].get("toolCallId")
                for u in client.updates[start_idx:]
                if u["kind"] == "tool_call"
            ]
            report["turns"].append(
                {
                    "prompt": text,
                    "latency_s": round(time.monotonic() - t, 2),
                    "stop_reason": result.get("stopReason"),
                    "result_meta": {k: v for k, v in result.items() if k != "stopReason"},
                    "update_kinds": {k: kinds.count(k) for k in sorted(set(kinds))},
                    "tool_calls": tools,
                    "permission_requests": client.permission_requests[start_perm:],
                    "reply": "".join(client.text_chunks)[:400],
                }
            )
        report["usage_updates"] = [
            u["raw"] for u in client.updates if u["kind"] in ("usage_update", "usage")
        ][-2:]
    except (AcpError, TimeoutError) as e:
        report["error"] = str(e)[:600]
    finally:
        report["agent_requests_seen"] = sorted(set(client.agent_requests))
        report["stderr_tail"] = client.stderr_tail[-5:]
        client.close()
    report["total_s"] = round(time.monotonic() - t0, 2)
    return report


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--prompt", action="append", required=True, help="repeat for several turns in one session")
    p.add_argument(
        "--allow-tool",
        help="regex; permission requests whose title/kind/input match are allowed, others rejected",
    )
    p.add_argument("--cwd", default=os.getcwd())
    p.add_argument("--model", help="session/set_model id, if the agent offers it")
    p.add_argument("--farm-url", default=FARM_URL)
    p.add_argument("--auth-method", help="methodId for the authenticate call, one of the agent's authMethods")
    p.add_argument("--no-farm", action="store_true", help="do not hand over the Farm MCP endpoint")
    p.add_argument("--out", help="write the JSON report here")
    p.add_argument("agent", nargs=argparse.REMAINDER, help="-- <agent command and args>")
    args = p.parse_args()
    agent = [a for a in args.agent if a != "--"] if args.agent[:1] == ["--"] else args.agent
    if not agent:
        p.error("give the agent command after --")
    report = run(
        agent,
        args.prompt,
        args.allow_tool,
        args.cwd,
        args.model,
        args.farm_url,
        not args.no_farm,
        args.auth_method,
    )
    text = json.dumps(report, indent=2, default=str)
    if args.out:
        with open(args.out, "w", encoding="utf8") as fh:
            fh.write(text)
    print(text)
    return 1 if "error" in report else 0


if __name__ == "__main__":
    sys.exit(main())
