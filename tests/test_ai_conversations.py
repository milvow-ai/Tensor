# ruff: noqa: F811  (the shared fixtures are imported from test_ai_jobs and used by name)
"""Conversations: follow-ups to the same worker (same account, same native session) and what holds them together.

A conversation is one thread with one worker. It lives on the account that holds the CLI session, so a reply
never moves to another account; when that account is limited or logged out the reply says so, with the time it
is back. Real router, ledger, MCP server and Postgres; fake CLIs from the M3e harness.
"""

from __future__ import annotations

import json
import os
from collections.abc import Awaitable, Callable
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any
from uuid import uuid4

import pytest

from tests.conftest import START
from tests.farm_helpers import fetch
from tests.test_ai_jobs import (  # noqa: F401  (the fixtures are used by name)
    RESET_AT,
    Aip,
    agy_ok,
    aip,
    aip_factory,
    call,
    claude_limit,
    claude_ok,
    finish,
    start,
)

CRASH = {"stdout": "kaboom, not json", "stderr": "segfault", "exit_code": 3}


def flag_value(argv: list[str], flag: str) -> str:
    return argv[argv.index(flag) + 1]


async def conversations(client: Any, **filters: Any) -> list[dict[str, Any]]:
    return list((await call(client, "ai_conversations", **filters))["conversations"])


# --- a reply is the same worker, on the same account, in the same session ----------------------------------------------


async def test_a_reply_lands_on_the_same_account_and_resumes_its_native_session(aip: Aip) -> None:
    wrong = {"stdout": claude_ok("WRONG ACCOUNT", session="sess-B")}
    aip.harness.set_response(
        per_account={
            "claude-02": {"stdout": claude_ok("first answer", session="sess-A", cost=0.01, usage=(10, 5))},
            "claude-03": wrong,
        }
    )
    async with aip.client() as client:
        first = await start(client, task="turn one", ai="claude")
        (r1,) = await finish(client, [first["job_id"]])
        assert (r1["turn"], r1["native_session_id"], r1["account"]) == (1, "sess-A", "claude-02")

        aip.harness.set_response(
            per_account={
                "claude-02": {"stdout": claude_ok("second answer", session="sess-A", cost=0.02, usage=(20, 10))},
                "claude-03": wrong,
            }
        )
        reply = await call(client, "ai_reply", conversation_id=first["conversation_id"], message="turn two")
        assert reply["ok"] is True and reply["account"] == "claude-02" and reply["turn"] == 2
        assert reply["conversation_id"] == first["conversation_id"] and reply["job_id"] != first["job_id"]
        (r2,) = await finish(client, [reply["job_id"]])
        (thread,) = await conversations(client)

    assert (r2["text"], r2["account"], r2["native_session_id"], r2["turn"]) == ("second answer", "claude-02", "sess-A", 2)
    first_call, second_call = aip.harness.events()
    assert "--resume" not in first_call["argv"]
    assert flag_value(second_call["argv"], "--resume") == "sess-A"  # the native session id is passed through
    assert second_call["claude_dir"] == first_call["claude_dir"] == str(aip.tmp / "claude-02")  # the same account
    assert second_call["stdin"] == "turn two"
    assert (thread["turns"], thread["tokens"], thread["account"], thread["native_session_id"]) == (2, 45, "claude-02", "sess-A")
    assert thread["cost_usd"] == pytest.approx(0.03) and thread["last_job_id"] == reply["job_id"]
    assert thread["active_job_id"] is None and thread["last_job_state"] == "succeeded"
    session = await fetch(aip.pool, "select connection_id from public.ai_sessions where session_id = 'sess-A'")
    assert session == [("claude-02",)]  # the router keeps its own record of where the session lives


