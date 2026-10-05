"""AI jobs: the non-blocking layer (``farm.ai``) through the real router, ledger, MCP server and fake CLIs.

Everything between the MCP client and the CLI process is real (FastMCP server and middleware, ``JobManager``,
router, quota ledger, Postgres, the CLI runner with its thread and process-tree kill); only the CLI binary is
the M3e fake. ``AiHarness`` extends the M3e harness with what jobs need: one event file per CLI call (parallel
calls would race on the harness's single calls file), per-account responses for agy accounts (matched by their
home directory), a child process to prove a tree kill and a file written into the cwd.

``test_ai_conversations.py`` and ``test_accept_aip2.py`` import the fixtures and helpers of this module.
"""

from __future__ import annotations

import asyncio
import json
import os
import shutil
import subprocess
import time
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any
from uuid import uuid4

import mcp_types
import psutil
import psycopg
import pytest
import pytest_asyncio
from alembic import command
from fastmcp import Client, FastMCP
from typer.testing import CliRunner

from farm.ai.accounts import AccountInfo, Wish, plan_accounts, rank
from farm.ai.failures import AiRequestError, Busy, Failure, classify
from farm.ai.jobs import Attempt, JobManager, JobRecord, JobStore
from farm.capabilities.schemas import AiJobSpec, parse_uuid
from farm.context import FarmContext
from farm.control.cli import alembic_config, app, format_ai_list
from farm.db.pool import DbPool
from farm.executors.api import ApiExecutor
from farm.executors.base import ConnectionView, ErrorKind, ExecRequest
from farm.executors.cli_agent import CliAgentExecutor
from farm.executors.cli_agent.agy import AgyCliExecutor
from farm.executors.cli_agent.codex import CodexCliExecutor
from farm.gateway.ai_tools import AI_TOOLS, ManagerHook
from farm.gateway.server import INFRA_TOOLS, build_server
from farm.registry import CapabilitySpec, ConnectionSpec, ProviderSpec, Registry, load_registry
from farm.registry.models import UnitSpec
from farm.resources.router import AttemptSummary, RouteError
from tests.conftest import FIXTURE_REGISTRY, START, FakeClock
from tests.farm_helpers import fetch
from tests.test_cli_agent_fakes import FakeCliHarness

type Make = Callable[..., Awaitable[FarmContext]]

WAIT_S = 30
RESET_AT = START + timedelta(hours=6)


# --- the harness: the M3e fake CLIs plus what jobs need --------------------------------------------------------

_MATCH = "    if (claude_dir and acc_key in claude_dir) or (codex_home and acc_key in codex_home):\n"
_MATCH_WITH_HOME = (
    "    if (claude_dir and acc_key in claude_dir) or (codex_home and acc_key in codex_home)"
    ' or (os.environ.get("USERPROFILE") and acc_key in os.environ["USERPROFILE"]):\n'
)
_BEFORE_DELAY = "# Check if delay requested\n"
_EVENT = '''
import subprocess as _sp
import time as _time

_ev_dir = bin_dir / "events"
_ev_dir.mkdir(exist_ok=True)
_ev_path = _ev_dir / f"{_time.time_ns()}-{os.getpid()}.json"
_event = {
    "pid": os.getpid(), "start": _time.time(), "argv": raw_argv, "stdin": stdin_data, "cwd": os.getcwd(),
    "claude_dir": claude_dir, "codex_home": codex_home, "userprofile": os.environ.get("USERPROFILE"),
    "home": os.environ.get("HOME"), "env": dict(os.environ),
}
_ev_path.write_text(json.dumps(_event), encoding="utf-8")
if active_config.get("write_file"):
    Path(os.getcwd(), active_config["write_file"]).write_text("changed by the worker", encoding="utf-8")
if active_config.get("pid_file"):
    _child = _sp.Popen([sys.executable, "-c", "import time; time.sleep(300)"])
    Path(active_config["pid_file"]).write_text(f"{os.getpid()} {_child.pid}", encoding="utf-8")

'''
_EXIT = 'sys.exit(int(active_config.get("exit_code", 0)))'
_EXIT_WITH_END = '_event["end"] = _time.time()\n_ev_path.write_text(json.dumps(_event), encoding="utf-8")\n' + _EXIT


class AiHarness(FakeCliHarness):
    """``FakeCliHarness`` with an event file per call, per-home responses, a child process and a file write.

    Extra keys of a ``per_account`` entry: ``write_file`` (written into the cwd) and ``pid_file`` (the fake's
    pid and that of a child it spawns, so a test can check that a kill took the whole tree).
    """

    def _write_runner_script(self) -> None:
        super()._write_runner_script()
        text = self.runner_script.read_text(encoding="utf-8")
        for old, new in (
            (_MATCH, _MATCH_WITH_HOME),
            (_BEFORE_DELAY, _EVENT + _BEFORE_DELAY),
            (_EXIT, _EXIT_WITH_END),
        ):
            assert text.count(old) == 1, "the M3e fake runner changed: update AiHarness"
            text = text.replace(old, new)
        self.runner_script.write_text(text, encoding="utf-8")

    def events(self) -> list[dict[str, Any]]:
        """One entry per CLI call so far, oldest first (``end`` is missing for a call that was killed)."""
        folder = self.bin_dir / "events"
        found: list[dict[str, Any]] = []
        for path in sorted(folder.glob("*.json")) if folder.exists() else []:
            try:
                found.append(json.loads(path.read_text(encoding="utf-8")))
            except (OSError, json.JSONDecodeError):  # a call that is being written right now
                continue
        return sorted(found, key=lambda e: e["start"])

    def runners(self) -> list[psutil.Process]:
        """Fake CLI processes that are still alive, found by the pid each call recorded.

        (Scanning every process of the machine for the script name takes seconds on Windows, per test.)
        """
        live = []
        for event in self.events():
            try:
                proc = psutil.Process(event["pid"])
                if proc.status() != psutil.STATUS_ZOMBIE and str(self.runner_script) in " ".join(proc.cmdline()):
                    live.append(proc)  # the cmdline check guards against a pid the system has reused
            except psutil.Error:
                continue
        return live


def claude_ok(
    text: str, *, session: str | None = None, cost: float | None = 0.01, usage: tuple[int, int] = (10, 5)
) -> str:
    payload: dict[str, Any] = {
        "result": text,
        "session_id": session or f"sess-{uuid4().hex[:10]}",
        "is_error": False,
        "usage": {"input_tokens": usage[0], "output_tokens": usage[1]},
    }
    if cost is not None:
        payload["total_cost_usd"] = cost
    return json.dumps(payload)


def agy_ok(text: str, *, conversation: str | None = None) -> str:
    return json.dumps(
        {
            "conversation_id": conversation or f"conv-{uuid4().hex[:10]}",
            "status": "SUCCESS",
            "response": text,
            "usage": {"input_tokens": 7, "output_tokens": 3, "total_tokens": 10},
        }
    )


def claude_limit(reset: datetime = RESET_AT) -> str:
    stamp = reset.strftime("%Y-%m-%dT%H:%M:%SZ")
    return json.dumps({"result": f"Usage limit reached. Your limit will reset at {stamp}", "is_error": True})


# --- the registry and the context -------------------------------------------------------------------------------


def _connection(
    conn_id: str,
    priority: int,
    meta: dict[str, Any],
    *,
    concurrency: int = 1,
    charged_on: str = "attempt",
    limit: int = 100,
) -> ConnectionSpec:
    return ConnectionSpec(
        id=conn_id,
        auth_ref=f"cli:{conn_id}",
        priority=priority,
        concurrency=concurrency,
        meta=meta,
        units={"requests": UnitSpec(limit=Decimal(limit), period="rolling_5h", charged_on=charged_on)},  # type: ignore[arg-type]
    )


def ai_registry(
    tmp: Path,
    *,
    claude: int = 3,
    gemini: int = 2,
    codex: int = 0,
    hermes: int = 0,
    charged_on: str = "attempt",
    claude_parallel: int = 1,
    pool_cap: int | None = None,
    edit_root: Path | None = None,
    limit: int = 100,
) -> Registry:
    """The fixture registry with AI pools: claude-02.. (own config dirs), agy-01.. (own homes), codex-01.."""
    registry = load_registry(FIXTURE_REGISTRY)
    kw: dict[str, Any] = {"charged_on": charged_on, "limit": limit}
    claudes = []
    for index in range(claude):
        number = index + 2
        meta: dict[str, Any] = {
            "cli": "claude",
            "config_dir": str(tmp / f"claude-{number:02d}"),
            "models": ["sonnet", "opus"],
        }
        if claude_parallel > 1:
            meta["max_parallel"] = claude_parallel
        if edit_root is not None and index == 0:
            meta.update(allow_edit=True, edit_roots=[str(edit_root)])
        claudes.append(_connection(f"claude-{number:02d}", index + 1, meta, concurrency=claude_parallel, **kw))
    registry.providers["claude"] = ProviderSpec(
        name="Claude",
        kind="ai",
        executor="cli_agent",
        connections=claudes,
        config={} if pool_cap is None else {"max_parallel": pool_cap},
    )
    registry.providers["gemini"] = ProviderSpec(
        name="Gemini (agy)",
        kind="ai",
        executor="cli_agent",
        connections=[
            _connection(
                f"agy-{n:02d}",
                n,
                {"cli": "agy", "home": str(tmp / f"agy-{n:02d}"), "models": ["gemini-3.8-flash-high"]},
                **kw,
            )
            for n in range(1, gemini + 1)
        ],
    )
    routes = ["claude", "gemini"]
    if codex:
        registry.providers["codex"] = ProviderSpec(
            name="Codex",
            kind="ai",
            executor="cli_agent",
            connections=[
                _connection(
                    f"codex-{n:02d}",
                    n,
                    {"cli": "codex", "home": str(tmp / f"codex-{n:02d}"), "models": ["gpt-5"]},
                    **kw,
                )
                for n in range(1, codex + 1)
            ],
        )
        routes.append("codex")
    if hermes:
        registry.providers["hermes"] = ProviderSpec(
            name="Hermes",
            kind="ai",
            executor="cli_agent",
            connections=[
                _connection(
                    f"hermes-{n:02d}",
                    n,
                    {"cli": "hermes", "profile": "farm-agent", "models": ["deepseek"]},
                    **kw,
                )
                for n in range(1, hermes + 1)
            ],
        )
        routes.append("hermes")
    registry.capabilities["ask_ai"] = CapabilitySpec(
        kind="ai", description="Ask another AI to do a task.", routes=routes, strategy="failover"
    )
    return registry


