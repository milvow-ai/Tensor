"""R2 automatic same-provider failover: a failed answer turn continues on an untried account of the same AI."""

from __future__ import annotations

import hashlib
from collections.abc import Awaitable, Callable
from datetime import timedelta
from typing import Any
from uuid import UUID, uuid4

import pytest
from pydantic import ValidationError

import tests.test_ai_jobs as ai_jobs
from farm.ai import recovery, selection
from farm.ai.failures import Failure
from farm.ai.jobs import Attempt, Finish, JobManager, JobRecord, JobStore
from farm.db.pool import tenant_connection
from farm.gateway.ai_tools import ManagerHook
from farm.tenancy import use_tenant
from tests.conftest import START
from tests.farm_helpers import fetch
from tests.test_ai_jobs import (
    WAIT_S,
    Aip,
    ScriptedTransport,
    answered,
    call,
    crashed,
    eventually,
    finish,
    recovery_context,
    scripted_manager,
    spec,
)

# The AI-pool fixtures, exposed here by name (assigned, not imported, so test arguments do not redefine them).
aip = ai_jobs.aip
aip_factory = ai_jobs.aip_factory


def limited_with_partial(account: str, partial: str = "half of the second answer") -> Attempt:
    return Attempt(
        account,
        START,
        START,
        failure=Failure("limit", "usage limit reached"),
        partial_text=partial,
        partial_chars=len(partial),
    )


async def checkpoints(aip: Aip, job_id: Any) -> list[tuple[Any, ...]]:
    return await fetch(
        aip.pool,
        "select attempt, from_account, to_account, sha256 from public.ai_job_checkpoints "
        "where job_id = %s order by attempt",
        job_id,
    )


async def run_to_end(manager: JobManager, job_id: Any) -> Any:
    (result,), _ = await manager.wait([job_id], mode="all", timeout_s=WAIT_S)
    return result


async def test_a_mid_conversation_limit_continues_on_the_next_account_with_history_and_partial(aip: Aip) -> None:
    transport = ScriptedTransport(
        answered("claude-02", "first answer", session="sess-A"),
        limited_with_partial("claude-02"),
        lambda job, task: answered(job.account, "continued answer", session="sess-B"),
    )
    manager = scripted_manager(aip, transport)
    first = await manager.start(
        spec(task="first question", ai="claude", account="claude-02", retry_other_account=True), caller="test"
    )
    assert (await run_to_end(manager, first.job_id)).ok

    second = await manager.reply(first.conversation_id, "second question", timeout_s=None, caller="test")
    result = await run_to_end(manager, second.job_id)  # the same job id follows the failover to the end

    assert result.ok and result.text == "continued answer"
    resumed, resumed_task = transport.calls[1]
    handed, handed_task = transport.calls[2]
    assert resumed.account == "claude-02" and resumed.resume_session_id == "sess-A"
    assert resumed_task == "second question"  # the first attempt of a turn is the plain message
    assert handed.ai == "claude" and handed.account != "claude-02"
    assert handed.resume_session_id is None and "sess-A" not in handed_task  # the old session never moves
    assert handed.model == resumed.model  # never an automatic model change

    context = recovery_context(handed_task)
    assert context["automatic_failover"] is True and context["native_session_transferred"] is False
    assert context["task_to_continue"] == "second question"
    assert context["partial_output"] == "half of the second answer"
    assert [turn["answer"] for turn in context["history"]] == ["first answer"]

    record = await manager.store.get(second.job_id)
    assert record is not None and record.state == "succeeded" and record.account == handed.account
    assert [a["account"] for a in record.attempts] == ["claude-02", handed.account]
    assert record.attempts[0]["failover"] == "next_account" and record.attempts[0]["kind"] == "limit"
    assert record.attempts[0]["next_account"] == handed.account
    conv = await manager.conversations.get(first.conversation_id)
    assert conv is not None and conv.account == handed.account and conv.native_session_id == "sess-B"
    # the original input stays as it was; the context pack is a checkpoint of its own
    inputs = await fetch(aip.pool, "select payload from public.ai_job_inputs where job_id = %s", second.job_id)
    assert inputs == [(b"second question",)]
    digest = hashlib.sha256(handed_task.encode("utf-8", errors="surrogatepass")).hexdigest()
    assert await checkpoints(aip, second.job_id) == [(2, "claude-02", handed.account, digest)]


