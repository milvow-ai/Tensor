# ruff: noqa: F811  (the shared fixtures are imported from test_ai_jobs and used by name)
"""Tests for per-task reasoning effort for AI workers (brief AIP2b).

Validates end-to-end effort flow from schemas, database migration, job store & manager,
gateway tools (ai_start, ai_start_many, ai_reply, ask_ai), and CLI agent drivers
(Claude, Codex, agy, Hermes).
"""

from __future__ import annotations

import json
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any
from uuid import uuid4

import psycopg
import pytest

from farm.executors.base import ConnectionView, ErrorKind, ExecRequest
from farm.executors.cli_agent.agy import AgyCliExecutor
from farm.executors.cli_agent.claude import ClaudeCliExecutor
from farm.executors.cli_agent.codex import CodexCliExecutor
from farm.executors.cli_agent.hermes import HermesCliExecutor
from tests.farm_helpers import fetch
from tests.test_ai_jobs import (  # noqa: F401
    Aip,
    agy_ok,
    aip,
    aip_factory,
    call,
    claude_ok,
    finish,
    start,
)
from tests.test_cli_agent_fakes import FakeCliHarness


def flag(argv: list[str], name: str) -> str:
    """Extract argument value following a flag."""
    return argv[argv.index(name) + 1]


# --- Acceptance Test: ai_start_many with Claude (opus, high) and (sonnet, low) ----------------


async def test_ai_start_many_with_different_models_and_efforts(aip: Aip) -> None:
    """Acceptance: start two Claude jobs with (opus, high) and (sonnet, low).

    Verifies fake CLI receives `--model opus --effort high` and `--model sonnet --effort low`,
    both are persisted in ai_jobs.effort and reported back in AiStarted, ai_status, and ai_result.
    """
    aip.harness.set_response(stdout=claude_ok("task completed"))
    jobs = [
        {"task": "think deeply", "ai": "claude", "model": "opus", "effort": "high"},
        {"task": "fast response", "ai": "claude", "model": "sonnet", "effort": "low"},
    ]
    async with aip.client() as client:
        started = await call(client, "ai_start_many", jobs=jobs, distinct_accounts=True)
        assert started["ok"] is True
        job_list = started["jobs"]
        assert len(job_list) == 2
        assert job_list[0]["model"] == "opus" and job_list[0]["effort"] == "high"
        assert job_list[1]["model"] == "sonnet" and job_list[1]["effort"] == "low"

        job_ids = [j["job_id"] for j in job_list]

        # ai_status returns effort
        status = await call(client, "ai_status", job_ids=job_ids)
        status_by_id = {j["job_id"]: j for j in status["jobs"]}
        assert status_by_id[job_list[0]["job_id"]]["effort"] == "high"
        assert status_by_id[job_list[1]["job_id"]]["effort"] == "low"

        results = await finish(client, job_ids)

    # ai_result returns effort and effort_applied=True
    assert results[0]["ok"] is True and results[0]["effort"] == "high" and results[0]["effort_applied"] is True
    assert results[1]["ok"] is True and results[1]["effort"] == "low" and results[1]["effort_applied"] is True

    # Database persistence: check ai_jobs.effort column
    db_rows = await fetch(
        aip.pool,
        "select id, model, effort from public.ai_jobs where id = any(%s)",
        job_ids,
    )
    assert len(db_rows) == 2
    by_id = {str(row[0]): (row[1], row[2]) for row in db_rows}
    assert by_id[job_list[0]["job_id"]] == ("opus", "high")
    assert by_id[job_list[1]["job_id"]] == ("sonnet", "low")

    # Fake CLI invocation: verify argv
    events = aip.harness.events()
    assert len(events) == 2
    e_opus = next(e for e in events if flag(e["argv"], "--model") == "opus")
    e_sonnet = next(e for e in events if flag(e["argv"], "--model") == "sonnet")
    assert flag(e_opus["argv"], "--effort") == "high"
    assert flag(e_sonnet["argv"], "--effort") == "low"


# --- Acceptance Test: Invalid effort rejected before any process starts ----------------------