@dataclass
class Aip:
    """A running Farm with AI pools: context, MCP server, the job manager behind it and the fake CLIs."""

    ctx: FarmContext
    server: FastMCP
    manager: JobManager
    harness: AiHarness
    clock: FakeClock
    tmp: Path

    @property
    def pool(self) -> DbPool:
        return self.ctx.pool

    def client(self, name: str = "claude-code") -> Client[Any]:
        return Client(self.server, client_info=mcp_types.Implementation(name=name, version="1"))

    async def job(self, job_id: str) -> dict[str, Any]:
        """The ``ai_jobs`` row (without any answer text: there is none in the table)."""
        rows = await fetch(
            self.pool,
            "select state, account, error_kind, error_cause, retry_at, attempts, native_session_id, tokens, "
            "cost_usd, cost_estimated, run_id, result_path, finished_at from public.ai_jobs where id = %s",
            job_id,
        )
        keys = (
            "state account error_kind error_cause retry_at attempts native_session_id tokens cost_usd "
            "cost_estimated run_id result_path finished_at"
        ).split()
        assert rows, f"no job {job_id}"
        return dict(zip(keys, rows[0], strict=True))

    async def reservations(self, account: str) -> list[str]:
        rows = await fetch(
            self.pool,
            "select status from public.quota_reservations where connection_id = %s order by created_at",
            account,
        )
        return [r[0] for r in rows]

    async def quota(self, account: str) -> tuple[Decimal, Decimal]:
        """``(used, reserved)`` of the account's requests."""
        rows = await fetch(
            self.pool,
            "select coalesce(sum(used), 0), coalesce(sum(reserved), 0) from public.quota_usage "
            "where connection_id = %s and unit = 'requests'",
            account,
        )
        return rows[0]  # type: ignore[return-value]