async def test_a_first_turn_without_partial_output_reruns_its_original_task(aip: Aip) -> None:
    transport = ScriptedTransport(crashed("claude-02"), lambda job, task: answered(job.account, task))
    manager = scripted_manager(aip, transport)
    started = await manager.start(
        spec(task="plain first task", ai="claude", account="claude-02", retry_other_account=True), caller="test"
    )
    result = await run_to_end(manager, started.job_id)
    assert result.ok and result.text == "plain first task"
    assert [job.account for job, _ in transport.calls][0] == "claude-02"
    assert transport.calls[1][0].account != "claude-02"


class CheckpointProbe:
    """Fails the first attempt, then records what the database held when the second attempt started."""

    def __init__(self, aip: Aip) -> None:
        self.aip = aip
        self.calls: list[tuple[JobRecord, str]] = []
        self.seen: list[tuple[Any, ...]] = []

    async def run(self, job: JobRecord, task: str) -> Attempt:
        self.calls.append((job, task))
        if len(self.calls) == 1:
            return limited_with_partial(job.account, "partial before the limit")
        self.seen = await checkpoints(self.aip, job.id)
        return answered(job.account, "done")


async def test_the_checkpoint_is_committed_before_the_next_attempt_starts(aip: Aip) -> None:
    probe = CheckpointProbe(aip)
    manager = JobManager(aip.ctx, transport=probe, tick_s=0.05, busy_backoff_s=0.05)
    aip.ctx.executors["ai-jobs:probe"] = ManagerHook(manager)  # stopped with the context
    started = await manager.start(
        spec(task="task", ai="claude", account="claude-02", retry_other_account=True), caller="test"
    )
    assert (await run_to_end(manager, started.job_id)).ok
    handed_task = probe.calls[1][1]
    digest = hashlib.sha256(handed_task.encode("utf-8", errors="surrogatepass")).hexdigest()
    assert probe.seen == [(2, "claude-02", probe.calls[1][0].account, digest)]
    partial = await fetch(
        aip.pool, "select partial, partial_chars from public.ai_job_checkpoints where job_id = %s", started.job_id
    )
    assert partial == [(b"partial before the limit", len("partial before the limit"))]


async def test_retry_other_account_false_opts_out(aip: Aip) -> None:
    transport = ScriptedTransport(crashed("claude-02"), answered("claude-03", "must not run"))
    manager = scripted_manager(aip, transport)
    started = await manager.start(
        spec(task="task", ai="claude", account="claude-02", retry_other_account=False), caller="test"
    )
    result = await run_to_end(manager, started.job_id)
    assert not result.ok and len(transport.calls) == 1
    record = await manager.store.get(started.job_id)
    assert record is not None and record.attempts[0].get("failover") is None
    assert await checkpoints(aip, started.job_id) == []


async def test_failover_is_bounded_to_five_attempts_and_keeps_every_failure_and_its_cost(
    aip_factory: Callable[..., Awaitable[Aip]],
) -> None:
    farm = await aip_factory(claude=7)
    transport = ScriptedTransport(lambda job, task: crashed(job.account))
    manager = scripted_manager(farm, transport)
    started = await manager.start(
        spec(task="task", ai="claude", account="claude-02", retry_other_account=True), caller="test"
    )
    result = await run_to_end(manager, started.job_id)
    assert not result.ok and len(transport.calls) == 5
    record = await manager.store.get(started.job_id)
    assert record is not None and record.state == "failed"
    assert len(record.attempts) == 5 and len({a["account"] for a in record.attempts}) == 5
    assert all(a["outcome"] == "failed" and a["kind"] == "crash" for a in record.attempts)
    assert [a["failover"] for a in record.attempts] == ["next_account"] * 4 + ["attempt_limit"]
    assert [row[0] for row in await checkpoints(farm, started.job_id)] == [2, 3, 4, 5]