RESUME: dict[str, tuple[str, str, Callable[[str], dict[str, Any]], list[str]]] = {
    # ai: (account, first turn's session id, the fake's output for that session, the resume argv that must follow)
    "claude": ("claude-02", "s-claude", lambda s: {"stdout": claude_ok("c", session=s)}, ["--resume", "s-claude"]),
    "gemini": ("agy-01", "s-agy", lambda s: {"stdout": agy_ok("g", conversation=s)}, ["--conversation", "s-agy"]),
    "codex": (
        "codex-01",
        "s-codex",
        lambda s: {"stdout": json.dumps({"text": "x", "thread_id": s})},
        ["resume", "--", "s-codex"],
    ),
    "hermes": (
        "hermes-01",
        "s-hermes",
        lambda s: {"stdout": "hermes text", "usage_file_data": {"session_id": s, "estimated_cost_usd": 0.0001}},
        ["--resume", "s-hermes"],
    ),
}


@pytest.mark.parametrize("ai", sorted(RESUME))
async def test_each_ais_own_resume_flag_carries_the_session_to_the_same_account(
    aip_factory: Callable[..., Awaitable[Aip]], ai: str
) -> None:
    aip = await aip_factory(codex=1, hermes=1)
    account, session, output, resume = RESUME[ai]
    aip.harness.set_response(**output(session))
    async with aip.client() as client:
        first = await start(client, ai=ai)
        assert first["account"] == account
        await finish(client, [first["job_id"]])
        reply = await call(client, "ai_reply", conversation_id=first["conversation_id"], message="follow up")
        assert reply["account"] == account
        (second,) = await finish(client, [reply["job_id"]])
    assert second["ok"] is True and second["native_session_id"] == session and second["turn"] == 2
    argv = aip.harness.events()[-1]["argv"]
    assert any(argv[i : i + len(resume)] == resume for i in range(len(argv))), argv
    if ai == "gemini":
        assert aip.harness.events()[-1]["userprofile"] == str(aip.tmp / "agy-01")  # agy's own home, both turns
    if ai == "codex":
        assert aip.harness.events()[-1]["codex_home"] == str(aip.tmp / "codex-01")


async def test_a_reply_carries_over_mode_cwd_and_model_and_can_change_the_timeout(
    aip_factory: Callable[..., Awaitable[Aip]],
) -> None:
    roots = Path(os.environ["FARM_DATA_DIR"]) / "workspaces"
    aip = await aip_factory(edit_root=roots)
    work = roots / "work"
    work.mkdir(parents=True)
    aip.harness.set_response(stdout=claude_ok("done", session="sess-E"))
    async with aip.client() as client:
        first = await start(
            client, ai="claude", account="claude-02", model="sonnet", mode="edit", cwd=str(work), timeout_s=123
        )
        await finish(client, [first["job_id"]])
        plain = await call(client, "ai_reply", conversation_id=first["conversation_id"], message="more")
        await finish(client, [plain["job_id"]])
        quicker = await call(client, "ai_reply", conversation_id=first["conversation_id"], message="again", timeout_s=77)
        await finish(client, [quicker["job_id"]])
    rows = await fetch(aip.pool, "select turn, model, mode, cwd, timeout_s from public.ai_jobs order by turn")
    assert rows == [
        (1, "sonnet", "edit", str(work), 123),
        (2, "sonnet", "edit", str(work), 123),
        (3, "sonnet", "edit", str(work), 77),
    ]
    assert [e["cwd"] for e in aip.harness.events()] == [str(work.resolve())] * 3  # every turn ran in the same cwd


# --- a conversation without a session, and one that must not be started twice ---------------------------------------------