async def test_invalid_effort_rejected_at_gateway_before_process_starts(aip: Aip) -> None:
    """Acceptance: invalid effort rejected before any process starts."""
    async with aip.client() as client:
        # ai_start rejects invalid effort
        res1 = await client.call_tool("ai_start", {"task": "t", "ai": "claude", "effort": "extreme"}, raise_on_error=False)
        assert res1.is_error or (res1.structured_content and not res1.structured_content.get("ok"))
        assert len(aip.harness.events()) == 0

        # ai_start rejects flag injection attempt
        res2 = await client.call_tool("ai_start", {"task": "t", "ai": "claude", "effort": "--danger"}, raise_on_error=False)
        assert res2.is_error or (res2.structured_content and not res2.structured_content.get("ok"))
        assert len(aip.harness.events()) == 0

        # ai_start_many rejects invalid effort
        res3 = await client.call_tool(
            "ai_start_many",
            {"jobs": [{"task": "t", "ai": "claude", "effort": "bogus"}]},
            raise_on_error=False,
        )
        assert res3.is_error or (res3.structured_content and not res3.structured_content.get("ok"))
        assert len(aip.harness.events()) == 0


async def test_invalid_effort_rejected_by_all_drivers_without_spawning(tmp_path: Path) -> None:
    """All CLI drivers validate effort against the allow-list (SEC1 argv safety)."""
    harness = FakeCliHarness(tmp_path / "bin")
    claude_bin = harness.register_cli("claude")
    codex_bin = harness.register_cli("codex")
    agy_bin = harness.register_cli("agy")
    hermes_bin = harness.register_cli("hermes")

    drivers: list[tuple[Any, Path, str]] = [
        (ClaudeCliExecutor(), claude_bin, "claude"),
        (CodexCliExecutor(), codex_bin, "codex"),
        (AgyCliExecutor(), agy_bin, "agy"),
        (HermesCliExecutor(), hermes_bin, "hermes"),
    ]

    for executor, bin_path, cli_name in drivers:
        harness.clear_calls()
        conn = ConnectionView(
            auth_ref=f"cli:{cli_name}",
            id=f"{cli_name}-01",
            provider_id=cli_name,
            meta={"cli_path": str(bin_path), "config_dir": str(tmp_path / cli_name), "home": str(tmp_path / cli_name)},
        )
        req = ExecRequest(
            request_id=uuid4(),
            capability="ask_ai",
            params={"task": "do something", "effort": "invalid_effort"},
            connection=conn,
        )
        res = await executor.execute(req)
        assert not res.ok
        assert res.error_kind is ErrorKind.BAD_REQUEST
        assert "invalid_effort" in str(res.error) or "effort" in str(res.error)
        assert len(harness.get_calls()) == 0


# --- Acceptance Test: Codex mapping (xhigh/max -> high, others direct) ------------------------


@pytest.mark.parametrize(
    ("input_effort", "expected_mapped"),
    [
        ("low", "low"),
        ("medium", "medium"),
        ("high", "high"),
        ("xhigh", "high"),
        ("max", "high"),
    ],
)
async def test_codex_effort_mapping(tmp_path: Path, input_effort: str, expected_mapped: str) -> None:
    """Codex maps xhigh and max to high; low, medium, high pass directly."""
    harness = FakeCliHarness(tmp_path / f"bin_{input_effort}")
    codex_bin = harness.register_cli("codex")
    harness.set_response(stdout=json.dumps({"type": "item.completed", "item": {"text": "ok"}}))

    conn = ConnectionView(
        auth_ref="cli:codex",
        id="codex-01",
        provider_id="codex",
        meta={"cli_path": str(codex_bin), "home": str(tmp_path / f"home_{input_effort}"), "model": "o3"},
    )
    req = ExecRequest(
        request_id=uuid4(),
        capability="ask_ai",
        params={"task": "solve algorithm", "effort": input_effort},
        connection=conn,
    )
    res = await CodexCliExecutor().execute(req)
    assert res.ok
    assert res.data is not None
    assert res.data["effort"] == input_effort
    assert res.data["effort_applied"] is True

    calls = harness.get_calls()
    assert len(calls) == 1
    argv = calls[0]["argv"]
    assert "-c" in argv
    c_index = argv.index("-c")
    assert argv[c_index + 1] == f"model_reasoning_effort={expected_mapped}"


