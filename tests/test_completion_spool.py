"""A lost database connection must never lose a completed answer or execute a second worker."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast
from uuid import UUID, uuid4

import psycopg
import pytest

from farm.ai import completions, results
from farm.ai.failures import Failure
from farm.ai.jobs import Completion, Finish, JobManager, JobRecord
from farm.context import FarmContext
from farm.tenancy import MILVOW_TENANT_ID, use_tenant

NOW = datetime(2026, 11, 11, tzinfo=UTC)


def record(owner: UUID) -> JobRecord:
    values: dict[str, Any] = dict.fromkeys(JobRecord.model_fields)
    values.update(
        id=uuid4(),
        conversation_id=uuid4(),
        turn=1,
        ai="claude",
        account="claude-01",
        model="sonnet",
        mode="answer",
        state="running",
        timeout_s=60,
        retry_other_account=False,
        caller="test",
        task_chars=4,
        owner_id=owner,
        heartbeat_at=NOW,
        attempts=[],
        usage={},
        cost_usd=Decimal(0),
        cost_estimated=True,
        created_at=NOW,
        started_at=NOW,
        recovery_depth=0,
    )
    return JobRecord.model_validate(values)


class Store:
    def __init__(self, row: JobRecord) -> None:
        self.row = row
        self.down = True
        self.writes = 0
        self.sweeps = 0

    async def get(self, job_id: UUID) -> JobRecord:
        assert job_id == self.row.id
        if self.down:
            raise psycopg.OperationalError("database unavailable")
        return self.row

    async def finish(self, row: JobRecord, owner: UUID, fin: Finish, now: datetime) -> bool:
        assert owner == self.row.owner_id and row.id == self.row.id
        if self.down:
            raise psycopg.OperationalError("database unavailable")
        if self.row.finished:
            return False
        self.writes += 1
        self.row = self.row.model_copy(
            update={
                "state": fin.state,
                "result_path": str(fin.stored.path) if fin.stored else None,
                "finished_at": now,
                "error_kind": fin.failure.kind if fin.failure else None,
            }
        )
        return True

    async def sweep_orphans(self, *args: Any) -> list[JobRecord]:
        assert self.row.finished, "terminal result must be reconciled before orphan sweep"
        self.sweeps += 1
        return []


def manager(store: Store) -> JobManager:
    ctx = cast(FarmContext, SimpleNamespace(pool=None, clock=lambda: NOW))
    mgr = JobManager(ctx)
    mgr.store = cast(Any, store)
    mgr._closing = True  # drive retries explicitly; never start a background worker in these unit tests
    return mgr


@pytest.fixture
def spool_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("FARM_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("FARM_ENV_FILE", str(tmp_path / "absent.env"))
    return tmp_path


@pytest.mark.parametrize("failed", [False, True])
async def test_database_outage_preserves_exact_success_or_partial_and_retries_only_sql(
    spool_dir: Path, failed: bool
) -> None:
    owner = uuid4()
    store = Store(record(owner))
    mgr = manager(store)
    mgr.instance_id = owner
    text = "full answer with unicode ♥ and NUL \x00" * 10000
    saved = results.store_result(store.row.id, text)
    fin = Finish(
        state="failed" if failed else "succeeded",
        account=store.row.account,
        attempts=[],
        stored=saved,
        failure=Failure("timeout", "worker timed out") if failed else None,
    )
    with use_tenant(MILVOW_TENANT_ID):
        assert await mgr._commit(store.row, fin) == "pending"
    assert store.writes == 0 and completions.entries()
    assert results.read_result(saved.path) == text
    store.down = False
    await mgr._flush_completions()
    assert store.row.state == fin.state and store.writes == 1
    assert not completions.entries() and results.read_result(saved.path) == text


async def test_a_new_manager_replays_before_sweep_and_committed_replay_is_idempotent(spool_dir: Path) -> None:
    owner = uuid4()
    store = Store(record(owner))
    mgr = manager(store)
    mgr.instance_id = owner
    saved = results.store_result(store.row.id, "complete answer")
    fin = Finish(state="succeeded", account=store.row.account, attempts=[], stored=saved)
    with use_tenant(MILVOW_TENANT_ID):
        assert await mgr._commit(store.row, fin) == "pending"
    path = completions.entries()[0]
    payload = completions.read(path)
    store.down = False
    restarted = manager(store)
    await restarted.recover()
    assert store.writes == 1 and store.sweeps == 1
    completions.write(MILVOW_TENANT_ID, store.row.id, payload)  # crash after SQL commit, before unlink
    await restarted._flush_completions()
    assert store.writes == 1 and not completions.entries()


async def test_terminal_conflict_does_not_override_or_delete_an_answer(spool_dir: Path) -> None:
    owner = uuid4()
    store = Store(record(owner))
    mgr = manager(store)
    mgr.instance_id = owner
    saved = results.store_result(store.row.id, "preserve this answer")
    fin = Finish(state="succeeded", account=store.row.account, attempts=[], stored=saved)
    with use_tenant(MILVOW_TENANT_ID):
        await mgr._commit(store.row, fin)
    store.down = False
    store.row = store.row.model_copy(update={"state": "cancelled", "finished_at": NOW})
    await mgr._flush_completions()
    assert store.row.state == "cancelled" and store.writes == 0
    assert results.read_result(saved.path) == "preserve this answer"
    assert list(completions.root().glob("*/*.conflict"))


@pytest.mark.parametrize("tamper", ["checksum", "result_path", "result_hash"])
async def test_corrupt_or_escaping_spool_is_retained_and_never_applied(spool_dir: Path, tamper: str) -> None:
    store = Store(record(uuid4()))
    store.down = False
    saved = results.store_result(store.row.id, "answer")
    if tamper == "result_path":
        saved = results.StoredResult(spool_dir / "foreign.txt", saved.chars, saved.sha256)
    item = Completion(
        tenant_id=MILVOW_TENANT_ID,
        job_id=store.row.id,
        owner_id=store.row.owner_id,
        completed_at=NOW,
        finish=Finish(state="succeeded", account=store.row.account, attempts=[], stored=saved),
    )
    path = completions.write(MILVOW_TENANT_ID, store.row.id, item.model_dump_json().encode())
    if tamper == "checksum":
        path.write_text('{"payload":"{}","sha256":"bad"}', encoding="utf-8")
    elif tamper == "result_hash":
        saved.path.write_text("modified result", encoding="utf-8")
    await manager(store)._flush_completions()
    assert store.writes == 0 and store.row.state == "running"
    assert list(completions.root().glob("*/*.invalid"))


def test_metadata_limit_is_checked_before_writing(spool_dir: Path) -> None:
    with pytest.raises(ValueError, match="spool limit"):
        completions.write(MILVOW_TENANT_ID, uuid4(), b"x" * (completions.MAX_METADATA_BYTES + 1))
    assert not completions.entries()


async def test_changed_database_references_close_job_without_replaying_or_losing_output(spool_dir: Path) -> None:
    class RestoredStore(Store):
        async def finish(self, row: JobRecord, owner: UUID, fin: Finish, now: datetime) -> bool:
            if fin.run_id:
                raise psycopg.errors.ForeignKeyViolation("run absent after restore")
            return await super().finish(row, owner, fin, now)

    owner = uuid4()
    store = RestoredStore(record(owner))
    store.down = False
    mgr = manager(store)
    mgr.instance_id = owner
    saved = results.store_result(store.row.id, "already completed answer")
    with use_tenant(MILVOW_TENANT_ID):
        assert await mgr._commit(store.row, Finish(
            state="succeeded", account=store.row.account, attempts=[], stored=saved, run_id=uuid4()
        )) == "conflict"
    assert store.row.state == "failed" and store.row.error_kind == "outcome_unknown"
    assert store.writes == 1 and not completions.entries()
    assert results.read_result(saved.path) == "already completed answer"
    assert list(completions.root().glob("*/*.conflict"))


async def test_corrupt_completion_does_not_leave_current_owners_job_running_forever(spool_dir: Path) -> None:
    owner = uuid4()
    store = Store(record(owner))
    mgr = manager(store)
    mgr.instance_id = owner
    path = completions.write(MILVOW_TENANT_ID, store.row.id, b"broken payload")
    with pytest.raises(psycopg.OperationalError):
        await mgr._flush_completions()
    assert path.exists()  # quarantine only after DB reconciliation is possible
    store.down = False
    await mgr._flush_completions()
    assert store.row.state == "failed" and store.row.error_kind == "outcome_unknown"
    assert store.writes == 1 and not completions.entries()