async def test_a_conversation_whose_first_turn_failed_has_no_session_to_reply_to_but_can_be_started_again(
    aip: Aip,
) -> None:
    aip.harness.set_response(per_account={"claude-02": CRASH})
    async with aip.client() as client:
        first = await start(client, ai="claude", account="claude-02")
        (failed,) = await finish(client, [first["job_id"]])
        assert failed["ok"] is False and failed["error"]["kind"] == "crash"
        (thread,) = await conversations(client)
        assert (thread["turns"], thread["native_session_id"], thread["last_job_state"]) == (0, None, "failed")

        refused = await call(client, "ai_reply", conversation_id=first["conversation_id"], message="hello?")
        assert refused["ok"] is False and refused["error"]["kind"] == "bad_request"
        assert "ai_start(conversation_id=...)" in refused["error"]["message"] and refused["error"]["account"] == "claude-02"

        aip.harness.set_response(per_account={"claude-02": {"stdout": claude_ok("now it works", session="sess-N")}})
        again = await start(client, task="retry the first turn", conversation_id=first["conversation_id"])
        assert again["turn"] == 1 and again["account"] == "claude-02"  # still the first turn, same conversation
        (done,) = await finish(client, [again["job_id"]])
        (thread,) = await conversations(client)
    assert done["ok"] is True and (thread["turns"], thread["native_session_id"]) == (1, "sess-N")


async def test_only_one_turn_of_a_conversation_runs_at_a_time(aip: Aip) -> None:
    aip.harness.set_response(stdout=claude_ok("x", session="sess-1"))
    async with aip.client() as client:
        first = await start(client, ai="claude")
        await finish(client, [first["job_id"]])
        aip.harness.set_response(stdout=claude_ok("slow", session="sess-1"), delay_s=1.5)
        running = await call(client, "ai_reply", conversation_id=first["conversation_id"], message="one")
        assert running["ok"] is True
        racing = await call(client, "ai_reply", conversation_id=first["conversation_id"], message="two")
        assert racing["ok"] is False and racing["error"]["kind"] == "conversation_busy"
        await finish(client, [running["job_id"]])
    assert await fetch(aip.pool, "select count(*) from public.ai_jobs") == [(2,)]  # the refused turn left no job


async def test_unknown_conversations_are_not_found_and_the_ai_and_account_cannot_be_changed(aip: Aip) -> None:
    aip.harness.set_response(stdout=claude_ok("x", session="sess-1"))
    async with aip.client() as client:
        first = await start(client, ai="claude")
        await finish(client, [first["job_id"]])
        ghost = str(uuid4())
        for refusal in (
            await call(client, "ai_reply", conversation_id=ghost, message="m"),
            await call(client, "ai_start", task="t", conversation_id=ghost),
        ):
            assert refusal["ok"] is False and refusal["error"]["kind"] == "not_found"
        other_ai = await call(client, "ai_start", task="t", conversation_id=first["conversation_id"], ai="gemini")
        other_account = await call(
            client, "ai_start", task="t", conversation_id=first["conversation_id"], account="claude-03"
        )
        retry = await call(
            client, "ai_start", task="t", conversation_id=first["conversation_id"], retry_other_account=True
        )
    assert other_ai["error"]["kind"] == "bad_request" and "is a claude conversation, not gemini" in other_ai["error"]["message"]
    assert other_account["error"]["kind"] == "bad_request" and "lives on account 'claude-02'" in other_account["error"]["message"]
    assert retry["error"]["kind"] == "bad_request" and "first turns only" in retry["error"]["message"]
    assert await fetch(aip.pool, "select count(*) from public.ai_jobs") == [(1,)]


# --- the account of a conversation is gone for a while: wait for it, never move -----------------------------------------------