async def test_codex_job_end_to_end_effort_mapping(aip_factory: Callable[..., Awaitable[Aip]]) -> None:
    """End-to-end Codex job through FastMCP server maps xhigh to high."""
    aip = await aip_factory(codex=1, claude=0, gemini=0)
    aip.harness.set_response(stdout=json.dumps({"type": "item.completed", "item": {"text": "codex done"}}))
    async with aip.client() as client:
        started = await start(client, ai="codex", effort="xhigh")
        assert started["effort"] == "xhigh"
        (result,) = await finish(client, [started["job_id"]])
    assert result["ok"] is True
    assert result["effort"] == "xhigh"
    assert result["effort_applied"] is True

    event = aip.harness.events()[-1]
    assert "-c" in event["argv"]
    c_idx = event["argv"].index("-c")
    assert event["argv"][c_idx + 1] == "model_reasoning_effort=high"


# --- Acceptance Test: agy and Hermes ignore effort (effort_applied: false) --------------------


async def test_agy_and_hermes_ignore_effort(tmp_path: Path) -> None:
    """agy and Hermes ignore effort; results show effort_applied: False."""
    harness = FakeCliHarness(tmp_path / "bin")
    agy_bin = harness.register_cli("agy")
    hermes_bin = harness.register_cli("hermes")

    # Agy ignores effort
    harness.set_response(stdout=agy_ok("gemini reply"))
    agy_conn = ConnectionView(
        auth_ref="cli:agy",
        id="agy-01",
        provider_id="gemini",
        meta={"cli_path": str(agy_bin), "home": str(tmp_path / "agy")},
    )
    req_agy = ExecRequest(
        request_id=uuid4(),
        capability="ask_ai",
        params={"task": "gemini task", "effort": "high"},
        connection=agy_conn,
    )
    res_agy = await AgyCliExecutor().execute(req_agy)
    assert res_agy.ok
    assert res_agy.data is not None
    assert res_agy.data["effort"] == "high"
    assert res_agy.data["effort_applied"] is False
    calls_agy = harness.get_calls()
    assert "--effort" not in calls_agy[0]["argv"]
    assert "model_reasoning_effort" not in " ".join(calls_agy[0]["argv"])

    # Hermes ignores effort
    harness.clear_calls()
    harness.set_response(stdout="Hermes reply")
    hermes_conn = ConnectionView(
        auth_ref="cli:hermes",
        id="hermes-01",
        provider_id="hermes",
        meta={"cli_path": str(hermes_bin), "profile": "farm-agent"},
    )
    req_hermes = ExecRequest(
        request_id=uuid4(),
        capability="ask_ai",
        params={"task": "hermes task", "effort": "low"},
        connection=hermes_conn,
    )
    res_hermes = await HermesCliExecutor().execute(req_hermes)
    assert res_hermes.ok
    assert res_hermes.data is not None
    assert res_hermes.data["effort"] == "low"
    assert res_hermes.data["effort_applied"] is False
    calls_hermes = harness.get_calls()
    assert "--effort" not in calls_hermes[0]["argv"]
    assert "model_reasoning_effort" not in " ".join(calls_hermes[0]["argv"])


async def test_agy_job_end_to_end_ignores_effort(aip: Aip) -> None:
    """End-to-end Gemini (agy) job through FastMCP server ignores effort."""
    aip.harness.set_response(stdout=agy_ok("gemini finished"))
    async with aip.client() as client:
        started = await start(client, ai="gemini", effort="high")
        assert started["effort"] == "high"
        (result,) = await finish(client, [started["job_id"]])
    assert result["ok"] is True
    assert result["effort"] == "high"
    assert result["effort_applied"] is False

    event = aip.harness.events()[-1]
    assert "--effort" not in event["argv"]
    assert "model_reasoning_effort" not in " ".join(event["argv"])


# --- Acceptance Test: ai_reply carries effort over and allows override -------------------------