@pytest_asyncio.fixture
async def aip_factory(
    farm_factory: Make, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> AsyncIterator[Callable[..., Awaitable[Aip]]]:
    """``await make(**registry_options)``: a Farm with AI pools wired to fake CLIs on PATH, results under tmp."""
    harness = AiHarness(tmp_path / "bin")
    for cli in ("claude", "codex", "agy", "hermes"):
        harness.register_cli(cli)
    monkeypatch.setenv("PATH", f"{harness.bin_dir}{os.pathsep}{os.environ.get('PATH', '')}")
    monkeypatch.setenv("FARM_DATA_DIR", str(tmp_path / "data"))

    async def make(**options: Any) -> Aip:
        clock = FakeClock()
        ctx = await farm_factory(
            ai_registry(tmp_path, **options),
            executors={"api": ApiExecutor(), "cli_agent": CliAgentExecutor()},
            clock=clock,
        )
        server = await build_server(ctx)
        hook = next(e for e in ctx.executors.values() if isinstance(e, ManagerHook))
        return Aip(ctx=ctx, server=server, manager=hook.manager, harness=harness, clock=clock, tmp=tmp_path)

    yield make
    for proc in harness.runners():  # a failing test must not leave fake CLIs (or what they spawned) behind
        for child in proc.children(recursive=True):
            child.kill()
        proc.kill()


@pytest_asyncio.fixture
async def aip(aip_factory: Callable[..., Awaitable[Aip]]) -> Aip:
    return await aip_factory()


async def call(client: Client[Any], tool: str, **arguments: Any) -> dict[str, Any]:
    """Call a tool and return its structured content (a protocol-level error has none and fails loudly)."""
    reply = await client.call_tool(tool, arguments, raise_on_error=False)
    assert reply.structured_content is not None, f"{tool}: {reply.content}"
    return dict(reply.structured_content)


async def start(client: Client[Any], **arguments: Any) -> dict[str, Any]:
    arguments.setdefault("task", "do the work")
    reply = await call(client, "ai_start", **arguments)
    assert reply["ok"] is True, reply
    return reply


async def finish(client: Client[Any], job_ids: list[str], timeout_s: float = WAIT_S) -> list[dict[str, Any]]:
    """Wait for all jobs; their results in the order asked for."""
    done = await call(client, "ai_wait", job_ids=job_ids, mode="all", timeout_s=timeout_s)
    assert done["ok"] is True and not done["timed_out"], done
    by_id = {r["job_id"]: r for r in done["finished"]}
    return [by_id[i] for i in job_ids]


async def eventually(check: Callable[[], Any], timeout_s: float = WAIT_S) -> None:
    """Poll ``check`` (sync or async) until it is true."""
    deadline = time.monotonic() + timeout_s
    while True:
        outcome = check()
        if isinstance(outcome, Awaitable):
            outcome = await outcome
        if outcome:
            return
        assert time.monotonic() < deadline, "the condition did not come true in time"
        await asyncio.sleep(0.05)


async def pids_of(pid_file: Path) -> tuple[int, int]:
    """(fake CLI pid, the pid of the child it spawned) once the fake has written them."""
    await eventually(lambda: pid_file.exists() and len(pid_file.read_text().split()) == 2)
    parent, child = pid_file.read_text().split()
    return int(parent), int(child)


async def all_gone(*pids: int) -> None:
    def check() -> bool:
        for pid in pids:
            try:
                if psutil.Process(pid).status() != psutil.STATUS_ZOMBIE:
                    return False
            except psutil.NoSuchProcess:
                continue
        return True

    await eventually(check, timeout_s=10)


def spec(**fields: Any) -> AiJobSpec:
    fields.setdefault("task", "do the work")
    return AiJobSpec(**fields)


# --- exact results: the answer is the worker's, byte for byte, and only a file ------------------------------------

EXACT_TEXTS = [
    "plain answer",
    "  leading and trailing whitespace \n\n",
    "windows\r\nline\r\nends\r\n",
    "tabs\tand unicode: ünïcödé, 日本語, emoji 🧪🚀",
    '{"looks": "like json", "but": [1, 2, 3]}',
    "\x1b[32mansi escape kept\x1b[0m",
]


@pytest.mark.parametrize("text", EXACT_TEXTS)
async def test_the_answer_comes_back_byte_for_byte(aip: Aip, text: str) -> None:
    aip.harness.set_response(stdout=claude_ok(text))
    async with aip.client() as client:
        started = await start(client, ai="claude")
        (result,) = await finish(client, [started["job_id"]])

    assert result["ok"] is True and result["text"] == text and result["text_truncated"] is False
    assert Path(result["result_path"]).read_bytes() == text.encode("utf-8")  # the file is the exact answer


async def test_ai_start_returns_at_once_while_the_worker_is_still_running(aip: Aip) -> None:
    aip.harness.set_response(stdout=claude_ok("slow answer"), delay_s=1.5)
    async with aip.client() as client:
        started = await start(client, ai="claude")
        assert started["state"] == "queued" and started["account"] == "claude-02"
        assert set(started) >= {"job_id", "conversation_id", "account", "ai", "turn"}
        early = (await call(client, "ai_status", job_ids=[started["job_id"]]))["jobs"][0]
        assert early["state"] in ("queued", "running") and early["tokens"] is None  # nothing known until it ends
        (result,) = await finish(client, [started["job_id"]])
    assert result["text"] == "slow answer" and result["state"] == "succeeded"


async def test_nothing_of_the_task_or_the_answer_is_stored_in_the_database(aip: Aip) -> None:
    task, answer = f"TASK-{uuid4().hex}", f"ANSWER-{uuid4().hex}"
    aip.harness.set_response(stdout=claude_ok(answer))
    async with aip.client() as client:
        started = await start(client, task=task, ai="claude")
        (result,) = await finish(client, [started["job_id"]])
    assert result["text"] == answer

    tables = [r[0] for r in await fetch(aip.pool, "select tablename from pg_tables where schemaname = 'public'")]
    assert "ai_jobs" in tables and "ai_conversations" in tables
    for table in tables:
        for secret in (task, answer):
            hits = await fetch(aip.pool, f"select count(*) from public.{table} t where t::text like %s", f"%{secret}%")
            assert hits == [(0,)], f"{table} holds text that must stay out of the database"


async def test_a_result_over_the_inline_limit_is_a_file_with_a_preview(
    aip: Aip, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("FARM_AI_RESULT_INLINE_CHARS", "1000")
    big = "0123456789" * 3000  # 30,000 characters
    exact_limit, just_over = "x" * 1000, "y" * 1001
    async with aip.client() as client:
        results = []
        for answer in (big, exact_limit, just_over):
            aip.harness.set_response(stdout=claude_ok(answer))
            started = await start(client, ai="claude")
            (result,) = await finish(client, [started["job_id"]])
            results.append(result)
    huge, at_limit, over = results

    assert huge["text_truncated"] is True and huge["text"] == big[:20_000] and huge["result_chars"] == 30_000
    assert Path(huge["result_path"]).read_text(encoding="utf-8") == big  # the whole answer is in the file
    assert at_limit["text"] == exact_limit and at_limit["text_truncated"] is False  # up to the limit: inline
    assert over["text"] == just_over and over["result_chars"] == 1001  # over the limit but shorter than a preview
    assert Path(over["result_path"]).read_text(encoding="utf-8") == just_over


async def test_a_result_whose_file_was_deleted_says_so_instead_of_pretending(aip: Aip) -> None:
    aip.harness.set_response(stdout=claude_ok("here today"))
    async with aip.client() as client:
        started = await start(client, ai="claude")
        (result,) = await finish(client, [started["job_id"]])
        Path(result["result_path"]).unlink()  # someone cleaned the results folder
        again = await call(client, "ai_result", job_id=started["job_id"])
    assert result["note"] is None and result["text"] == "here today"
    assert again["ok"] is True and again["text"] is None and again["result_chars"] == len("here today")
    assert "is gone" in again["note"] and result["result_path"] in again["note"]


async def test_ai_result_of_a_job_that_is_still_running_says_so(aip: Aip) -> None:
    aip.harness.set_response(stdout=claude_ok("later"), delay_s=1.0)
    async with aip.client() as client:
        started = await start(client, ai="claude")
        early = await call(client, "ai_result", job_id=started["job_id"])
        assert early["ok"] is False and early["error"]["kind"] == "not_finished"
        await finish(client, [started["job_id"]])
        late = await call(client, "ai_result", job_id=started["job_id"])
    assert late["ok"] is True and late["text"] == "later"


SCHEMA = {"type": "object", "properties": {"n": {"type": "integer"}}, "required": ["n"]}


@pytest.mark.parametrize(
    ("answer", "valid", "parsed", "mentions"),
    [
        ('{"n": 3}', True, {"n": 3}, None),
        ('{"n": "three"}', False, {"n": "three"}, "n"),
        ("sorry, no json today", False, None, "not valid JSON"),
    ],
    ids=["valid", "violates-the-schema", "not-json"],
)
async def test_a_json_answer_is_checked_against_the_schema_and_the_raw_text_is_kept(
    aip: Aip, answer: str, valid: bool, parsed: Any, mentions: str | None
) -> None:
    aip.harness.set_response(stdout=claude_ok(answer))
    async with aip.client() as client:
        started = await start(client, ai="claude", json_schema=SCHEMA)
        (result,) = await finish(client, [started["job_id"]])
    assert result["ok"] is True and result["text"] == answer  # the raw text is kept whatever the check says
    assert result["json_valid"] is valid and result["json"] == parsed
    assert (not result["json_errors"]) if valid else (mentions in " ".join(result["json_errors"]))


async def test_usage_cost_and_whether_the_cost_is_estimated_are_recorded(aip: Aip) -> None:
    aip.harness.set_response(
        stdout="",
        per_account={
            "claude-02": {"stdout": claude_ok("c", cost=0.0123, usage=(100, 50))},
            "agy-01": {"stdout": agy_ok("g")},
        },
    )
    async with aip.client() as client:
        a = await start(client, ai="claude")
        b = await start(client, ai="gemini")
        ra, rb = await finish(client, [a["job_id"], b["job_id"]])
    assert (ra["tokens"], ra["usage"]["input_tokens"], ra["cost_usd"], ra["cost_estimated"]) == (150, 100.0, 0.0123, False)
    assert (rb["tokens"], rb["cost_usd"], rb["cost_estimated"]) == (10, 0.0, True)  # agy reports no cost
    assert ra["model"] is None and ra["duration_s"] is not None and ra["run_id"] and rb["run_id"]


# --- the queue: one job per account, per-account and per-pool limits ----------------------------------------------


async def test_status_shows_the_running_job_and_the_queue_behind_it(aip: Aip) -> None:
    aip.harness.set_response(stdout=claude_ok("x"), delay_s=1.5)
    async with aip.client() as client:
        ids = [(await start(client, ai="claude", account="claude-02"))["job_id"] for _ in range(3)]

        async def first_is_running() -> bool:
            return (await aip.job(ids[0]))["state"] == "running"

        await eventually(first_is_running)
        status = await call(client, "ai_status", job_ids=ids)
        states = [(j["state"], j["jobs_ahead"]) for j in status["jobs"]]
        assert states == [("running", None), ("queued", 1), ("queued", 2)]
        assert all(j["account"] == "claude-02" and j["tokens"] is None for j in status["jobs"])
        await call(client, "ai_cancel", job_id=ids[2])
        await call(client, "ai_cancel", job_id=ids[1])
        await call(client, "ai_cancel", job_id=ids[0])


async def test_one_job_at_a_time_per_account_by_default(aip: Aip) -> None:
    aip.harness.set_response(stdout=claude_ok("x"), delay_s=0.6)
    async with aip.client() as client:
        ids = [(await start(client, ai="claude", account="claude-02"))["job_id"] for _ in range(2)]
        await finish(client, ids)
    first, second = aip.harness.events()
    assert second["start"] >= first["end"]  # the second job waited for the account


async def test_an_account_that_allows_it_runs_that_many_jobs_at_once(aip_factory: Callable[..., Awaitable[Aip]]) -> None:
    aip = await aip_factory(claude_parallel=2)
    aip.harness.set_response(stdout=claude_ok("x"), delay_s=1.5)
    async with aip.client() as client:
        ids = [(await start(client, ai="claude", account="claude-02"))["job_id"] for _ in range(3)]
        await finish(client, ids)
    events = aip.harness.events()
    assert len(events) == 3
    assert events[1]["start"] < events[0]["end"]  # two ran together
    assert events[2]["start"] >= min(e["end"] for e in events[:2])  # the third waited for one of them


def acct(account_id: str, ai: str = "claude", **fields: Any) -> AccountInfo:
    defaults: dict[str, Any] = {
        "label": account_id,
        "status": "active",
        "priority": 1,
        "concurrency": 1,
        "scope": ("internal",),
        "meta": {"models": ["sonnet"]},
        "circuit": "closed",
        "cooldown_until": None,
        "next_reset_at": None,
        "last_error": None,
        "success_count": 1,
        "provider_enabled": True,
        "pool_max_parallel": None,
        "route_rank": 0,
    }
    return AccountInfo(id=account_id, ai=ai, **{**defaults, **fields})


@pytest.mark.parametrize(
    ("meta", "concurrency", "expected"),
    [
        ({}, 1, 1),  # nothing configured: the router's gate
        ({"max_parallel": 3}, 1, 1),  # cannot exceed what the router lets through
        ({"max_parallel": 3}, 4, 3),
        ({}, 2, 2),  # concurrency alone is the limit
        ({"max_parallel": 0}, 2, 2),  # nonsense falls back
        ({"max_parallel": True}, 2, 2),
        ({"max_parallel": "3"}, 2, 2),
    ],
)
def test_max_parallel_is_capped_by_the_routers_concurrency(meta: dict[str, Any], concurrency: int, expected: int) -> None:
    assert acct("claude-02", meta=meta, concurrency=concurrency).max_parallel == expected


async def test_a_pool_cap_limits_the_jobs_of_all_its_accounts(aip_factory: Callable[..., Awaitable[Aip]]) -> None:
    aip = await aip_factory(pool_cap=1)
    aip.harness.set_response(stdout=claude_ok("x"), delay_s=0.6)
    async with aip.client() as client:
        reply = await call(
            client, "ai_start_many", jobs=[{"task": "a", "ai": "claude"}, {"task": "b", "ai": "claude"}]
        )
        assert [j["account"] for j in reply["jobs"]] == ["claude-02", "claude-03"]  # two accounts ...
        await finish(client, [j["job_id"] for j in reply["jobs"]])
    first, second = aip.harness.events()
    assert second["start"] >= first["end"]  # ... but the pool takes one job at a time


async def test_a_job_waits_for_an_account_that_a_blocking_call_holds_instead_of_failing(aip: Aip) -> None:
    aip.harness.set_response(stdout=claude_ok("shared"), delay_s=1.0)
    async with aip.client() as client:
        blocking = asyncio.create_task(client.call_tool("ask_ai", {"ai": "claude", "task": "blocking"}))
        await eventually(lambda: aip.harness.events())  # the blocking call is running on claude-02
        started = await start(client, ai="claude", account="claude-02")
        (result,) = await finish(client, [started["job_id"]])
        reply = await blocking
    assert result["ok"] is True and result["text"] == "shared"
    assert reply.structured_content is not None and reply.structured_content["ok"] is True


# --- picking accounts ------------------------------------------------------------------------------------------------


def test_the_best_account_is_first_in_route_order_then_idle_then_by_priority() -> None:
    accounts = [
        acct("claude-03", priority=2),
        acct("claude-02", priority=1, active=1),  # busy: the idle one goes first
        acct("agy-01", "gemini", route_rank=1),
    ]
    assert [a.id for a in rank(accounts)] == ["claude-03", "claude-02", "agy-01"]
    assert [a.id for a in rank(accounts, {"claude-03": 1})] == ["claude-02", "claude-03", "agy-01"]  # both busy: priority


def test_distinct_accounts_go_to_different_accounts_in_route_order() -> None:
    accounts = [
        acct("claude-02", priority=1),
        acct("claude-03", priority=2),
        acct("claude-04", priority=3),
        acct("agy-01", "gemini", route_rank=1),
        acct("agy-02", "gemini", route_rank=1, priority=2),
    ]
    wishes = [Wish("claude", None, None)] * 3 + [Wish("gemini", None, None)] * 2
    plan = plan_accounts(accounts, wishes, distinct=True, now=START)
    assert [a.id for a in plan] == ["claude-02", "claude-03", "claude-04", "agy-01", "agy-02"]
    anywhere = plan_accounts(accounts, [Wish("any", None, None)] * 5, distinct=True, now=START)
    assert [a.id for a in anywhere] == ["claude-02", "claude-03", "claude-04", "agy-01", "agy-02"]


def test_without_distinct_accounts_jobs_spread_over_the_accounts_then_queue() -> None:
    accounts = [acct("claude-02", priority=1), acct("claude-03", priority=2)]
    plan = plan_accounts(accounts, [Wish("claude", None, None)] * 3, distinct=False, now=START)
    assert [a.id for a in plan] == ["claude-02", "claude-03", "claude-02"]


def test_not_enough_usable_accounts_is_refused_and_says_which_are_missing_and_why() -> None:
    accounts = [
        acct("claude-02"),
        acct("claude-03"),
        acct("claude-04", status="needs_login"),
        acct("agy-01", "gemini", route_rank=1),
        acct("agy-02", "gemini", route_rank=1, status="exhausted", cooldown_until=RESET_AT),
    ]
    wishes = [Wish("claude", None, None)] * 3 + [Wish("gemini", None, None)] * 2
    with pytest.raises(AiRequestError) as refused:
        plan_accounts(accounts, wishes, distinct=True, now=START)
    error = refused.value
    assert error.kind == "not_enough_accounts"
    assert "need 3 distinct claude account(s), only 2 usable (claude-02, claude-03" in error.message
    assert "claude-04 (needs_login)" in error.message
    assert "need 2 distinct gemini account(s), only 1 usable (agy-01" in error.message
    assert "agy-02 (exhausted, back 2026-10-04 18:00Z)" in error.message


def test_named_accounts_must_exist_be_usable_and_not_repeat_when_distinct() -> None:
    accounts = [acct("claude-02"), acct("claude-03", status="paused"), acct("agy-01", "gemini")]
    cases = [
        ([Wish("claude", None, "claude-09")], "does not exist"),
        ([Wish("claude", None, "agy-01")], "is a gemini account, not claude"),
        ([Wish("claude", None, "claude-03")], "unavailable (paused)"),
        ([Wish("claude", None, "claude-02"), Wish("any", None, "claude-02")], "already used by another job"),
        ([Wish("claude", "opus", None)], "offering model 'opus'"),
    ]
    for wishes, expected in cases:
        with pytest.raises(AiRequestError) as refused:
            plan_accounts(accounts, wishes, distinct=True, now=START)
        assert expected in refused.value.message, (wishes, refused.value.message)


@pytest.mark.parametrize(
    ("fields", "reason"),
    [
        ({"status": "needs_login"}, "needs_login"),
        ({"status": "paused"}, "paused"),
        ({"status": "disabled"}, "disabled"),
        ({"status": "exhausted", "cooldown_until": RESET_AT}, "exhausted"),
        ({"circuit": "open", "cooldown_until": RESET_AT}, "circuit_open"),
        ({"cooldown_until": RESET_AT}, "cooldown"),
        ({"provider_enabled": False}, "provider_disabled"),
        ({"scope": ("client:acme",)}, "scope"),
    ],
)
def test_an_account_the_router_would_refuse_is_never_chosen(fields: dict[str, Any], reason: str) -> None:
    broken = acct("claude-02", **fields)
    assert broken.unusable_reason(START) == reason

def test_an_exhausted_account_is_usable_again_once_its_reset_has_passed_and_the_model_must_be_offered() -> None:
    back = acct("claude-02", status="exhausted", cooldown_until=START - timedelta(minutes=1))
    assert back.unusable_reason(START) is None
    assert acct("claude-02").unusable_reason(START, model="gpt-9") == "model_not_offered"
    assert acct("claude-02").unusable_reason(START, model="SONNET") is None  # model names compare case-insensitively


async def test_start_without_a_usable_account_is_refused_with_the_reason_and_the_time_it_is_back(aip: Aip) -> None:
    async with aip.pool.connection() as conn:
        for account in ("claude-02", "claude-03", "claude-04"):
            await conn.execute("update public.connections set status = 'exhausted' where id = %s", (account,))
            await conn.execute(
                "insert into public.connection_health (connection_id, cooldown_until) values (%s, %s) "
                "on conflict (connection_id) do update set cooldown_until = excluded.cooldown_until",
                (account, RESET_AT),
            )
    async with aip.client() as client:
        refused = await call(client, "ai_start", task="t", ai="claude")
        assert refused["ok"] is False
        error = refused["error"]
        assert error["kind"] == "account_unavailable" and error["ai"] == "claude"
        assert datetime.fromisoformat(error["retry_at"]) == RESET_AT
        assert "claude-02 (exhausted, back" in error["message"]
        wrong_model = await call(client, "ai_start", task="t", ai="claude", model="gpt-9")
        assert wrong_model["error"]["kind"] == "account_unavailable"
    assert await fetch(aip.pool, "select count(*) from public.ai_jobs") == [(0,)]


async def test_a_batch_that_cannot_get_its_distinct_accounts_starts_nothing(aip: Aip) -> None:
    async with aip.pool.connection() as conn:
        await conn.execute("update public.connections set status = 'needs_login' where id = 'claude-04'")
    async with aip.client() as client:
        refused = await call(
            client, "ai_start_many", jobs=[{"task": f"job {i}", "ai": "claude"} for i in range(3)]
        )
    assert refused["ok"] is False and refused["error"]["kind"] == "not_enough_accounts"
    assert "only 2 usable (claude-02, claude-03" in refused["error"]["message"]
    assert "claude-04 (needs_login)" in refused["error"]["message"]
    assert await fetch(aip.pool, "select count(*) from public.ai_jobs") == [(0,)]
    assert aip.harness.events() == []


# --- failures: which worker, why, and when it is back ------------------------------------------------------------


def route_error(kind: str, message: str = "boom", *attempts: AttemptSummary) -> RouteError:
    return RouteError(kind=kind, message=message, hint="", attempts=list(attempts))


def skipped(reason: str) -> AttemptSummary:
    return AttemptSummary(
        provider="claude", connection_id="claude-02", outcome="skipped", kind=reason, message=reason
    )


@pytest.mark.parametrize(
    ("error", "replying", "kind", "cause"),
    [
        (route_error("limit_reached"), False, "limit", None),
        (route_error("rate_limited"), False, "limit", None),
        (route_error("auth"), False, "auth", None),
        (route_error("needs_login"), False, "auth", None),
        (route_error("timeout"), False, "timeout", None),
        (route_error("bad_request"), False, "bad_request", None),
        (route_error("session_unknown"), True, "bad_request", None),
        (route_error("server"), False, "crash", None),
        (route_error("unknown"), False, "crash", None),
        (route_error("internal"), False, "crash", None),
        (route_error("empty"), False, "crash", None),
        (route_error("policy_blocked"), False, "account_unavailable", "policy_blocked"),
        (route_error("session_account_unavailable", "x is unavailable (exhausted)"), True, "account_unavailable", "exhausted"),
        (route_error("no_capacity", "none", skipped("exhausted")), False, "account_unavailable", "exhausted"),
        (route_error("no_capacity", "none", skipped("model_not_offered")), False, "bad_request", None),
        (route_error("limit_reached"), True, "account_unavailable", "limit"),  # a reply cannot move: wait
        (route_error("auth"), True, "account_unavailable", "auth"),
        (route_error("timeout"), True, "timeout", None),
    ],
)
def test_a_router_error_becomes_one_of_the_failure_kinds(
    error: RouteError, replying: bool, kind: str, cause: str | None
) -> None:
    verdict = classify(error, account="claude-02", replying=replying)
    assert isinstance(verdict, Failure)
    assert (verdict.kind, verdict.cause) == (kind, cause)
    if verdict.kind == "auth":
        assert "farm ai login claude-02" in verdict.message


def test_quota_exhaustion_is_a_limit_and_a_gate_taken_by_another_call_is_not_a_failure() -> None:
    quota = AttemptSummary(
        provider="claude",
        connection_id="claude-02",
        outcome="reserve_failed",
        kind="limit_reached",
        message="not enough 'requests' left for one call",
    )
    verdict = classify(route_error("no_capacity", "none", quota), account="claude-02", replying=False)
    assert isinstance(verdict, Failure) and (verdict.kind, verdict.cause) == ("limit", "quota")
    assert "quota is used up" in verdict.message
    for reason in ("concurrency_limit", "saturated"):
        assert isinstance(classify(route_error("no_capacity", "n", skipped(reason)), account="a", replying=False), Busy)
        assert isinstance(
            classify(route_error("session_account_unavailable", "n", skipped(reason)), account="a", replying=True), Busy
        )
    mixed = classify(route_error("no_capacity", "n", skipped("saturated"), skipped("cooldown")), account="a", replying=False)
    assert isinstance(mixed, Failure)  # a skip for another reason means the account really is out


@pytest.mark.parametrize(
    ("config", "kind", "message", "retry"),
    [
        ({"stdout": claude_limit(), "exit_code": 1}, "limit", "Usage limit reached", RESET_AT),
        ({"stdout": json.dumps({"result": "Not logged in", "is_error": True}), "exit_code": 1}, "auth", "farm ai login", None),
        ({"stdout": "kaboom, not json", "stderr": "segfault", "exit_code": 3}, "crash", "segfault", None),
    ],
    ids=["limit", "auth", "crash"],
)
async def test_a_failed_job_says_which_worker_failed_why_and_when_it_is_back(
    aip: Aip, config: dict[str, Any], kind: str, message: str, retry: datetime | None
) -> None:
    aip.harness.set_response(per_account={"claude-02": config})
    async with aip.client() as client:
        started = await start(client, ai="claude", account="claude-02")
        reply = await call(client, "ai_wait", job_ids=[started["job_id"]], mode="all", timeout_s=WAIT_S)
        (result,) = reply["finished"]
        via_result = await client.call_tool("ai_result", {"job_id": started["job_id"]}, raise_on_error=False)
    assert result["ok"] is False and result["state"] == "failed" and result["text"] is None
    error = result["error"]
    assert (error["kind"], error["ai"], error["account"]) == (kind, "claude", "claude-02")
    assert message in error["message"]
    assert (datetime.fromisoformat(error["retry_at"]) if error["retry_at"] else None) == retry
    assert via_result.is_error is True and via_result.structured_content is not None  # a failure is an error result


async def test_a_job_that_runs_past_its_timeout_fails_as_timeout_and_the_process_is_killed(aip: Aip) -> None:
    pid_file = aip.tmp / "timeout.pids"
    aip.harness.set_response(
        per_account={"claude-02": {"stdout": claude_ok("late"), "delay_s": 60, "pid_file": str(pid_file)}}
    )
    async with aip.client() as client:
        started = await start(client, ai="claude", account="claude-02", timeout_s=1)
        (result,) = await finish(client, [started["job_id"]], timeout_s=20)
    assert result["error"]["kind"] == "timeout" and result["error"]["account"] == "claude-02"
    await all_gone(*await pids_of(pid_file))  # the CLI and its child, killed together


async def test_edit_outside_the_allowed_roots_is_a_bad_request_and_runs_no_process(
    aip_factory: Callable[..., Awaitable[Aip]],
) -> None:
    roots = Path(os.environ["FARM_DATA_DIR"]) / "workspaces"
    aip = await aip_factory(edit_root=roots)
    roots.mkdir(parents=True)
    outside = aip.tmp / "outside"
    outside.mkdir()
    async with aip.client() as client:
        not_allowed = await start(client, ai="claude", account="claude-03", mode="edit", cwd=str(roots))
        elsewhere = await start(client, ai="claude", account="claude-02", mode="edit", cwd=str(outside))
        first, second = await finish(client, [not_allowed["job_id"], elsewhere["job_id"]])
    assert first["error"]["kind"] == second["error"]["kind"] == "bad_request"
    assert "allow_edit" in first["error"]["message"] and "outside allowed edit roots" in second["error"]["message"]
    assert aip.harness.events() == []  # SEC1: the executor refused before any process started


@pytest.mark.skipif(shutil.which("git") is None, reason="needs git")
async def test_an_edit_reports_the_files_it_changed_when_the_cwd_is_a_repository(
    aip_factory: Callable[..., Awaitable[Aip]],
) -> None:
    roots = Path(os.environ["FARM_DATA_DIR"]) / "workspaces"
    aip = await aip_factory(edit_root=roots)
    repo, plain = roots / "repo", roots / "plain"
    repo.mkdir(parents=True)
    plain.mkdir()
    git = ["git", "-c", "user.name=t", "-c", "user.email=t@example.com"]
    subprocess.run([*git, "init", "-q"], cwd=repo, check=True)
    (repo / "tracked.txt").write_text("v1", encoding="utf-8")
    subprocess.run([*git, "add", "."], cwd=repo, check=True)
    subprocess.run([*git, "commit", "-qm", "base"], cwd=repo, check=True)
    aip.harness.set_response(per_account={"claude-02": {"stdout": claude_ok("edited"), "write_file": "new.txt"}})
    async with aip.client() as client:
        in_repo = await start(client, ai="claude", account="claude-02", mode="edit", cwd=str(repo))
        not_repo = await start(client, ai="claude", account="claude-02", mode="edit", cwd=str(plain))
        asked = await start(client, ai="claude", account="claude-03", mode="answer")
        r1, r2, r3 = await finish(client, [in_repo["job_id"], not_repo["job_id"], asked["job_id"]])
    assert r1["files_changed"] == ["?? new.txt"]
    assert r2["files_changed"] is None and r3["files_changed"] is None  # not a repository / not an edit
    assert (repo / "new.txt").read_text(encoding="utf-8") == "changed by the worker"


async def test_retry_other_account_is_refused_for_an_edit(aip: Aip) -> None:
    async with aip.client() as client:
        refused = await call(client, "ai_start", task="t", ai="claude", mode="edit", cwd=str(aip.tmp), retry_other_account=True)
    assert refused["ok"] is False and refused["error"]["kind"] == "bad_request"
    assert "retry_other_account" in refused["error"]["message"]


# --- quota: every job reserves and commits through the router ----------------------------------------------------


async def test_a_job_reserves_and_commits_quota_through_the_router_and_an_empty_account_is_a_limit(
    aip_factory: Callable[..., Awaitable[Aip]],
) -> None:
    aip = await aip_factory(limit=2)
    aip.harness.set_response(stdout=claude_ok("ok"))
    async with aip.client() as client:
        for _ in range(2):
            started = await start(client, ai="claude", account="claude-02")
            await finish(client, [started["job_id"]])
        assert await aip.quota("claude-02") == (2, 0)  # committed, nothing left reserved
        assert await aip.reservations("claude-02") == ["committed", "committed"]

        started = await start(client, ai="claude", account="claude-02")
        (empty,) = await finish(client, [started["job_id"]])
    assert empty["error"]["kind"] == "limit" and "quota is used up" in empty["error"]["message"]
    assert len(aip.harness.events()) == 2  # the third call never reached the CLI
    run = await aip.job(started["job_id"])
    assert run["state"] == "failed" and await aip.quota("claude-02") == (2, 0)


# --- cancelling and stopping ----------------------------------------------------------------------------------------


async def test_cancelling_a_queued_job_withdraws_it_before_it_ever_runs(aip: Aip) -> None:
    aip.harness.set_response(stdout=claude_ok("x"), delay_s=1.0)
    async with aip.client() as client:
        first = await start(client, ai="claude", account="claude-02")
        second = await start(client, ai="claude", account="claude-02")
        cancelled = await call(client, "ai_cancel", job_id=second["job_id"])
        assert cancelled["ok"] is True and cancelled["cancelled"] is True and cancelled["state"] == "cancelled"
        (done,) = await finish(client, [first["job_id"]])
        (gone_job,) = await finish(client, [second["job_id"]])
    assert done["ok"] is True
    assert gone_job["state"] == "cancelled" and gone_job["error"]["kind"] == "cancelled"
    assert len(aip.harness.events()) == 1  # the withdrawn job never started a CLI


async def test_cancelling_a_job_that_has_finished_reports_it_as_it_is(aip: Aip) -> None:
    aip.harness.set_response(stdout=claude_ok("x"))
    async with aip.client() as client:
        started = await start(client, ai="claude")
        await finish(client, [started["job_id"]])
        again = await call(client, "ai_cancel", job_id=started["job_id"])
        unknown = await call(client, "ai_cancel", job_id=str(uuid4()))
    assert again["ok"] is True and again["cancelled"] is False and again["state"] == "succeeded"
    assert "already finished" in again["message"]
    assert unknown["ok"] is False and unknown["error"]["kind"] == "not_found"


async def test_a_cancel_written_by_another_process_is_picked_up_by_the_owner(aip: Aip) -> None:
    owner = JobManager(aip.ctx, tick_s=0.05)
    aip.ctx.executors["ai-jobs:fast"] = ManagerHook(owner)  # closed with the context
    pid_file = aip.tmp / "remote.pids"
    aip.harness.set_response(
        per_account={"claude-02": {"stdout": claude_ok("x"), "delay_s": 60, "pid_file": str(pid_file)}}
    )
    started = await owner.start(spec(ai="claude", account="claude-02"), caller="test")
    pids = await pids_of(pid_file)
    # `farm ai cancel` is another process: all it can do is write the request
    await JobStore(aip.pool).request_cancel(started.job_id, aip.clock())

    async def cancelled() -> bool:
        return (await aip.job(str(started.job_id)))["state"] == "cancelled"

    await eventually(cancelled)
    await all_gone(*pids)


async def test_shutting_down_ends_running_and_queued_jobs_as_farm_restart_and_kills_the_processes(aip: Aip) -> None:
    pid_file = aip.tmp / "shutdown.pids"
    aip.harness.set_response(
        per_account={"claude-02": {"stdout": claude_ok("x"), "delay_s": 60, "pid_file": str(pid_file)}}
    )
    running = await aip.manager.start(spec(ai="claude", account="claude-02"), caller="test")
    queued = await aip.manager.start(spec(ai="claude", account="claude-02"), caller="test")
    pids = await pids_of(pid_file)

    await aip.ctx.aclose()  # what stopping the Farm does: the manager is closed with the context

    for job in (running, queued):
        row = await aip.job(str(job.job_id))
        assert (row["state"], row["error_kind"]) == ("failed", "farm_restart")
    await all_gone(*pids)
    assert await aip.reservations("claude-02") == ["committed"]  # the interrupted attempt is settled, not left open
    with pytest.raises(AiRequestError, match="shutting down"):
        await aip.manager.start(spec(ai="claude"), caller="test")


async def test_a_dead_owners_jobs_fail_as_farm_restart_a_live_owners_do_not_and_a_late_finish_cannot_overwrite(
    aip: Aip,
) -> None:
    dead = JobManager(aip.ctx, heartbeat_s=3600)  # in this test it never renews its heartbeat: it "crashed"
    live = JobManager(aip.ctx, heartbeat_s=3600)
    for index, manager in enumerate((dead, live)):
        aip.ctx.executors[f"ai-jobs:{index}"] = ManagerHook(manager)
    aip.harness.set_response(
        per_account={
            "claude-02": {"stdout": claude_ok("from the dead owner"), "delay_s": 1.5},
            "claude-03": {"stdout": claude_ok("from the live owner"), "delay_s": 1.5},
        }
    )
    orphan = await dead.start(spec(ai="claude", account="claude-02"), caller="test")

    async def orphan_is_running() -> bool:
        return (await aip.job(str(orphan.job_id)))["state"] == "running"

    await eventually(orphan_is_running)
    aip.clock.advance(120)  # two minutes pass; `dead` renewed nothing
    healthy = await live.start(spec(ai="claude", account="claude-03"), caller="test")

    assert await live.recover() == 1  # exactly the orphan, not the job whose owner is alive
    row = await aip.job(str(orphan.job_id))
    assert (row["state"], row["error_kind"]) == ("failed", "farm_restart")
    assert (await aip.job(str(healthy.job_id)))["state"] in ("queued", "running")

    await dead.drain()  # the "dead" process still finishes its CLI call, and must not overwrite the verdict
    await live.drain()
    row = await aip.job(str(orphan.job_id))
    assert (row["state"], row["error_kind"], row["result_path"]) == ("failed", "farm_restart", None)
    assert not (Path(os.environ["FARM_DATA_DIR"]) / "ai-results" / f"{orphan.job_id}.txt").exists()
    assert (await aip.job(str(healthy.job_id)))["state"] == "succeeded"
    assert await live.recover() == 0 and await dead.recover() == 0  # nothing left to sweep


async def test_a_manager_never_fails_its_own_jobs_however_old_their_heartbeat(aip: Aip) -> None:
    aip.harness.set_response(stdout=claude_ok("mine"), delay_s=1.0)
    started = await aip.manager.start(spec(ai="claude", account="claude-02"), caller="test")
    aip.clock.advance(3600)
    assert await aip.manager.recover() == 0
    await aip.manager.drain()
    assert (await aip.job(str(started.job_id)))["state"] == "succeeded"


async def test_one_queued_or_running_job_per_conversation(aip: Aip) -> None:
    aip.harness.set_response(stdout=claude_ok("x"), delay_s=1.0)
    async with aip.client() as client:
        first = await start(client, ai="claude", account="claude-02")
        busy = await call(client, "ai_start", task="again", conversation_id=first["conversation_id"])
        assert busy["ok"] is False and busy["error"]["kind"] == "conversation_busy"
        assert await fetch(aip.pool, "select count(*) from public.ai_jobs") == [(1,)]
        await finish(client, [first["job_id"]])


# --- SEC1: ids, flags, environment ------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "value",
    ["../../etc/passwd", r"..\..\x", "a/b", "", "not-a-uuid", "{12345678-1234-5678-1234-567812345678}",
     "12345678123456781234567812345678", "urn:uuid:12345678-1234-5678-1234-567812345678",
     "12345678-1234-5678-1234-567812345678/../x", "12345678-1234-5678-1234-56781234567"],
)
def test_only_a_canonical_uuid_is_an_id(value: str) -> None:
    with pytest.raises(ValueError, match="must be a UUID"):
        parse_uuid(value, "job_id")


def test_a_canonical_uuid_is_accepted_in_either_case() -> None:
    wanted = uuid4()
    assert parse_uuid(str(wanted), "job_id") == wanted
    assert parse_uuid(str(wanted).upper(), "job_id") == wanted


async def test_ids_and_arguments_are_validated_before_anything_runs(aip: Aip) -> None:
    traversal = ["../../x", r"..\..\x", "12345678-1234-5678-1234-567812345678/../x"]
    async with aip.client() as client:
        for bad in traversal:
            for tool, arguments in (
                ("ai_status", {"job_ids": [bad]}),
                ("ai_wait", {"job_ids": [bad]}),
                ("ai_result", {"job_id": bad}),
                ("ai_cancel", {"job_id": bad}),
                ("ai_reply", {"conversation_id": bad, "message": "m"}),
                ("ai_start", {"task": "t", "conversation_id": bad}),
            ):
                reply = await call(client, tool, **arguments)
                assert reply["ok"] is False and reply["error"]["kind"] == "bad_request", (tool, bad, reply)
        flag = await call(client, "ai_start", task="t", ai="claude", model="--dangerously-skip-permissions")
        assert flag["ok"] is False and flag["error"]["kind"] == "bad_request" and "model" in flag["error"]["message"]
        for account in ("../x", "UPPER", "a b", "claude-02; rm -rf /"):
            raw = await client.call_tool("ai_start", {"task": "t", "account": account}, raise_on_error=False)
            assert raw.is_error is True, account  # refused by the tool's own schema
    assert await fetch(aip.pool, "select count(*) from public.ai_jobs") == [(0,)]
    assert aip.harness.events() == []


async def test_the_child_environment_of_a_job_has_no_farm_secrets(aip: Aip, monkeypatch: pytest.MonkeyPatch) -> None:
    secret_pw = "pass" + "word789"
    monkeypatch.setenv("FARM_DB_URL", f"postgres://farm:{secret_pw}" + "@" + "127.0.0.1:5432/farm")  # (not a literal URL)
    monkeypatch.setenv("SUPABASE_SERVICE_ROLE_KEY", "service-role-secret")
    monkeypatch.setenv("BIFROST_FARM_VK", "vk-secret")
    aip.harness.set_response(stdout=claude_ok("ok"))
    async with aip.client() as client:
        started = await start(client, ai="claude", account="claude-03")
        await finish(client, [started["job_id"]])
    (event,) = aip.harness.events()
    env = event["env"]
    assert not {"FARM_DB_URL", "SUPABASE_SERVICE_ROLE_KEY", "BIFROST_FARM_VK"} & set(env)
    assert all(secret_pw not in value and "service-role-secret" not in value for value in env.values())
    assert env["NO_COLOR"] == "1" and event["claude_dir"] == str(aip.tmp / "claude-03")


# --- the tools, the accounts report and the CLI ------------------------------------------------------------------------


async def test_the_ai_tools_are_listed_beside_the_capability_and_infra_tools(aip: Aip) -> None:
    async with aip.client() as client:
        tools = {t.name: t for t in await client.list_tools()}
    assert set(AI_TOOLS) <= set(tools) and set(INFRA_TOOLS) <= set(tools) and "ask_ai" in tools
    assert all(tools[name].description for name in AI_TOOLS)
    assert tools["ai_start"].input_schema["required"] == ["task"]
    assert "jobs" in tools["ai_start_many"].input_schema["properties"]
    assert tools["ai_status"].annotations is not None and tools["ai_status"].annotations.read_only_hint is True


async def test_the_blocking_ask_tools_still_work_beside_the_job_tools(aip: Aip) -> None:
    aip.harness.set_response(stdout=claude_ok("blocking answer"))
    async with aip.client() as client:
        single = await call(client, "ask_ai", ai="claude", task="t")
        batch = await call(client, "ask_ai_batch", tasks=[{"ai": "claude", "task": "a"}, {"ai": "claude", "task": "b"}])
    assert single["ok"] is True and single["result"]["text"] == "blocking answer"
    assert batch["ok"] is True and [t["result"]["text"] for t in batch["tasks"]] == ["blocking answer"] * 2


async def test_list_ais_shows_the_job_load_the_limits_and_the_login_state(
    aip_factory: Callable[..., Awaitable[Aip]],
) -> None:
    aip = await aip_factory(pool_cap=2)
    aip.harness.set_response(stdout=claude_ok("x"), delay_s=1.5)
    async with aip.client() as client:
        a = await start(client, ai="claude", account="claude-02")
        b = await start(client, ai="claude", account="claude-02")

        async def running() -> bool:
            return (await aip.job(a["job_id"]))["state"] == "running"

        await eventually(running)
        report = await call(client, "list_ais")
        accounts = {acc["id"]: acc for acc in report["accounts"]}
        pool = next(ai for ai in report["ais"] if ai["id"] == "claude")
        await finish(client, [a["job_id"], b["job_id"]])
        after = {acc["id"]: acc for acc in (await call(client, "list_ais"))["accounts"]}
    busy = accounts["claude-02"]
    assert (busy["active_jobs"], busy["queued_jobs"], busy["max_parallel"]) == (1, 1, 1)
    assert busy["login_state"] == "unknown" and accounts["claude-03"]["login_state"] == "unknown"
    assert (pool["max_parallel"], pool["active_jobs"], pool["queued_jobs"]) == (2, 1, 1)
    assert after["claude-02"]["login_state"] == "ok" and after["claude-02"]["active_jobs"] == 0
    # what list_ais showed before this round is still there
    assert {"status", "models", "circuit", "reset_at", "next_reset_at", "todays_calls"} <= set(busy)


def test_farm_ai_list_has_a_jobs_column() -> None:
    table = format_ai_list(
        {
            "ais": [
                {
                    "id": "claude",
                    "accounts": [
                        {"id": "claude-02", "status": "active", "models": ["sonnet"], "todays_calls": 3,
                         "active_jobs": 1, "queued_jobs": 2, "max_parallel": 1},
                        {"id": "claude-03", "status": "active", "models": [], "active_jobs": 0, "queued_jobs": 0,
                         "max_parallel": 2},
                    ],
                }
            ]
        }
    )
    lines = table.splitlines()
    assert lines[0].split()[-1] == "JOBS"
    assert lines[1].endswith("1/1 +2 queued") and lines[2].endswith("0/2")


def _seed_cli_database(url: str, tmp: Path) -> dict[str, str]:
    """A succeeded, a failed and a running job (whose owner died long ago), as the Farm leaves them."""
    ids = {name: str(uuid4()) for name in ("done", "failed", "orphan")}
    result = tmp / "data" / "ai-results" / f"{ids['done']}.txt"
    result.parent.mkdir(parents=True)
    result.write_bytes(b"The exact answer.\nSecond line.")  # bytes: no newline translation on Windows
    with psycopg.connect(url, autocommit=True) as conn:
        for name, state, kind in (("done", "succeeded", None), ("failed", "failed", "limit"), ("orphan", "running", None)):
            conversation = conn.execute(
                "insert into public.ai_conversations (ai, account) values ('claude', 'claude-02') returning id"
            ).fetchone()
            assert conversation is not None
            conn.execute(
                "insert into public.ai_jobs (id, conversation_id, turn, ai, account, model, state, error_kind, "
                "error_message, retry_at, tokens, cost_usd, cost_estimated, result_path, result_chars, owner_id, "
                "heartbeat_at, attempts, native_session_id) values (%s, %s, 1, 'claude', 'claude-02', 'sonnet', %s, %s, "
                "%s, %s, %s, 0.0123, false, %s, %s, %s, '2020-01-01', %s::jsonb, 'sess-1')",
                (
                    ids[name], conversation[0], state, kind, "usage limit reached" if kind else None,
                    "2026-10-05 18:00+00" if kind else None, 150 if name == "done" else None,
                    str(result) if name == "done" else None, 30 if name == "done" else None, str(uuid4()),
                    json.dumps([{"n": 1, "account": "claude-02", "outcome": "failed", "kind": "limit",
                                 "message": "usage limit reached"}]),
                ),
            )
    return ids


def test_farm_ai_jobs_show_and_cancel(
    scratch_db: Callable[[], str], monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    url = scratch_db()
    command.upgrade(alembic_config(url), "head")
    monkeypatch.setenv("FARM_DB_URL", url)
    monkeypatch.setenv("FARM_DATA_DIR", str(tmp_path / "data"))
    ids = _seed_cli_database(url, tmp_path)
    run = CliRunner()

    listing = run.invoke(app, ["ai", "jobs"])
    assert listing.exit_code == 0, listing.output
    assert all(i in listing.output for i in ids.values()) and "limit: usage limit reached" in listing.output
    only_failed = run.invoke(app, ["ai", "jobs", "--state", "failed"])
    assert ids["failed"] in only_failed.output and ids["done"] not in only_failed.output
    assert run.invoke(app, ["ai", "jobs", "--state", "bogus"]).exit_code == 2

    shown = run.invoke(app, ["ai", "show", ids["done"]])
    assert shown.exit_code == 0, shown.output
    assert "succeeded" in shown.output and "claude / claude-02" in shown.output and "150" in shown.output
    assert "The exact answer.\nSecond line." in shown.output
    failed = run.invoke(app, ["ai", "show", ids["failed"]])
    assert "error        limit (retry at 2026-10-05 18:00:00Z): usage limit reached" in failed.output
    assert "attempt 1" in failed.output
    assert run.invoke(app, ["ai", "show", "../../x"]).exit_code == 2
    assert run.invoke(app, ["ai", "show", str(uuid4())]).exit_code == 1

    # the orphan's owner stopped renewing its heartbeat in 2020: cancelling resolves it instead of waiting forever
    cancelled = run.invoke(app, ["ai", "cancel", ids["orphan"]])
    assert cancelled.exit_code == 0 and "already finished (failed)" in cancelled.output
    assert run.invoke(app, ["ai", "cancel", ids["done"]]).output.strip().endswith("already finished (succeeded)")
    assert run.invoke(app, ["ai", "cancel", "nope"]).exit_code == 2
    with psycopg.connect(url, autocommit=True) as conn:
        row = conn.execute(
            "select state, error_kind from public.ai_jobs where id = %s", (ids["orphan"],)
        ).fetchone()
    assert row == ("failed", "farm_restart")


# --- per-account homes of the Codex and agy executors ----------------------------------------------------------------------


async def test_each_codex_and_agy_connection_runs_with_its_own_home(tmp_path: Path) -> None:
    harness = FakeCliHarness(tmp_path / "bin")
    codex_bin, agy_bin = harness.register_cli("codex"), harness.register_cli("agy")
    harness.set_response(stdout=json.dumps({"type": "item.completed", "item": {"text": "ok"}}))

    def request(connection: ConnectionView) -> ExecRequest:
        return ExecRequest(request_id=uuid4(), capability="ask_ai", params={"task": "t"}, connection=connection)

    codex = CodexCliExecutor()
    for meta in ({"home": str(tmp_path / "codex-02")}, {"config_dir": str(tmp_path / "codex-03")}):
        harness.clear_calls()
        view = ConnectionView(id="codex-x", provider_id="codex", auth_ref="cli:x", meta={"cli_path": str(codex_bin), **meta})
        assert (await codex.execute(request(view))).ok
        assert harness.get_calls()[0]["env"]["CODEX_HOME"] == next(iter(meta.values()))  # home, or the older config_dir

    harness.set_response(stdout=agy_ok("ok"))
    agy = AgyCliExecutor()
    harness.clear_calls()
    home = str(tmp_path / "agy-02")
    view = ConnectionView(id="agy-02", provider_id="gemini", auth_ref="cli:x", meta={"cli_path": str(agy_bin), "home": home})
    assert (await agy.execute(request(view))).ok
    env = harness.get_calls()[0]["full_env"]
    assert env["USERPROFILE"] == home and env["HOME"] == home

    harness.clear_calls()
    relative = ConnectionView(id="agy-03", provider_id="gemini", auth_ref="cli:x", meta={"cli_path": str(agy_bin), "home": "rel/dir"})
    refused = await agy.execute(request(relative))
    assert not refused.ok and refused.error_kind is ErrorKind.BAD_REQUEST and "absolute" in str(refused.error)
    assert harness.get_calls() == []  # no process ran with a relative home


def test_farm_ai_login_starts_each_cli_in_its_own_home(
    scratch_db: Callable[[], str], monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from psycopg.types.json import Jsonb

    url = scratch_db()
    command.upgrade(alembic_config(url), "head")
    monkeypatch.setenv("FARM_DB_URL", url)
    monkeypatch.setenv("FARM_DATA_DIR", str(tmp_path / "data"))
    agy_home, codex_home = tmp_path / "homes" / "agy-02", tmp_path / "homes" / "codex-02"
    with psycopg.connect(url, autocommit=True) as conn:
        conn.execute(
            "insert into public.providers (id, name, kind, executor) values "
            "('gemini', 'Gemini', 'ai', 'cli_agent'), ('codex', 'Codex', 'ai', 'cli_agent')"
        )
        for conn_id, provider, meta in (
            ("agy-02", "gemini", {"cli": "agy", "home": str(agy_home)}),
            ("codex-02", "codex", {"cli": "codex", "home": str(codex_home)}),
            ("agy-01", "gemini", {"cli": "agy"}),
        ):
            conn.execute(
                "insert into public.connections (id, provider_id, auth_ref, meta) values (%s, %s, %s, %s)",
                (conn_id, provider, f"cli:{conn_id}", Jsonb(meta)),
            )
    launched: list[tuple[list[str], dict[str, str]]] = []
    monkeypatch.setattr("subprocess.run", lambda argv, **kw: launched.append((argv, kw["env"])))

    run = CliRunner()
    outputs = [run.invoke(app, ["ai", "login", conn_id]) for conn_id in ("agy-02", "codex-02", "agy-01")]
    assert [o.exit_code for o in outputs] == [0, 0, 0], [o.output for o in outputs]
    (agy_argv, agy_env), (codex_argv, codex_env), (global_argv, global_env) = launched
    assert agy_argv == ["agy"] and agy_env["USERPROFILE"] == agy_env["HOME"] == str(agy_home)
    assert agy_home.is_dir() and f"Home directory: {agy_home}" in outputs[0].output
    assert codex_argv == ["codex", "login"] and codex_env["CODEX_HOME"] == str(codex_home) and codex_home.is_dir()
    assert global_argv == ["agy"] and global_env.get("USERPROFILE") == os.environ.get("USERPROFILE")  # not redirected
    assert "global login" in outputs[2].output


# --- a second transport plugs in behind the same job API --------------------------------------------------------------------------


class ScriptedTransport:
    """A transport that answers from a script instead of running a CLI (``farm.ai.jobs.Transport``).

    ``script`` entries are used in order, the last one repeats: an ``Attempt``, an exception (raised) or a
    callable ``(job, task) -> Attempt``.
    """

    def __init__(self, *script: Attempt | BaseException | Callable[[JobRecord, str], Attempt]) -> None:
        self.script = list(script)
        self.calls: list[tuple[JobRecord, str]] = []

    async def run(self, job: JobRecord, task: str) -> Attempt:
        self.calls.append((job, task))
        step = self.script.pop(0) if len(self.script) > 1 else self.script[0]
        if isinstance(step, BaseException):
            raise step
        return step(job, task) if callable(step) else step


def answered(account: str, text: str, *, session: str | None = None) -> Attempt:
    data = {"text": text, "session_id": session, "usage": {"input_tokens": 3, "output_tokens": 2}, "cost_usd": 0.0}
    return Attempt(account, START, START, data=data)


def busy(account: str) -> Attempt:
    return Attempt(account, START, START, busy=True)


def scripted_manager(aip: Aip, transport: ScriptedTransport) -> JobManager:
    manager = JobManager(aip.ctx, transport=transport, tick_s=0.05, busy_backoff_s=0.05)
    aip.ctx.executors[f"ai-jobs:{uuid4()}"] = ManagerHook(manager)  # stopped with the context
    return manager


async def test_a_second_transport_runs_turns_and_conversations_behind_the_same_job_api(aip: Aip) -> None:
    def echo(job: JobRecord, task: str) -> Attempt:
        return answered(job.account, f"echo: {task}", session=job.resume_session_id or "x-1")

    transport = ScriptedTransport(echo)
    manager = scripted_manager(aip, transport)
    first = await manager.start(spec(task="hello", ai="claude", account="claude-03", model="sonnet"), caller="test")
    (one,), _ = await manager.wait([first.job_id], mode="all", timeout_s=WAIT_S)
    assert (one.ok, one.text, one.native_session_id, one.tokens) == (True, "echo: hello", "x-1", 5)

    reply = await manager.reply(first.conversation_id, "and again", timeout_s=None, caller="test")
    (two,), _ = await manager.wait([reply.job_id], mode="all", timeout_s=WAIT_S)
    assert (two.text, two.turn, two.account) == ("echo: and again", 2, "claude-03")
    (job1, _), (job2, task2) = transport.calls
    assert job1.resume_session_id is None
    assert (job2.resume_session_id, job2.model, job2.account, task2) == ("x-1", "sonnet", "claude-03", "and again")
    thread = await manager.conversations.get(first.conversation_id)
    assert thread is not None and (thread.turns, thread.tokens, thread.native_session_id) == (2, 10, "x-1")
    assert aip.harness.events() == []  # no CLI was involved: the queue, limits and results are the job layer's


async def test_a_busy_account_sends_the_job_back_to_the_queue_until_its_gate_is_free(aip: Aip) -> None:
    transport = ScriptedTransport(busy("claude-02"), busy("claude-02"), answered("claude-02", "finally"))
    manager = scripted_manager(aip, transport)
    started = await manager.start(spec(ai="claude", account="claude-02"), caller="test")
    (done,), _ = await manager.wait([started.job_id], mode="all", timeout_s=WAIT_S)
    assert (done.ok, done.text, len(transport.calls)) == (True, "finally", 3)
    assert len(done.attempts) == 1  # a round in which nothing ran is not an attempt


async def test_an_account_that_stays_busy_longer_than_the_jobs_timeout_fails_the_job(aip: Aip) -> None:
    manager = scripted_manager(aip, ScriptedTransport(busy("claude-02")))
    started = await manager.start(spec(ai="claude", account="claude-02", timeout_s=1), caller="test")
    (done,), _ = await manager.wait([started.job_id], mode="all", timeout_s=WAIT_S)
    assert done.ok is False and done.error is not None
    assert (done.error.kind, done.error.cause, done.error.account) == ("account_unavailable", "busy", "claude-02")
    assert "stayed busy" in done.error.message


async def test_a_bug_in_a_transport_fails_that_job_as_crash_and_the_manager_goes_on(aip: Aip) -> None:
    transport = ScriptedTransport(RuntimeError("boom"), answered("claude-02", "fine after the bug"))
    manager = scripted_manager(aip, transport)
    a = await manager.start(spec(ai="claude", account="claude-02"), caller="test")
    (bad,), _ = await manager.wait([a.job_id], mode="all", timeout_s=WAIT_S)
    assert bad.error is not None and bad.error.kind == "crash" and "RuntimeError: boom" in bad.error.message
    b = await manager.start(spec(ai="claude", account="claude-02"), caller="test")
    (good,), _ = await manager.wait([b.job_id], mode="all", timeout_s=WAIT_S)
    assert (good.ok, good.text) == (True, "fine after the bug")
    assert await fetch(aip.pool, "select count(*) from public.ai_jobs where state in ('queued', 'running')") == [(0,)]


async def test_a_job_fails_as_bad_request_when_the_farm_has_no_ask_ai_capability(aip: Aip) -> None:
    async with aip.pool.connection() as conn:
        await conn.execute("delete from public.capabilities where name = 'ask_ai'")
    async with aip.client() as client:
        started = await start(client, ai="claude")
        (result,) = await finish(client, [started["job_id"]])
    assert result["ok"] is False and result["error"]["kind"] == "bad_request"
    assert "farm registry sync" in result["error"]["message"]


# --- the new tables follow the schema contract (CONTEXT section 3) -----------------------------------------------------------------

DEFAULT_WORKSPACE = "00000000-0000-0000-0000-000000000001"
AI_TABLE_COLUMNS = {
    "ai_conversations": {
        "id", "ai", "account", "native_session_id", "turns", "tokens", "cost", "last_job_id", "created_at",
        "updated_at",
    },
    "ai_jobs": {
        "id", "conversation_id", "turn", "ai", "account", "model", "mode", "cwd", "state", "timeout_s",
        "retry_other_account", "caller", "owner_id", "heartbeat_at", "cancel_requested_at", "attempts", "run_id",
        "result_path", "result_chars", "usage", "tokens", "cost_usd", "cost_estimated", "duration_s", "error_kind",
        "error_message", "retry_at", "created_at", "started_at", "finished_at", "updated_at",
    },
}


def test_the_ai_tables_have_their_columns_a_workspace_id_default_and_row_level_security(db_url: str) -> None:
    with psycopg.connect(db_url, autocommit=True) as conn:
        columns = conn.execute(
            "select table_name, column_name, data_type, is_nullable, column_default "
            "from information_schema.columns where table_schema = 'public' and table_name = any(%s)",
            (list(AI_TABLE_COLUMNS),),
        ).fetchall()
        rls = dict(
            conn.execute(
                "select tablename, rowsecurity from pg_tables where schemaname = 'public' and tablename = any(%s)",
                (list(AI_TABLE_COLUMNS),),
            ).fetchall()
        )
    for table, expected in AI_TABLE_COLUMNS.items():
        present = {c[1] for c in columns if c[0] == table}
        assert expected <= present, f"{table}: missing {expected - present}"
        (workspace,) = [c for c in columns if c[0] == table and c[1] == "workspace_id"]
        assert workspace[2] == "uuid" and workspace[3] == "NO" and DEFAULT_WORKSPACE in workspace[4]
    assert rls == {"ai_conversations": True, "ai_jobs": True}


async def test_the_ai_tables_reject_rows_that_break_their_rules(pool: DbPool) -> None:
    insert = (
        "insert into public.ai_jobs (conversation_id, turn, ai, account, {columns}) "
        "values (%s, 1, 'claude', 'claude-02', {values})"
    )

    async def new_conversation(conn: Any) -> Any:
        cur = await conn.execute(
            "insert into public.ai_conversations (ai, account) values ('claude', 'claude-02') returning id"
        )
        return (await cur.fetchone())[0]

    bad = [
        ("state", "'weird'"),
        ("mode", "'both'"),
        ("timeout_s", "0"),
        ("timeout_s", "7201"),
        ("state, error_kind", "'failed', null"),  # a failed job must say why
        ("state, error_kind", "'cancelled', null"),
        ("error_kind", "'nonsense'"),
        ("tokens", "-1"),
    ]
    async with pool.connection() as conn:
        for columns, values in bad:
            conv = await new_conversation(conn)
            with pytest.raises(psycopg.errors.CheckViolation):
                await conn.execute(insert.format(columns=columns, values=values), (conv,))
        with pytest.raises(psycopg.errors.CheckViolation):
            await conn.execute("insert into public.ai_conversations (ai, account, turns) values ('claude', 'c', -1)")

        # one queued or running job per conversation; finished jobs do not count
        conv = await new_conversation(conn)
        await conn.execute(insert.format(columns="state", values="'succeeded'"), (conv,))
        await conn.execute(insert.format(columns="state, error_kind", values="'failed', 'crash'"), (conv,))
        await conn.execute(insert.format(columns="state", values="'queued'"), (conv,))
        for state in ("queued", "running"):
            with pytest.raises(psycopg.errors.UniqueViolation):
                await conn.execute(insert.format(columns="state", values=f"'{state}'"), (conv,))
        counted = await conn.execute("select count(*) from public.ai_jobs where conversation_id = %s", (conv,))
        assert await counted.fetchone() == (3,)

        await conn.execute("delete from public.ai_conversations where id = %s", (conv,))  # its jobs go with it
        remaining = await conn.execute("select count(*) from public.ai_jobs where conversation_id = %s", (conv,))
        assert await remaining.fetchone() == (0,)


async def test_updated_at_moves_when_a_job_or_a_conversation_changes(pool: DbPool) -> None:
    probe = (
        "select (select updated_at from public.ai_jobs where id = %s), "
        "(select updated_at from public.ai_conversations where id = %s)"
    )
    async with pool.connection() as conn:
        cur = await conn.execute("insert into public.ai_conversations (ai, account) values ('claude', 'c') returning id")
        conv = (await cur.fetchone())[0]
        cur = await conn.execute(
            "insert into public.ai_jobs (conversation_id, turn, ai, account) values (%s, 1, 'claude', 'c') returning id",
            (conv,),
        )
        job = (await cur.fetchone())[0]
        before = await (await conn.execute(probe, (job, conv))).fetchone()
        await conn.execute("update public.ai_jobs set state = 'running' where id = %s", (job,))
        await conn.execute("update public.ai_conversations set turns = 1 where id = %s", (conv,))
        after = await (await conn.execute(probe, (job, conv))).fetchone()
    assert before is not None and after is not None and after[0] > before[0] and after[1] > before[1]


def test_under_supabase_the_owner_may_read_the_ai_tables_and_nobody_else_may_touch_them(
    scratch_db: Callable[[], str],
) -> None:
    from psycopg import errors

    from tests.test_db_schema import FAKE_AUTH, _as

    url = scratch_db()
    with psycopg.connect(url, autocommit=True) as conn:
        conn.execute(FAKE_AUTH)
    command.upgrade(alembic_config(url), "head")
    with psycopg.connect(url, autocommit=True) as conn:
        conn.execute("update public.farm_settings set owner_email = 'Owner@Example.com' where id = 1")
        conv = conn.execute(
            "insert into public.ai_conversations (ai, account) values ('claude', 'claude-02') returning id"
        ).fetchone()
        assert conv is not None
        conn.execute(
            "insert into public.ai_jobs (conversation_id, turn, ai, account) values (%s, 1, 'claude', 'claude-02')",
            (conv[0],),
        )
        _as(conn, "authenticated", "owner@example.com")
        assert conn.execute("select count(*) from public.ai_jobs").fetchone() == (1,)
        assert conn.execute("select count(*) from public.ai_conversations").fetchone() == (1,)
        for statement in (
            "update public.ai_jobs set state = 'cancelled'",
            "delete from public.ai_jobs",
            "insert into public.ai_conversations (ai, account) values ('claude', 'x')",
        ):
            with pytest.raises(errors.InsufficientPrivilege):
                conn.execute(statement)  # type: ignore[call-overload]
        conn.execute("reset role")
        _as(conn, "authenticated", "intruder@example.com")
        assert conn.execute("select count(*) from public.ai_jobs").fetchone() == (0,)
        assert conn.execute("select count(*) from public.ai_conversations").fetchone() == (0,)
        conn.execute("reset role")
        _as(conn, "anon", None)
        with pytest.raises(errors.InsufficientPrivilege):
            conn.execute("select count(*) from public.ai_jobs")