async def test_a_conversation_never_moves_to_another_account_it_waits_for_the_one_that_holds_its_session(
    aip: Aip,
) -> None:
    wrong = {"stdout": claude_ok("WRONG ACCOUNT", session="sess-B")}
    aip.harness.set_response(per_account={"claude-02": {"stdout": claude_ok("one", session="sess-A")}, "claude-03": wrong})
    async with aip.client() as client:
        first = await start(client, ai="claude")
        await finish(client, [first["job_id"]])

        # 1. the account reaches its usage limit during the reply: the reply fails, with the time it is back
        aip.harness.set_response(per_account={"claude-02": {"stdout": claude_limit(), "exit_code": 1}, "claude-03": wrong})
        reply = await call(client, "ai_reply", conversation_id=first["conversation_id"], message="two")
        (limited,) = await finish(client, [reply["job_id"]])
        error = limited["error"]
        assert limited["ok"] is False and (error["kind"], error["cause"]) == ("account_unavailable", "limit")
        assert (error["ai"], error["account"]) == ("claude", "claude-02")
        assert datetime.fromisoformat(error["retry_at"]) == RESET_AT
        assert [a["account"] for a in limited["attempts"]] == ["claude-02"]  # no second attempt elsewhere

        # 2. the Farm now knows the account is out: the next reply is refused at once, no job is created
        jobs_before = await fetch(aip.pool, "select count(*) from public.ai_jobs")
        refused = await call(client, "ai_reply", conversation_id=first["conversation_id"], message="three")
        assert refused["ok"] is False and refused["error"]["kind"] == "account_unavailable"
        assert refused["error"]["account"] == "claude-02" and datetime.fromisoformat(refused["error"]["retry_at"]) == RESET_AT
        assert await fetch(aip.pool, "select count(*) from public.ai_jobs") == jobs_before

        # 3. once the reset has passed the same conversation goes on, on the same account and session
        aip.clock.advance(7 * 3600)
        aip.harness.set_response(
            per_account={"claude-02": {"stdout": claude_ok("after the wait", session="sess-A")}, "claude-03": wrong}
        )
        again = await call(client, "ai_reply", conversation_id=first["conversation_id"], message="four")
        (resumed,) = await finish(client, [again["job_id"]])
    assert resumed["ok"] is True and resumed["text"] == "after the wait" and resumed["turn"] == 2
    assert all(e["claude_dir"] == str(aip.tmp / "claude-02") for e in aip.harness.events())  # claude-03 was never called
    assert flag_value(aip.harness.events()[-1]["argv"], "--resume") == "sess-A"


async def test_a_reply_to_a_logged_out_account_is_refused_with_no_time_to_wait_for(aip: Aip) -> None:
    aip.harness.set_response(stdout=claude_ok("one", session="sess-A"))
    async with aip.client() as client:
        first = await start(client, ai="claude")
        await finish(client, [first["job_id"]])
        async with aip.pool.connection() as conn:
            await conn.execute("update public.connections set status = 'needs_login' where id = 'claude-02'")
        refused = await call(client, "ai_reply", conversation_id=first["conversation_id"], message="two")
    error = refused["error"]
    assert refused["ok"] is False and (error["kind"], error["account"], error["retry_at"]) == ("account_unavailable", "claude-02", None)
    assert "needs_login" in error["message"]  # a person must log the account in: there is no reset to wait for


async def test_a_logout_during_a_reply_asks_for_the_login_command(aip: Aip) -> None:
    aip.harness.set_response(stdout=claude_ok("one", session="sess-A"))
    async with aip.client() as client:
        first = await start(client, ai="claude")
        await finish(client, [first["job_id"]])
        aip.harness.set_response(stdout=json.dumps({"result": "Not logged in", "is_error": True}), exit_code=1)
        reply = await call(client, "ai_reply", conversation_id=first["conversation_id"], message="two")
        (result,) = await finish(client, [reply["job_id"]])
    error = result["error"]
    assert (error["kind"], error["cause"], error["retry_at"]) == ("account_unavailable", "auth", None)
    assert "farm ai login claude-02" in error["message"]


# --- totals and the list of threads ---------------------------------------------------------------------------------------------


async def test_a_failed_turn_adds_no_turn_and_the_totals_add_up(aip: Aip) -> None:
    ok = {"stdout": claude_ok("fine", session="sess-A", cost=0.01, usage=(10, 5))}
    aip.harness.set_response(per_account={"claude-02": ok})
    async with aip.client() as client:
        first = await start(client, ai="claude")
        await finish(client, [first["job_id"]])
        aip.harness.set_response(per_account={"claude-02": CRASH})
        broken = await call(client, "ai_reply", conversation_id=first["conversation_id"], message="two")
        (failed,) = await finish(client, [broken["job_id"]])
        aip.harness.set_response(
            per_account={"claude-02": {"stdout": claude_ok("fixed", session="sess-A", cost=0.02, usage=(20, 10))}}
        )
        fixed = await call(client, "ai_reply", conversation_id=first["conversation_id"], message="three")
        await finish(client, [fixed["job_id"]])
        (thread,) = await conversations(client)
    assert failed["ok"] is False and failed["error"]["kind"] == "crash" and broken["turn"] == 2
    assert fixed["turn"] == 2  # the failed turn did not count, so the next try is turn 2 again
    assert (thread["turns"], thread["tokens"], thread["last_job_id"]) == (2, 45, fixed["job_id"])
    rows = await fetch(aip.pool, "select turn, state from public.ai_jobs order by turn, state")  # (the clock is frozen)
    assert rows == [(1, "succeeded"), (2, "failed"), (2, "succeeded")]