async def test_an_exhausted_pool_asks_the_operator_and_never_changes_provider_or_model(aip: Aip) -> None:
    transport = ScriptedTransport(lambda job, task: crashed(job.account))
    manager = scripted_manager(aip, transport)
    started = await manager.start(
        spec(task="task", ai="claude", account="claude-02", model="sonnet", retry_other_account=True),
        caller="test",
    )
    result = await run_to_end(manager, started.job_id)
    assert not result.ok and len(transport.calls) == 3  # claude-02, -03, -04: the whole claude pool
    assert {job.ai for job, _ in transport.calls} == {"claude"}
    assert {job.model for job, _ in transport.calls} == {"sonnet"}
    record = await manager.store.get(started.job_id)
    assert record is not None and record.attempts[-1]["failover"] == "pool_exhausted"
    assert result.recovery is not None
    assert result.recovery["action"] == "operator_approval_required"
    assert result.recovery["provider_change_requires_approval"] is True
    assert result.recovery["accounts_tried"] == ["claude-02", "claude-03", "claude-04"]


async def test_an_edit_is_never_replayed_automatically(aip: Aip) -> None:
    with pytest.raises(ValidationError, match="only for mode 'answer'"):
        spec(task="task", ai="claude", mode="edit", retry_other_account=True)
    transport = ScriptedTransport(crashed("claude-02"), answered("claude-03", "must not run"))
    manager = scripted_manager(aip, transport)
    started = await manager.start(spec(task="edit task", ai="claude", account="claude-02", mode="edit"), caller="test")
    result = await run_to_end(manager, started.job_id)
    assert not result.ok and len(transport.calls) == 1
    assert result.recovery is not None and result.recovery["action"] == "reconcile_then_recover"
    assert await checkpoints(aip, started.job_id) == []


async def test_the_gateway_turns_failover_on_for_answers_by_default(aip: Aip) -> None:
    aip.manager.transport = ScriptedTransport(
        crashed("claude-02"), lambda job, task: answered(job.account, "from the next account")
    )
    async with aip.client() as client:
        started = await call(client, "ai_start", task="question", ai="claude", account="claude-02", model="sonnet", effort="high")
        assert started["ok"] is True, started
        (done,) = await finish(client, [started["job_id"]])
        assert done["ok"] is True and done["account"] != "claude-02"
    record = await aip.manager.store.get(UUID(started["job_id"]))
    assert record is not None and record.retry_other_account is True
    assert record.attempts[0]["failover"] == "next_account"


LONG = "L" * (recovery.MAX_ANSWER_CHARS + 10_000)


async def test_every_partial_of_a_failover_chain_travels_with_its_own_truncation(aip: Aip) -> None:
    transport = ScriptedTransport(
        lambda job, task: limited_with_partial(job.account, "P1 short"),
        lambda job, task: limited_with_partial(job.account, LONG),
        lambda job, task: answered(job.account, "done"),
    )
    manager = scripted_manager(aip, transport)
    started = await manager.start(
        spec(task="chain task", ai="claude", account="claude-02", retry_other_account=True), caller="test"
    )
    assert (await run_to_end(manager, started.job_id)).ok
    first, second, third = (job.account for job, _ in transport.calls)
    outputs = recovery_context(transport.calls[2][1])["partial_outputs"]
    assert [(o["attempt"], o["account"], o["truncated"], o["chars"]) for o in outputs] == [
        (1, first, False, len("P1 short")),
        (2, second, True, len(LONG)),
    ]
    assert outputs[0]["text"] == "P1 short" and len(outputs[1]["text"]) == recovery.MAX_ANSWER_CHARS
    saved = await fetch(
        aip.pool,
        "select attempt, from_account, partial_chars from public.ai_job_checkpoints where job_id = %s "
        "order by attempt",
        started.job_id,
    )
    assert saved == [(2, first, len("P1 short")), (3, second, len(LONG))]


