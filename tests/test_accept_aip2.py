# ruff: noqa: F811  (the shared fixtures are imported from test_ai_jobs and used by name)
"""Acceptance tests for AIP2: the main AI runs other AIs as workers through the Farm.

"Give this task to 3 Claude accounts and 2 Gemini, check on them, read each exact result, find what is wrong,
send a follow-up to the same worker, repeat." Every test drives the real MCP server in memory with a real
client, the real router, ledger and Postgres, and the M3e fake CLIs (no real CLI, no network, no spending).
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import os
import sys
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from uuid import uuid4

import mcp_types
import psutil
import psycopg
import pytest
import yaml
from alembic import command
from fastmcp import Client
from fastmcp.client.transports import StdioTransport
from typer.testing import CliRunner

from farm.ai.jobs import JobManager
from farm.context import FarmContext
from farm.control.cli import alembic_config, app
from farm.db.pool import open_pool, run
from farm.executors.api import ApiExecutor
from farm.executors.cli_agent import CliAgentExecutor
from farm.gateway.ai_tools import AI_TOOLS
from farm.gateway.server import build_server
from tests.farm_helpers import fetch
from tests.test_ai_jobs import (  # noqa: F401  (the fixtures are used by name)
    RESET_AT,
    AiHarness,
    Aip,
    agy_ok,
    ai_registry,
    aip,
    aip_factory,
    all_gone,
    call,
    claude_limit,
    claude_ok,
    finish,
    pids_of,
    start,
)

SLOW = 60  # seconds a fake CLI sleeps when the test means to interrupt it


def flag(argv: list[str], name: str) -> str:
    return argv[argv.index(name) + 1]


# --- fan out: 3 Claude + 2 Gemini, parallel, exact ---------------------------------------------------------------------------

TEXTS = {
    "claude-02": "alpha, straight from claude-02 ✓\r\n",
    "claude-03": "  beta, with leading spaces\n\n",
    "claude-04": "gamma\ttabbed, 日本語, \U0001f9ea",
    "agy-01": "delta from the first gemini account",
    "agy-02": "epsilon from the second gemini account\n",
}


def per_account_outputs(delay_s: float = 0.0) -> dict[str, dict[str, Any]]:
    return {
        account: {"stdout": agy_ok(text) if account.startswith("agy") else claude_ok(text), "delay_s": delay_s}
        for account, text in TEXTS.items()
    }


async def test_three_claude_and_two_gemini_accounts_run_in_parallel_and_return_their_exact_results(aip: Aip) -> None:
    aip.harness.set_response(per_account=per_account_outputs(delay_s=3.5))
    jobs = [{"task": f"job {i}", "ai": ai} for i, ai in enumerate(["claude"] * 3 + ["gemini"] * 2)]
    async with aip.client() as client:
        started = await call(client, "ai_start_many", jobs=jobs, distinct_accounts=True)
        assert started["ok"] is True
        accounts = [j["account"] for j in started["jobs"]]
        assert accounts == ["claude-02", "claude-03", "claude-04", "agy-01", "agy-02"]  # five different accounts
        assert len({j["job_id"] for j in started["jobs"]}) == len({j["conversation_id"] for j in started["jobs"]}) == 5
        results = await finish(client, [j["job_id"] for j in started["jobs"]])

    for account, result in zip(accounts, results, strict=True):  # each result is its own worker's, byte for byte
        assert result["ok"] is True and result["account"] == account and result["state"] == "succeeded"
        assert result["text"] == TEXTS[account]
        assert Path(result["result_path"]).read_bytes() == TEXTS[account].encode("utf-8")
    events = aip.harness.events()
    assert len(events) == 5
    assert max(e["start"] for e in events) < min(e["end"] for e in events)  # all five were running at the same time
    homes = {e["claude_dir"] or e["userprofile"] for e in events}
    assert homes == {str(aip.tmp / name) for name in TEXTS}  # each worker ran with its own account's directory


async def test_more_jobs_than_accounts_queue_per_account_without_distinct_accounts(aip: Aip) -> None:
    aip.harness.set_response(per_account=per_account_outputs(delay_s=0.5))
    async with aip.client() as client:
        started = await call(
            client, "ai_start_many", jobs=[{"task": f"job {i}", "ai": "gemini"} for i in range(5)], distinct_accounts=False
        )
        accounts = [j["account"] for j in started["jobs"]]
        assert accounts == ["agy-01", "agy-02", "agy-01", "agy-02", "agy-01"]  # spread, then queued behind each other
        assert [j["jobs_ahead"] for j in started["jobs"]] == [0, 0, 1, 1, 2]
        results = await finish(client, [j["job_id"] for j in started["jobs"]])

    assert [r["text"] for r in results] == [TEXTS[a] for a in accounts]
    points = sorted([(e["start"], 1) for e in aip.harness.events()] + [(e["end"], -1) for e in aip.harness.events()])
    running = peak = 0
    for _, step in points:
        running += step
        peak = max(peak, running)
    assert peak == 2  # never more than one job per account


async def test_a_worker_that_fails_is_named_with_the_reason_while_the_others_deliver(aip: Aip) -> None:
    outputs = per_account_outputs()
    outputs["claude-03"] = {"stdout": json.dumps({"result": "Not logged in", "is_error": True}), "exit_code": 1}
    aip.harness.set_response(per_account=outputs)
    async with aip.client() as client:
        started = await call(
            client, "ai_start_many", jobs=[{"task": "same task", "ai": "claude"} for _ in range(3)]
        )
        results = await finish(client, [j["job_id"] for j in started["jobs"]])
    good = [r for r in results if r["ok"]]
    (bad,) = [r for r in results if not r["ok"]]
    assert sorted(r["account"] for r in good) == ["claude-02", "claude-04"]
    assert (bad["error"]["kind"], bad["error"]["ai"], bad["error"]["account"]) == ("auth", "claude", "claude-03")
    assert "farm ai login claude-03" in bad["error"]["message"]
    alert = await fetch(aip.pool, "select message from public.alerts where ref = 'login:claude-03'")
    assert alert and "farm ai login claude-03" in alert[0][0]  # the owner is told, as for ask_ai


# --- iterate: check each result, find what is wrong, send a follow-up to the same worker --------------------------------------


async def test_the_main_ai_finds_a_wrong_answer_and_iterates_with_the_same_worker_until_it_is_right(aip: Aip) -> None:
    schema = {"type": "object", "properties": {"total": {"type": "integer"}}, "required": ["total"]}
    aip.harness.set_response(
        per_account={
            "claude-02": {"stdout": claude_ok('{"total": 12}', session="s-02")},
            "claude-03": {"stdout": claude_ok('{"total": "twelve"}', session="s-03")},  # the wrong one
            "claude-04": {"stdout": claude_ok('{"total": 12}', session="s-04")},
        }
    )
    async with aip.client() as client:
        started = await call(
            client,
            "ai_start_many",
            jobs=[{"task": "Add 5 and 7. Answer as JSON.", "ai": "claude", "json_schema": schema} for _ in range(3)],
        )
        ids = [j["job_id"] for j in started["jobs"]]
        first_round = await finish(client, ids)
        verdicts = {r["account"]: r["json_valid"] for r in first_round}
        assert verdicts == {"claude-02": True, "claude-03": False, "claude-04": True}
        wrong = next(r for r in first_round if not r["json_valid"])
        assert wrong["text"] == '{"total": "twelve"}' and "total" in " ".join(wrong["json_errors"])  # exact text + why

        # the follow-up goes to that very worker: same account, same native session
        aip.harness.set_response(per_account={"claude-03": {"stdout": claude_ok('{"total": 12}', session="s-03")}})
        conversation = next(j["conversation_id"] for j in started["jobs"] if j["account"] == "claude-03")
        reply = await call(
            client,
            "ai_reply",
            conversation_id=conversation,
            message=f"Your JSON broke the schema ({wrong['json_errors'][0]}). Answer again.",
        )
        assert (reply["account"], reply["turn"]) == ("claude-03", 2)
        (fixed,) = await finish(client, [reply["job_id"]])
        (thread,) = (await call(client, "ai_conversations", account="claude-03"))["conversations"]

    assert fixed["text"] == '{"total": 12}' and fixed["native_session_id"] == "s-03"
    assert aip.harness.events()[-1]["claude_dir"] == str(aip.tmp / "claude-03")
    assert flag(aip.harness.events()[-1]["argv"], "--resume") == "s-03"
    assert (thread["turns"], thread["account"], thread["native_session_id"]) == (2, "claude-03", "s-03")


# --- limits mid-conversation, retry on another account ------------------------------------------------------------------------


async def test_a_conversation_whose_account_got_limited_meanwhile_fails_with_account_unavailable_and_retry_at(
    aip: Aip,
) -> None:
    wrong = {"stdout": claude_ok("WRONG ACCOUNT", session="s-B")}
    aip.harness.set_response(per_account={"claude-02": {"stdout": claude_ok("one", session="s-A")}, "claude-03": wrong})
    async with aip.client() as client:
        talk = await start(client, ai="claude")
        await finish(client, [talk["job_id"]])
        # another piece of work uses up the account's limit while the conversation is idle
        aip.harness.set_response(per_account={"claude-02": {"stdout": claude_limit(), "exit_code": 1}, "claude-03": wrong})
        other = await start(client, ai="claude", account="claude-02", task="unrelated work")
        (hit,) = await finish(client, [other["job_id"]])
        assert hit["error"]["kind"] == "limit" and datetime.fromisoformat(hit["error"]["retry_at"]) == RESET_AT

        reply = await call(client, "ai_reply", conversation_id=talk["conversation_id"], message="next question")
    assert reply["ok"] is False
    error = reply["error"]
    assert (error["kind"], error["account"], error["ai"]) == ("account_unavailable", "claude-02", "claude")
    assert datetime.fromisoformat(error["retry_at"]) == RESET_AT  # when to try again
    assert all(e["claude_dir"] == str(aip.tmp / "claude-02") for e in aip.harness.events())  # never tried elsewhere


async def test_retry_other_account_reruns_a_first_turn_on_the_next_account_and_records_both_attempts(aip: Aip) -> None:
    aip.harness.set_response(
        per_account={
            "claude-02": {"stdout": claude_limit(), "exit_code": 1},
            "claude-03": {"stdout": claude_ok("the second account answers", session="s-03")},
        }
    )
    async with aip.client() as client:
        started = await start(client, ai="claude", retry_other_account=True)
        assert started["account"] == "claude-02"  # where it starts
        (result,) = await finish(client, [started["job_id"]])
        status = (await call(client, "ai_status", job_ids=[started["job_id"]]))["jobs"][0]

    assert result["ok"] is True and result["text"] == "the second account answers" and result["account"] == "claude-03"
    first, second = result["attempts"]
    assert (first["n"], first["account"], first["outcome"], first["kind"]) == (1, "claude-02", "failed", "limit")
    assert datetime.fromisoformat(first["retry_at"]) == RESET_AT
    assert (second["n"], second["account"], second["outcome"], second["kind"]) == (2, "claude-03", "succeeded", None)
    assert status["attempt_count"] == 2 and status["account"] == "claude-03"
    row = await aip.job(started["job_id"])
    assert row["state"] == "succeeded" and [a["account"] for a in row["attempts"]] == ["claude-02", "claude-03"]
    runs = await fetch(aip.pool, "select id, connection_id, status from public.runs where capability = 'ask_ai'")
    assert {(str(r[0]), r[1], r[2]) for r in runs} == {
        (first["run_id"], "claude-02", "failed"),
        (second["run_id"], "claude-03", "succeeded"),
    }  # each attempt is its own run, with its own trajectory


@pytest.mark.parametrize(
    ("config", "kind"),
    [
        ({"stdout": json.dumps({"result": "Not logged in", "is_error": True}), "exit_code": 1}, "auth"),
        ({"stdout": "kaboom", "stderr": "segfault", "exit_code": 3}, "crash"),
    ],
    ids=["auth", "crash"],
)
async def test_auth_and_crash_failures_are_retried_on_the_next_account_too(
    aip: Aip, config: dict[str, Any], kind: str
) -> None:
    aip.harness.set_response(
        per_account={"claude-02": config, "claude-03": {"stdout": claude_ok("recovered", session="s-03")}}
    )
    async with aip.client() as client:
        started = await start(client, ai="claude", retry_other_account=True)
        (result,) = await finish(client, [started["job_id"]])
    assert result["ok"] is True and result["account"] == "claude-03"
    assert [(a["account"], a["kind"]) for a in result["attempts"]] == [("claude-02", kind), ("claude-03", None)]


async def test_a_timeout_is_not_retried_on_another_account(aip: Aip) -> None:
    aip.harness.set_response(
        per_account={
            "claude-02": {"stdout": claude_ok("late"), "delay_s": SLOW},
            "claude-03": {"stdout": claude_ok("would have worked")},
        }
    )
    async with aip.client() as client:
        started = await start(client, ai="claude", retry_other_account=True, timeout_s=1)
        (result,) = await finish(client, [started["job_id"]], timeout_s=20)
    assert result["ok"] is False and result["error"]["kind"] == "timeout" and len(result["attempts"]) == 1
    assert len(aip.harness.events()) == 1  # only limit, auth and crash go to the next account


async def test_when_every_account_fails_the_job_fails_with_all_attempts_recorded(aip: Aip) -> None:
    limit = {"stdout": claude_limit(), "exit_code": 1}
    aip.harness.set_response(per_account={"claude-02": limit, "claude-03": limit, "claude-04": limit})
    async with aip.client() as client:
        started = await start(client, ai="claude", retry_other_account=True)
        (result,) = await finish(client, [started["job_id"]])
    assert result["ok"] is False and result["error"]["kind"] == "limit" and result["state"] == "failed"
    assert [a["account"] for a in result["attempts"]] == ["claude-02", "claude-03", "claude-04"]
    assert all(a["outcome"] == "failed" and a["kind"] == "limit" for a in result["attempts"])
    assert result["error"]["account"] == "claude-04"  # the last worker that was tried


# --- cancel, disconnect, restart ----------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("charged_on", "final", "used"),
    [("success", "released", 0), ("attempt", "committed", 1)],
    ids=["charged-on-success-is-released", "charged-on-attempt-is-settled"],
)
async def test_ai_cancel_kills_the_process_tree_and_settles_the_reservation(
    aip_factory: Callable[..., Awaitable[Aip]], charged_on: str, final: str, used: int
) -> None:
    aip = await aip_factory(charged_on=charged_on)
    pid_file = aip.tmp / "cancel.pids"
    aip.harness.set_response(
        per_account={"claude-02": {"stdout": claude_ok("never"), "delay_s": SLOW, "pid_file": str(pid_file)}}
    )
    async with aip.client() as client:
        started = await start(client, ai="claude", account="claude-02")
        parent, child = await pids_of(pid_file)
        assert await aip.reservations("claude-02") == ["reserved"]  # held while the worker runs
        assert await aip.quota("claude-02") == (0, 1)

        cancelled = await call(client, "ai_cancel", job_id=started["job_id"])
        assert cancelled["ok"] is True and cancelled["cancelled"] is True and cancelled["state"] == "cancelled"
        await all_gone(parent, child)  # the CLI and the child it spawned: the whole tree
        (result,) = await finish(client, [started["job_id"]])

    assert result["ok"] is False and result["error"]["kind"] == "cancelled" and result["error"]["account"] == "claude-02"
    assert await aip.reservations("claude-02") == [final]  # settled, never left reserved
    assert await aip.quota("claude-02") == (used, 0)
    assert not aip.harness.runners()


async def test_a_running_job_goes_on_when_the_mcp_client_disconnects(aip: Aip) -> None:
    aip.harness.set_response(stdout=claude_ok("finished while nobody was connected"), delay_s=1.5)
    async with aip.client("claude-code") as first_client:
        started = await start(first_client, ai="claude", task="long job")
    # the first client is gone; the job belongs to the Farm process, not to that connection
    async with aip.client("another-client") as second_client:
        (still,) = (await call(second_client, "ai_status", job_ids=[started["job_id"]]))["jobs"]
        assert still["state"] in ("queued", "running")
        (result,) = await finish(second_client, [started["job_id"]])
    assert result["ok"] is True and result["text"] == "finished while nobody was connected"
    run = await fetch(aip.pool, "select caller from public.runs where id = %s", result["run_id"])
    assert run == [("claude",)]  # the run still says who asked: the first client


async def test_a_farm_restart_fails_the_jobs_that_were_running_and_never_runs_them_again(aip: Aip) -> None:
    pid_file = aip.tmp / "restart.pids"
    aip.harness.set_response(
        per_account={"claude-02": {"stdout": claude_ok("never"), "delay_s": SLOW, "pid_file": str(pid_file)}}
    )
    async with aip.client() as client:
        running = await start(client, ai="claude", account="claude-02")
        queued = await start(client, ai="claude", account="claude-02")
    pids = await pids_of(pid_file)

    await aip.ctx.aclose()  # the Farm stops ...
    ctx = FarmContext(  # ... and a new one starts on the same database
        pool=aip.pool, executors={"api": ApiExecutor(), "cli_agent": CliAgentExecutor()}, clock=aip.clock
    )
    server = await build_server(ctx)
    try:
        async with Client(server) as client:
            status = await call(client, "ai_status", job_ids=[running["job_id"], queued["job_id"]])
            result = await call(client, "ai_result", job_id=running["job_id"])
        assert [(j["state"], j["error"]["kind"]) for j in status["jobs"]] == [("failed", "farm_restart")] * 2
        assert result["ok"] is False and result["error"]["kind"] == "farm_restart" and result["error"]["account"] == "claude-02"
        assert "not re-run" in result["error"]["message"]
        await all_gone(*pids)
        await asyncio.sleep(1.0)
        assert len(aip.harness.events()) == 1  # nothing was started again: no silent re-run
    finally:
        await ctx.aclose()


async def test_a_crashed_farm_leaves_rows_that_the_next_start_fails_as_farm_restart(aip: Aip) -> None:
    pid_file = aip.tmp / "crash.pids"
    aip.harness.set_response(
        per_account={"claude-02": {"stdout": claude_ok("never"), "delay_s": SLOW, "pid_file": str(pid_file)}}
    )
    async with aip.client() as client:
        orphan = await start(client, ai="claude", account="claude-02")
    await pids_of(pid_file)
    aip.clock.advance(timedelta(hours=1).total_seconds())  # the dead process renews no heartbeat for an hour

    ctx = FarmContext(  # the next start: its server sweeps the rows whose owner is gone
        pool=aip.pool, executors={"api": ApiExecutor(), "cli_agent": CliAgentExecutor()}, clock=aip.clock
    )
    server = await build_server(ctx)
    try:
        async with Client(server) as client:
            (job,) = (await call(client, "ai_status", job_ids=[orphan["job_id"]]))["jobs"]
        assert (job["state"], job["error"]["kind"]) == ("failed", "farm_restart")
        assert len(aip.harness.events()) == 1
    finally:
        await ctx.aclose()


# --- waiting -------------------------------------------------------------------------------------------------------------------


async def test_ai_wait_any_returns_the_first_finished_job_and_lists_the_pending_ones(aip: Aip) -> None:
    aip.harness.set_response(
        per_account={
            "claude-02": {"stdout": claude_ok("quick")},
            "claude-03": {"stdout": claude_ok("slow"), "delay_s": 2.5},
        }
    )
    async with aip.client() as client:
        quick = await start(client, ai="claude", account="claude-02")
        slow = await start(client, ai="claude", account="claude-03")
        first = await call(client, "ai_wait", job_ids=[quick["job_id"], slow["job_id"]], mode="any", timeout_s=20)
        assert [r["job_id"] for r in first["finished"]] == [quick["job_id"]] and first["finished"][0]["text"] == "quick"
        assert [p["job_id"] for p in first["pending"]] == [slow["job_id"]] and first["timed_out"] is False
        rest = await call(client, "ai_wait", job_ids=[quick["job_id"], slow["job_id"]], mode="all", timeout_s=20)
    assert sorted(r["text"] for r in rest["finished"]) == ["quick", "slow"] and rest["pending"] == []


async def test_ai_wait_gives_up_after_its_timeout_and_the_caller_loops(aip: Aip) -> None:
    aip.harness.set_response(stdout=claude_ok("eventually"), delay_s=2.0)
    async with aip.client() as client:
        started = await start(client, ai="claude")
        early = await call(client, "ai_wait", job_ids=[started["job_id"]], mode="all", timeout_s=0.3, include_text=False)
        assert early["finished"] == [] and early["timed_out"] is True and early["pending"][0]["state"] in ("queued", "running")
        late = await call(client, "ai_wait", job_ids=[started["job_id"]], mode="all", timeout_s=20, include_text=False)
        unknown = await call(client, "ai_wait", job_ids=[str(uuid4())])
    (done,) = late["finished"]
    assert done["ok"] is True and done["text"] is None and done["result_chars"] == len("eventually")  # left out on request
    assert unknown["ok"] is False and unknown["error"]["kind"] == "not_found"


# --- results that do not fit inline ------------------------------------------------------------------------------------------


async def test_a_large_output_is_a_file_with_its_path_and_a_20000_character_preview(aip: Aip) -> None:
    # About 260,000 characters. The row numbers are zero-padded on purpose: a bare "401" or "429" anywhere in
    # an answer is read as a failure by the CLI executors (the known defect at the end of this file).
    answer = "".join(f"row {i:06d} {'x' * 40}\n" for i in range(5000))
    assert len(answer) > 200_000
    aip.harness.set_response(stdout=claude_ok(answer))
    async with aip.client() as client:
        started = await start(client, ai="claude")
        (result,) = await finish(client, [started["job_id"]])
        again = await call(client, "ai_result", job_id=started["job_id"])
    expected = Path(os.environ["FARM_DATA_DIR"]) / "ai-results" / f"{started['job_id']}.txt"
    for view in (result, again):
        assert Path(view["result_path"]) == expected and view["result_chars"] == len(answer)
        assert view["text_truncated"] is True and view["text"] == answer[:20_000]
    assert expected.read_bytes() == answer.encode("utf-8")  # the file holds every character, unmodified


# --- the old blocking tools next to the new ones ---------------------------------------------------------------------------------


async def test_the_blocking_tools_and_the_job_tools_share_the_same_accounts_and_ledger(aip: Aip) -> None:
    aip.harness.set_response(stdout=claude_ok("same pool"))
    async with aip.client() as client:
        blocking = await call(client, "ask_ai", ai="claude", task="blocking")
        job = await start(client, ai="claude", task="job")
        (result,) = await finish(client, [job["job_id"]])
        listing = await call(client, "list_ais")
    assert blocking["ok"] is True and result["ok"] is True
    assert blocking["source"]["connection_id"] == result["account"] == "claude-02"
    claude = next(a for a in listing["accounts"] if a["id"] == "claude-02")
    assert claude["todays_calls"] == 2  # both went through the router
    assert await aip.quota("claude-02") == (2, 0)  # and through the same quota ledger


# --- the real thing: `farm serve` as a child process on stdio ----------------------------------------------------------------------

STARTUP_TIMEOUT_S = 180


@dataclass
class ServedFarm:
    """A migrated scratch database holding the AI registry, fake CLIs on PATH and the results folder under tmp."""

    url: str
    tmp: Path
    harness: AiHarness

    def transport(self) -> StdioTransport:
        """``farm serve`` exactly as Claude Code starts it: a child process speaking MCP on stdio."""
        return StdioTransport(
            command=sys.executable,
            args=["-c", "from farm.control.cli import app; app()", "serve"],
            env={**os.environ, "FARM_DB_URL": self.url, "FARM_LOG_LEVEL": "INFO"},
            keep_alive=False,  # leaving the client stops the Farm process
        )

    def job_row(self, job_id: str) -> tuple[Any, ...]:
        with psycopg.connect(self.url, autocommit=True) as conn:
            row = conn.execute(
                "select state, error_kind, caller, result_path, owner_id from public.ai_jobs where id = %s", (job_id,)
            ).fetchone()
        assert row is not None
        return tuple(row)


@pytest.fixture
def served_farm(scratch_db: Callable[[], str], monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> ServedFarm:
    harness = AiHarness(tmp_path / "bin")
    for cli in ("claude", "codex", "agy", "hermes"):
        harness.register_cli(cli)
    monkeypatch.setenv("PATH", f"{harness.bin_dir}{os.pathsep}{os.environ.get('PATH', '')}")
    monkeypatch.setenv("FARM_DATA_DIR", str(tmp_path / "data"))
    url = scratch_db()
    command.upgrade(alembic_config(url), "head")
    monkeypatch.setenv("FARM_DB_URL", url)
    registry_file = tmp_path / "registry.yaml"
    registry = ai_registry(tmp_path)
    registry_file.write_text(yaml.safe_dump(registry.model_dump(mode="json"), sort_keys=False), encoding="utf-8")
    synced = CliRunner().invoke(app, ["registry", "sync", str(registry_file)])
    assert synced.exit_code == 0, synced.output
    return ServedFarm(url=url, tmp=tmp_path, harness=harness)


def kill_workers(*pids: int) -> None:
    for pid in pids:
        with contextlib.suppress(psutil.Error):
            psutil.Process(pid).kill()


def test_farm_serve_over_stdio_offers_the_ai_tools_and_runs_a_job_end_to_end(served_farm: ServedFarm) -> None:
    served_farm.harness.set_response(stdout=claude_ok("served over stdio"))

    async def talk() -> tuple[set[str], dict[str, Any], str]:
        client_info = mcp_types.Implementation(name="claude-code", version="1")
        async with Client(served_farm.transport(), client_info=client_info) as client:
            names = {t.name for t in await client.list_tools()}
            started = await start(client, ai="claude", account="claude-02")
            (result,) = await finish(client, [started["job_id"]], timeout_s=120)
            return names, result, started["job_id"]

    names, result, job_id = asyncio.run(asyncio.wait_for(talk(), STARTUP_TIMEOUT_S))
    assert set(AI_TOOLS) <= names
    assert result["ok"] is True and result["text"] == "served over stdio" and result["account"] == "claude-02"
    state, kind, caller, path, _ = served_farm.job_row(job_id)
    assert (state, kind, caller) == ("succeeded", None, "claude")
    assert Path(path).read_bytes() == b"served over stdio"


def test_stopping_farm_serve_while_a_job_runs_fails_it_as_farm_restart_and_kills_the_worker(
    served_farm: ServedFarm,
) -> None:
    pid_file = served_farm.tmp / "stop.pids"
    served_farm.harness.set_response(
        per_account={"claude-02": {"stdout": claude_ok("never"), "delay_s": SLOW, "pid_file": str(pid_file)}}
    )
    found: dict[str, Any] = {}

    async def talk() -> None:
        async with Client(served_farm.transport()) as client:
            found["job"] = (await start(client, ai="claude", account="claude-02"))["job_id"]
            found["pids"] = await pids_of(pid_file)
        # leaving the block closes stdin: the Farm process is asked to stop while the worker runs

    try:
        asyncio.run(asyncio.wait_for(talk(), STARTUP_TIMEOUT_S))
        state, kind, *_ = served_farm.job_row(found["job"])
        assert (state, kind) == ("failed", "farm_restart")  # the stopping Farm said so itself, before it exited
        asyncio.run(all_gone(*found["pids"]))  # and took the worker's whole process tree with it
    finally:
        kill_workers(*found.get("pids", ()))


def test_a_farm_process_that_is_killed_mid_job_leaves_a_row_that_the_next_process_fails_as_farm_restart(
    served_farm: ServedFarm,
) -> None:
    pid_file = served_farm.tmp / "crash.pids"
    served_farm.harness.set_response(
        per_account={"claude-02": {"stdout": claude_ok("never"), "delay_s": SLOW, "pid_file": str(pid_file)}}
    )
    found: dict[str, Any] = {}

    async def talk() -> None:
        try:
            async with Client(served_farm.transport()) as client:
                found["job"] = (await start(client, ai="claude", account="claude-02"))["job_id"]
                found["pids"] = await pids_of(pid_file)
                for proc in psutil.Process().children(recursive=True):
                    with contextlib.suppress(psutil.Error):
                        if "from farm.control.cli import app" in " ".join(proc.cmdline()):
                            proc.kill()  # kill -9 on the Farm process: no shutdown code runs
        except Exception:  # the transport complains that its server is gone
            pass

    async def next_process_starts() -> int:
        """The next `farm serve` (here: its job manager) sweeps the rows whose owner stopped renewing them."""
        pool = await open_pool(served_farm.url)
        try:
            ctx = FarmContext(
                pool=pool,
                executors={"api": ApiExecutor(), "cli_agent": CliAgentExecutor()},
                clock=lambda: datetime.now(UTC) + timedelta(minutes=5),  # past the owner's lease
            )
            return await JobManager(ctx).recover()
        finally:
            await pool.close()

    try:
        asyncio.run(asyncio.wait_for(talk(), STARTUP_TIMEOUT_S))
        assert served_farm.job_row(found["job"])[:2] == ("running", None)  # a hard kill leaves the row as it was
        assert run(next_process_starts()) == 1
        state, kind, *_ = served_farm.job_row(found["job"])
        assert (state, kind) == ("failed", "farm_restart")
    finally:
        kill_workers(*found.get("pids", ()))


# --- a known defect of the M3e CLI executors that jobs inherit -------------------------------------------------------------------


@pytest.mark.xfail(
    reason=(
        "KNOWN DEFECT, M3e executors (claude.py, codex.py, agy.py, hermes.py): they search the whole output, the "
        "worker's answer included, for failure words (401, 429, 'capacity', 'usage limit', 'resets at', ...), so a "
        "successful answer that merely mentions one becomes a failed call and marks the account logged out or "
        "exhausted. The fix belongs in those executors: classify only when the call failed (non-zero exit or "
        "is_error), never on the text of a successful answer."
    ),
    strict=False,
)
async def test_an_answer_that_merely_mentions_a_status_code_or_a_limit_is_still_an_answer(aip: Aip) -> None:
    text = "The API answers 401 when unauthorized and 429 at capacity: that is the usage limit and the rate limit."
    aip.harness.set_response(stdout=claude_ok(text))
    async with aip.client() as client:
        started = await start(client, ai="claude", account="claude-02")
        (result,) = await finish(client, [started["job_id"]])
    assert result["ok"] is True and result["text"] == text