async def test_ai_reply_carries_effort_over_and_allows_override(aip: Aip) -> None:
    """Acceptance: ai_reply carries effort over from previous turn, and can override it."""
    aip.harness.set_response(stdout=claude_ok("turn 1 done", session="sess-effort-1"))
    async with aip.client() as client:
        # Turn 1: started with effort="high"
        t1 = await start(client, ai="claude", effort="high")
        assert t1["effort"] == "high"
        (r1,) = await finish(client, [t1["job_id"]])
        assert r1["effort"] == "high" and r1["effort_applied"] is True

        conv_id = t1["conversation_id"]

        # Turn 2: reply without specifying effort -> carries over "high"
        aip.harness.set_response(stdout=claude_ok("turn 2 done", session="sess-effort-1"))
        t2 = await call(client, "ai_reply", conversation_id=conv_id, message="follow-up turn")
        assert t2["ok"] is True
        assert t2["effort"] == "high"
        (r2,) = await finish(client, [t2["job_id"]])
        assert r2["effort"] == "high" and r2["effort_applied"] is True

        # Verify DB persisted "high" for turn 2
        row2 = await fetch(aip.pool, "select effort from public.ai_jobs where id = %s", t2["job_id"])
        assert row2[0][0] == "high"

        # Turn 3: reply overriding effort with "low"
        aip.harness.set_response(stdout=claude_ok("turn 3 done", session="sess-effort-1"))
        t3 = await call(client, "ai_reply", conversation_id=conv_id, message="override turn", effort="low")
        assert t3["ok"] is True
        assert t3["effort"] == "low"
        (r3,) = await finish(client, [t3["job_id"]])
        assert r3["effort"] == "low" and r3["effort_applied"] is True

        # Verify DB persisted "low" for turn 3
        row3 = await fetch(aip.pool, "select effort from public.ai_jobs where id = %s", t3["job_id"])
        assert row3[0][0] == "low"

    events = aip.harness.events()
    assert len(events) == 3
    assert flag(events[0]["argv"], "--effort") == "high"
    assert flag(events[1]["argv"], "--effort") == "high"  # inherited!
    assert flag(events[2]["argv"], "--effort") == "low"   # overridden!


# --- list_ais shows supported efforts per account ----------------------------------------------


async def test_list_ais_shows_supported_efforts(aip_factory: Callable[..., Awaitable[Aip]]) -> None:
    """list_ais reports supported efforts per account."""
    aip = await aip_factory(claude=2, gemini=1, codex=1, hermes=1)
    async with aip.client() as client:
        report = await call(client, "list_ais")

    accounts = {acc["id"]: acc for acc in report["accounts"]}
    assert accounts["claude-02"]["efforts"] == ["low", "medium", "high", "xhigh", "max"]
    assert accounts["codex-01"]["efforts"] == ["low", "medium", "high"]
    assert accounts["agy-01"]["efforts"] == []
    assert accounts["hermes-01"]["efforts"] == []


# --- ask_ai blocking tool passes effort -------------------------------------------------------


async def test_ask_ai_blocking_tool_passes_effort(aip: Aip) -> None:
    """The blocking ask_ai tool accepts effort and passes it to the driver."""
    aip.harness.set_response(stdout=claude_ok("blocking answer"))
    async with aip.client() as client:
        reply = await call(client, "ask_ai", task="answer immediately", ai="claude", effort="medium")
    assert reply["ok"] is True
    assert reply["result"]["effort"] == "medium"
    assert reply["result"]["effort_applied"] is True
    event = aip.harness.events()[-1]
    assert flag(event["argv"], "--effort") == "medium"


# --- DB Check Constraint on ai_jobs.effort ----------------------------------------------------


async def test_db_check_constraint_on_ai_jobs_effort(aip: Aip) -> None:
    """Migration 0011 check constraint permits valid efforts and null, rejects others."""
    # Valid values succeed (each job with its own conversation)
    for val in (None, "low", "medium", "high", "xhigh", "max"):
        conv_row = await fetch(
            aip.pool,
            "insert into public.ai_conversations (ai, account) values ('claude', 'claude-02') returning id",
        )
        conv_id = conv_row[0][0]
        await fetch(
            aip.pool,
            "insert into public.ai_jobs (id, conversation_id, turn, ai, account, effort, state) "
            "values (%s, %s, 1, 'claude', 'claude-02', %s, 'queued') returning id",
            str(uuid4()),
            conv_id,
            val,
        )

    # Invalid value raises CheckViolation
    bad_conv = await fetch(
        aip.pool,
        "insert into public.ai_conversations (ai, account) values ('claude', 'claude-02') returning id",
    )
    with pytest.raises(psycopg.errors.CheckViolation):
        await fetch(
            aip.pool,
            "insert into public.ai_jobs (id, conversation_id, turn, ai, account, effort, state) "
            "values (%s, %s, 1, 'claude', 'claude-02', 'superhigh', 'queued') returning id",
            str(uuid4()),
            bad_conv[0][0],
        )