async def test_explicit_recovery_reads_every_saved_partial_from_checkpoints_and_the_result(aip: Aip) -> None:
    transport = ScriptedTransport(
        lambda job, task: limited_with_partial(job.account, "P1"),
        lambda job, task: limited_with_partial(job.account, "P2"),
        lambda job, task: limited_with_partial(job.account, LONG),
    )
    manager = scripted_manager(aip, transport)
    started = await manager.start(
        spec(task="chain task", ai="claude", account="claude-02", retry_other_account=True), caller="test"
    )
    assert not (await run_to_end(manager, started.job_id)).ok  # the whole claude pool failed
    record = await manager.store.get(started.job_id)
    assert record is not None and record.attempts[-1]["failover"] == "pool_exhausted"
    context = recovery_context(await recovery.context_task(aip.pool, record))
    outputs = context["partial_outputs"]
    assert [o["attempt"] for o in outputs] == [1, 2, 3]
    assert [o["truncated"] for o in outputs] == [False, False, True]
    assert outputs[2]["chars"] == len(LONG) and context["partial_output_truncated"] is True


async def test_a_restart_between_handover_and_next_attempt_recovers_on_the_handed_account(aip: Aip) -> None:
    holder: dict[str, JobManager] = {}

    def fail_then_stop(job: JobRecord, task: str) -> Attempt:
        holder["manager"]._closing = True  # the process "dies": the handed-over job is never launched
        return limited_with_partial(job.account, "P1 before the restart")

    manager = scripted_manager(aip, ScriptedTransport(fail_then_stop))
    holder["manager"] = manager
    started = await manager.start(
        spec(task="restart task", ai="claude", account="claude-02", retry_other_account=True), caller="test"
    )

    async def handed() -> bool:
        return bool(await checkpoints(aip, started.job_id))

    await eventually(handed)
    swept = await JobStore(aip.pool).sweep_orphans(uuid4(), aip.ctx.clock() + timedelta(hours=1), 0)
    assert [job.id for job in swept] == [started.job_id]
    record = await manager.store.get(started.job_id)
    assert record is not None and record.state == "failed" and record.error_kind == "farm_restart"
    assert len(record.attempts) == 1 and record.account != "claude-02"
    (checkpoint,) = await fetch(
        aip.pool, "select payload from public.ai_job_checkpoints where job_id = %s", started.job_id
    )

    replay = ScriptedTransport(lambda job, task: answered(job.account, "resumed"))
    recovering = scripted_manager(aip, replay)
    child = await recovering.recover_job(started.job_id, caller="test")
    assert (await run_to_end(recovering, child.job_id)).ok
    job, task = replay.calls[0]
    assert job.account == record.account  # queued there, never run: not counted as tried
    assert job.resume_session_id is None
    assert task.encode("utf-8") == bytes(checkpoint[0])  # the verified checkpoint is the pending task
    assert recovery_context(task)["partial_output"] == "P1 before the restart"