async def test_ai_conversations_lists_the_open_threads_with_their_filters(aip: Aip) -> None:
    aip.harness.set_response(
        per_account={
            "claude-02": {"stdout": claude_ok("c", session="s-1", usage=(10, 5))},
            "agy-01": {"stdout": agy_ok("g", conversation="s-2")},
            "claude-03": {"stdout": claude_ok("slow", session="s-3"), "delay_s": 2.0},
        }
    )
    async with aip.client() as client:
        c1 = await start(client, ai="claude", account="claude-02")
        c2 = await start(client, ai="gemini")
        await finish(client, [c1["job_id"], c2["job_id"]])
        c3 = await start(client, ai="claude", account="claude-03")  # still running while we look

        everything = await conversations(client)
        assert len(everything) == 3
        assert everything[0]["conversation_id"] == c3["conversation_id"]  # the one used last comes first
        assert {c["conversation_id"] for c in everything[1:]} == {c1["conversation_id"], c2["conversation_id"]}
        by_id = {c["conversation_id"]: c for c in everything}
        assert (by_id[c1["conversation_id"]]["account"], by_id[c1["conversation_id"]]["turns"]) == ("claude-02", 1)
        assert by_id[c1["conversation_id"]]["tokens"] == 15 and by_id[c1["conversation_id"]]["last_job_state"] == "succeeded"
        assert by_id[c2["conversation_id"]]["ai"] == "gemini" and by_id[c2["conversation_id"]]["native_session_id"] == "s-2"
        assert by_id[c3["conversation_id"]]["active_job_id"] == c3["job_id"]
        assert by_id[c3["conversation_id"]]["native_session_id"] is None  # nothing known until its first answer

        assert [c["conversation_id"] for c in await conversations(client, ai="gemini")] == [c2["conversation_id"]]
        assert [c["conversation_id"] for c in await conversations(client, account="claude-02")] == [c1["conversation_id"]]
        assert [c["conversation_id"] for c in await conversations(client, only_active=True)] == [c3["conversation_id"]]
        assert len(await conversations(client, limit=2)) == 2
        await finish(client, [c3["job_id"]])
        assert await conversations(client, only_active=True) == []


async def test_a_conversation_started_with_retry_other_account_lives_on_the_account_that_answered(aip: Aip) -> None:
    aip.harness.set_response(
        per_account={
            "claude-02": {"stdout": claude_limit(START + timedelta(hours=3)), "exit_code": 1},
            "claude-03": {"stdout": claude_ok("answered elsewhere", session="sess-3")},
        }
    )
    async with aip.client() as client:
        first = await start(client, ai="claude", retry_other_account=True)
        (result,) = await finish(client, [first["job_id"]])
        assert (result["ok"], result["account"], result["native_session_id"]) == (True, "claude-03", "sess-3")
        (thread,) = await conversations(client)
        assert (thread["account"], thread["turns"]) == ("claude-03", 1)

        aip.harness.set_response(per_account={"claude-03": {"stdout": claude_ok("still here", session="sess-3")}})
        reply = await call(client, "ai_reply", conversation_id=first["conversation_id"], message="again")
        assert reply["account"] == "claude-03"  # the session lives where it was created, not on the first choice
        (second,) = await finish(client, [reply["job_id"]])
    assert second["text"] == "still here" and second["turn"] == 2
