"""Reliability invariants exercised only with isolated DB rows and scripted login drivers."""

import asyncio
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any
from uuid import uuid4

import pytest

from farm.ai import selection
from farm.ai.accounts import load_accounts
from farm.connect.drivers.base import Failure, Success
from farm.connect.drivers.fake import FakeDriver
from farm.connect.service import LoginService, set_login_service
from farm.control.commands import execute_command
from farm.db.pool import DbPool
from farm.tenancy import MILVOW_TENANT_ID
from tests.connect.test_service import JANE, ScriptDriver, _account, _connection, _service, _untouched, _wait


@pytest.fixture
async def enrollment(pool: DbPool, tmp_path: Path) -> AsyncIterator[LoginService]:
    async def never(*args: Any) -> Any:
        raise AssertionError("Enrollment must not run inference")

    svc = LoginService(
        pool,
        keys_dir=tmp_path / "keys",
        test_job=never,
        driver_factory=lambda *args: FakeDriver([Failure("fake", "No live account")]),
    )
    set_login_service(svc)
    try:
        yield svc
    finally:
        await svc.stop()
        set_login_service(None)


async def test_fifteen_concurrent_operators_allocate_unique_ids_without_pool_deadlock(
    pool: DbPool,
    enrollment: LoginService,
) -> None:
    async def add(index: int) -> tuple[str, dict[str, Any]]:
        return await execute_command(
            pool,
            uuid4(),
            "add_accounts",
            {
                "provider_id": "claude",
                "count": 1,
                "emails": [f"operator{index}@example.test"],
                "request_id": str(uuid4()),
            },
            actor=f"operator-{index}",
        )

    results = await asyncio.wait_for(asyncio.gather(*(add(i) for i in range(15))), timeout=60)
    assert all(status == "done" for status, _ in results), results
    assert len({result["connection_ids"][0] for _, result in results}) == 15


async def test_replayed_enrollment_has_one_account_and_one_session(
    pool: DbPool, enrollment: LoginService
) -> None:
    payload = {
        "provider_id": "codex",
        "count": 1,
        "emails": ["person@example.test"],
        "request_id": str(uuid4()),
    }
    results = await asyncio.gather(
        *(execute_command(pool, uuid4(), "add_accounts", payload) for _ in range(4))
    )
    assert all(status == "done" for status, _ in results)
    assert len({result["connection_ids"][0] for _, result in results}) == 1
    assert len({result["session_ids"][0] for _, result in results}) == 1


async def test_partial_login_failure_returns_all_slots_and_resumes_same_request(
    pool: DbPool,
    enrollment: LoginService,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original = enrollment.start_login

    async def partial(tenant: Any, account: str, actor: str, method: Any = None) -> Any:
        if account.endswith("02"):
            raise RuntimeError("fake isolated startup failure")
        return await original(tenant, account, actor, method)

    monkeypatch.setattr(enrollment, "start_login", partial)
    payload = {
        "provider_id": "claude",
        "count": 2,
        "emails": ["one@example.test", "two@example.test"],
        "request_id": str(uuid4()),
    }
    status, result = await execute_command(pool, uuid4(), "add_accounts", payload)
    assert status == "failed" and len(result["accounts"]) == 2
    assert result["accounts"][1]["session_id"] is None
    monkeypatch.setattr(enrollment, "start_login", original)
    status, retried = await execute_command(pool, uuid4(), "add_accounts", payload)
    assert status == "done" and retried["connection_ids"] == result["connection_ids"]
    assert len(retried["session_ids"]) == 2


async def test_wrong_browser_identity_cannot_replace_working_auth(pool: DbPool, tmp_path: Path) -> None:
    home = await _account(pool, tmp_path, "claude-01", expected_email="somebody-else@example.test")
    svc = _service(
        pool, tmp_path, {"claude-01": ScriptDriver([Success()], identity=JANE)}, verify_with_test=False
    )
    try:
        row = await _wait(pool, await svc.start_login(MILVOW_TENANT_ID, "claude-01", "owner"), "failed")
        assert row["error_kind"] == "identity_mismatch"
        _untouched(home)
    finally:
        await svc.stop()


async def test_unknown_model_discovery_allows_explicit_choice_without_paid_enrollment_test(
    pool: DbPool,
    tmp_path: Path,
) -> None:
    await _account(pool, tmp_path, "claude-01", expected_email=JANE.email)
    svc = _service(
        pool, tmp_path, {"claude-01": ScriptDriver([Success()], models=())}, verify_with_test=False
    )
    try:
        row = await _wait(pool, await svc.start_login(MILVOW_TENANT_ID, "claude-01", "owner"), "done")
        assert row["evidence"]["test_ok"] is None and row["evidence"]["test_status"] == "not_run"
        _, meta = await _connection(pool, "claude-01")
        assert meta["model_discovery"]["status"] == "unknown"
        assert isinstance(
            selection.resolve("claude", meta, model="operator-picked-model", effort="high"), selection.Choice
        )
        assert (await load_accounts(pool))[0].offers("operator-picked-model")
    finally:
        await svc.stop()