async def test_a_context_too_large_for_failover_asks_for_manual_review(
    aip: Aip, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(recovery, "MAX_TASK_CHARS", 50)
    transport = ScriptedTransport(
        lambda job, task: limited_with_partial(job.account), answered("claude-03", "must not run")
    )
    manager = scripted_manager(aip, transport)
    started = await manager.start(
        spec(task="task", ai="claude", account="claude-02", retry_other_account=True), caller="test"
    )
    result = await run_to_end(manager, started.job_id)
    assert not result.ok and len(transport.calls) == 1
    record = await manager.store.get(started.job_id)
    assert record is not None and record.attempts[-1]["failover"] == "context_unavailable"
    assert result.recovery is not None
    assert result.recovery["available"] is False and result.recovery["tool"] is None
    assert result.recovery["action"] == "manual_review"
    assert await checkpoints(aip, started.job_id) == []


# --- operator alerts for failed jobs (internal: a row in public.alerts, nothing is sent) ------------------


async def failure_alerts(aip: Aip, job_id: Any) -> list[tuple[Any, ...]]:
    return await fetch(
        aip.pool,
        "select kind, severity, message, acked_at from public.alerts where ref = %s",
        f"ai-job:{job_id}",
    )


async def test_a_failed_job_leaves_one_internal_alert_with_the_operator_action(aip: Aip) -> None:
    transport = ScriptedTransport(lambda job, task: crashed(job.account))
    manager = scripted_manager(aip, transport)
    started = await manager.start(
        spec(task="SECRET-TASK-TEXT", ai="claude", account="claude-02", retry_other_account=True),
        caller="test",
    )
    assert not (await run_to_end(manager, started.job_id)).ok
    record = await manager.store.get(started.job_id)
    assert record is not None and record.attempts[-1]["failover"] == "pool_exhausted"

    ((kind, severity, message, acked_at),) = await failure_alerts(aip, started.job_id)
    assert (kind, severity, acked_at) == ("ai_job_failed", "warn", None)
    assert str(started.job_id) in message and record.account in message
    assert "crash" in message and "operator_approval_required" in message
    assert "SECRET-TASK-TEXT" not in message and "worker crashed" not in message

    # a second finish of the same job loses the state guard and adds nothing
    again = Finish(state="failed", account=record.account, attempts=[], failure=Failure("crash", "late"))
    assert await manager.store.finish(record, None, again, aip.ctx.clock()) is False
    assert len(await failure_alerts(aip, started.job_id)) == 1


async def test_a_failure_alert_belongs_to_the_jobs_tenant_only(aip: Aip, two_tenants: Any) -> None:
    manager = scripted_manager(aip, ScriptedTransport(crashed("claude-02")))
    started = await manager.start(spec(task="task", ai="claude", account="claude-02"), caller="test")
    assert not (await run_to_end(manager, started.job_id)).ok
    same = await fetch(
        aip.pool,
        "select a.tenant_id = j.tenant_id from public.alerts a join public.ai_jobs j on j.id = %s "
        "where a.ref = %s",
        started.job_id,
        f"ai-job:{started.job_id}",
    )
    assert same == [(True,)]
    with use_tenant(two_tenants.b):
        async with tenant_connection(aip.pool) as conn:
            cur = await conn.execute(
                "select count(*) from public.alerts where ref = %s", (f"ai-job:{started.job_id}",)
            )
            assert await cur.fetchone() == (0,)
        assert all(job.job_id != started.job_id for job in await manager.attention(100))


async def test_the_orphan_sweep_alerts_each_job_for_its_own_tenant(aip: Aip) -> None:
    store = JobStore(aip.pool)
    orphan = await store.create(
        conversation=None,
        choice=selection.Choice("sonnet", "high", "high", "explicit"),
        ai="claude",
        account="claude-02",
        spec=spec(task="edit task", ai="claude", account="claude-02", mode="edit"),
        caller="test",
        owner_id=uuid4(),  # a Farm process that died
        now=aip.ctx.clock(),
    )
    swept = await store.sweep_orphans(uuid4(), aip.ctx.clock() + timedelta(hours=1), 0)
    assert [job.id for job in swept] == [orphan.id] and swept[0].error_kind == "farm_restart"
    ((kind, _, message, acked_at),) = await failure_alerts(aip, orphan.id)
    assert kind == "ai_job_failed" and acked_at is None
    assert "farm_restart" in message and "reconcile_then_recover" in message and "edit task" not in message
    same = await fetch(
        aip.pool,
        "select a.tenant_id = j.tenant_id from public.alerts a join public.ai_jobs j on j.id = %s "
        "where a.ref = %s",
        orphan.id,
        f"ai-job:{orphan.id}",
    )
    assert same == [(True,)]
    assert await store.sweep_orphans(uuid4(), aip.ctx.clock() + timedelta(hours=2), 0) == []
    assert len(await failure_alerts(aip, orphan.id)) == 1


async def test_ai_attention_lists_failed_jobs_with_their_action_and_acks_nothing(aip: Aip) -> None:
    aip.manager.transport = ScriptedTransport(crashed("claude-02"))
    async with aip.client() as client:
        started = await call(
            client, "ai_start", task="question", ai="claude", account="claude-02", model="sonnet", effort="high", retry_other_account=False
        )
        (done,) = await finish(client, [started["job_id"]])
        assert done["ok"] is False
        pending = await call(client, "ai_attention", limit=10)
    assert pending["ok"] is True
    (job,) = [j for j in pending["jobs"] if j["job_id"] == started["job_id"]]
    assert job["state"] == "failed" and job["account"] == "claude-02"
    assert job["recovery"]["action"] == "recover_same_provider" and job["recovery"]["tool"] == "ai_recover"
    ((_, _, _, acked_at),) = await failure_alerts(aip, started["job_id"])
    assert acked_at is None
