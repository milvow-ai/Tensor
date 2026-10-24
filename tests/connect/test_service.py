"""LoginService end to end on the test database, with a scripted driver instead of a real CLI."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any
from uuid import UUID

import pytest

from farm.connect import sessions
from farm.connect.drivers.base import Failure, Identity, NeedsInput, NeedsVerification, Success, Url, UserCode
from farm.connect.sealed import SealedError, load_or_create_keys, seal
from farm.connect.service import CheckResult, LoginService
from farm.db.pool import DbPool, tenant_connection
from farm.tenancy import MILVOW_TENANT_ID
from tests.conftest import seed_connection

GATE = "GATE"  # the script waits here until the test opens the gate
INPUT = "INPUT"  # the script waits here until the service submits the pasted text
HANG = "HANG"  # the script never goes on (the person never acts)

JANE = Identity(provider_account_id="acct-jane", email="jane@example.com", plan="pro", org="org-1")


class ScriptDriver:
    """A LoginDriver that plays a script and records what the service did to it."""

    def __init__(
        self,
        script: list[Any],
        *,
        identity: Identity | None = JANE,
        models: tuple[str, ...] = ("opus", "sonnet"),
        method: str = "device_code",
        start_error: Exception | None = None,
    ) -> None:
        self.method = method
        self.script = script
        self.ident = identity
        self.model_list = list(models)
        self.start_error = start_error
        self.gate = asyncio.Event()
        self.inputs: asyncio.Queue[str] = asyncio.Queue()
        self.submitted: list[str] = []
        self.cancelled = False
        self.home: Path | None = None
        self.hint: str | None = None

    async def start(self, home: Path, hint: str | None) -> None:
        if self.start_error is not None:
            raise self.start_error
        self.home, self.hint = home, hint

    async def events(self) -> AsyncIterator[Any]:
        for item in self.script:
            if item == GATE:
                await self.gate.wait()
            elif item == INPUT:
                await self.inputs.get()
            elif item == HANG:
                await asyncio.sleep(3600)
            else:
                if isinstance(item, Success):
                    assert self.home is not None
                    (self.home / "login.json").write_text('{"who": "new login"}', encoding="utf-8")
                yield item

    async def submit(self, text: str) -> None:
        self.submitted.append(text)
        self.inputs.put_nowait(text)

    async def cancel(self) -> None:
        self.cancelled = True

    async def identity(self, home: Path) -> Identity | None:
        return self.ident

    async def models(self, home: Path) -> list[str]:
        return self.model_list


async def _ok_job(connection_id: str, home: Path) -> CheckResult:
    assert (home / "login.json").exists(), "the test job must run with the staging login"
    return CheckResult(ok=True, run_id="run-ok-1")


def _service(pool: DbPool, tmp_path: Path, drivers: dict[str, ScriptDriver], **kw: Any) -> LoginService:
    kw.setdefault("test_job", _ok_job)
    return LoginService(
        pool,
        driver_factory=lambda provider_id, connection_id, meta: drivers[connection_id],
        keys_dir=tmp_path / "keys",
        sweep_interval_s=3600,
        **kw,
    )


async def _account(pool: DbPool, tmp_path: Path, conn_id: str, **meta: Any) -> Path:
    """An AI account whose home already holds an older login (``old.json``)."""
    home = tmp_path / "ai" / conn_id
    home.mkdir(parents=True)
    (home / "old.json").write_text('{"who": "old login"}', encoding="utf-8")
    await seed_connection(
        pool, "claude", conn_id, kind="ai", executor="cli_agent", status="needs_login",
        meta={"cli": "claude", "home": str(home), **meta},
    )
    return home


async def _wait(pool: DbPool, session_id: UUID, *states: str, timeout: float = 5.0) -> dict[str, Any]:
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    row: dict[str, Any] | None = None
    while loop.time() < deadline:
        row = await sessions.get(pool, session_id)
        if row is not None and row["state"] in states:
            return row
        await asyncio.sleep(0.02)
    raise AssertionError(f"session stayed {row and row['state']}, expected one of {states}")


async def _connection(pool: DbPool, conn_id: str) -> tuple[str, dict[str, Any]]:
    async with tenant_connection(pool) as conn:
        cur = await conn.execute("select status, meta from public.connections where id = %s", (conn_id,))
        row = await cur.fetchone()
    assert row is not None
    return row[0], row[1]


def _untouched(home: Path) -> None:
    assert (home / "old.json").exists() and not (home / "login.json").exists()
    assert not list(home.parent.glob(f"{home.name}.login-*")), "the staging home must be discarded"


async def test_device_code_login_promotes_the_new_home_and_activates_the_account(
    pool: DbPool, tmp_path: Path
) -> None:
    home = await _account(pool, tmp_path, "claude-05", expected_email="jane@example.com")
    driver = ScriptDriver([UserCode(code="ABCD-1234", verification_uri="https://example.test/device"), GATE, Success()])
    svc = _service(pool, tmp_path, {"claude-05": driver})
    await svc.start()
    try:
        sid = await svc.start_login(MILVOW_TENANT_ID, "claude-05", "owner")
        row = await _wait(pool, sid, "waiting_for_user")
        assert row["user_code"] == "ABCD-1234" and row["verification_uri"] == "https://example.test/device"
        assert row["expires_at"] is not None and row["method"] == "device_code"
        assert driver.hint == "jane@example.com"
        driver.gate.set()
        row = await _wait(pool, sid, "done")
    finally:
        await svc.stop()
    assert row["evidence"] == {"test_run_id": "run-ok-1", "models": ["opus", "sonnet"], "test_ok": True}
    assert row["identity"]["email_masked"].startswith("j") and row["identity"]["plan"] == "pro"
    assert row["finished_at"] is not None
    status, meta = await _connection(pool, "claude-05")
    assert status == "active"
    assert meta["identity"]["account_hash"] == row["identity"]["account_hash"]
    assert meta["models"] == ["opus", "sonnet"] and meta["login"]["method"] == "device_code"
    assert (home / "login.json").exists() and not (home / "old.json").exists()
    previous = list(home.parent.glob("claude-05.prev-*"))
    assert len(previous) == 1 and (previous[0] / "old.json").exists()
    assert not list(home.parent.glob("claude-05.login-*"))
    assert driver.cancelled, "the driver is reaped after success"
    async with tenant_connection(pool) as conn:
        cur = await conn.execute("select action from public.audit_events where target = 'claude-05'")
        actions = {r[0] for r in await cur.fetchall()}
    assert {"login_session.queued", "login_session.waiting_for_user", "login_session.done"} <= actions
    assert "connection.login" in actions


async def test_a_pasted_code_reaches_the_driver_sealed(pool: DbPool, tmp_path: Path) -> None:
    await _account(pool, tmp_path, "claude-05")
    driver = ScriptDriver(
        [Url(url="https://example.test/oauth"), NeedsInput(hint="Paste the code"), INPUT, Success()],
        method="paste_code",
    )
    svc = _service(pool, tmp_path, {"claude-05": driver})
    await svc.start()
    try:
        sid = await svc.start_login(MILVOW_TENANT_ID, "claude-05", "owner")
        row = await _wait(pool, sid, "waiting_for_user")
        while not row["needs_input"]:
            await asyncio.sleep(0.02)
            row = await _wait(pool, sid, "waiting_for_user")
        assert row["auth_url"] == "https://example.test/oauth" and row["input_hint"] == "Paste the code"
        _, jwk, _ = load_or_create_keys(tmp_path / "keys")
        wrong = seal("code-123", jwk, f"{MILVOW_TENANT_ID}|{UUID(int=1)}|login_input")
        with pytest.raises(SealedError):
            await svc.submit_input(MILVOW_TENANT_ID, sid, wrong, "owner")
        sealed = seal("code-123", jwk, f"{MILVOW_TENANT_ID}|{sid}|login_input")
        await svc.submit_input(MILVOW_TENANT_ID, sid, sealed, "owner")
        row = await _wait(pool, sid, "done")
    finally:
        await svc.stop()
    assert driver.submitted == ["code-123"]
    assert row["needs_input"] is False


async def test_signing_in_as_an_account_the_pool_has_is_refused(pool: DbPool, tmp_path: Path) -> None:
    home = await _account(pool, tmp_path, "claude-05")
    svc = _service(pool, tmp_path, {"claude-05": ScriptDriver([UserCode(code="X", verification_uri="u"), Success()])})
    twin_hash = svc._account_hash(MILVOW_TENANT_ID, "claude", JANE.provider_account_id)
    await seed_connection(
        pool, "claude", "claude-02", kind="ai", executor="cli_agent", status="active",
        meta={"cli": "claude", "identity": {"account_hash": twin_hash}},
    )
    await svc.start()
    try:
        sid = await svc.start_login(MILVOW_TENANT_ID, "claude-05", "owner")
        row = await _wait(pool, sid, "duplicate")
    finally:
        await svc.stop()
    assert "claude-02" in row["error_excerpt"]
    _untouched(home)
    status, meta = await _connection(pool, "claude-05")
    assert status == "needs_login" and "identity" not in meta


@pytest.mark.parametrize(
    ("script", "kwargs", "state", "kind"),
    [
        pytest.param([Url(url="u"), Failure(kind="auth", excerpt="denied")], {}, "failed", "auth", id="failure"),
        pytest.param([Url(url="u"), HANG], {"user_timeout_s": 0.3}, "expired", "expired", id="nobody-acts"),
        pytest.param([UserCode(code="C", verification_uri="u"), Success()], {"bad_job": True}, "failed",
                     "test_failed", id="test-job-fails"),
        pytest.param([Url(url="u"), Success()], {"no_identity": True}, "failed", "no_identity", id="who-unknown"),
    ],
)
async def test_every_unhappy_end_leaves_the_account_as_it_was(
    pool: DbPool, tmp_path: Path, script: list[Any], kwargs: dict[str, Any], state: str, kind: str
) -> None:
    kwargs = dict(kwargs)  # the parametrize dict is shared; never mutate it
    home = await _account(pool, tmp_path, "claude-05")
    driver = ScriptDriver(script, identity=None if kwargs.pop("no_identity", False) else JANE)
    if kwargs.pop("bad_job", False):

        async def bad_job(connection_id: str, staging: Path) -> CheckResult:
            return CheckResult(ok=False, run_id="run-bad", detail="429 from the provider")

        kwargs["test_job"] = bad_job
    svc = _service(pool, tmp_path, {"claude-05": driver}, **kwargs)
    await svc.start()
    try:
        sid = await svc.start_login(MILVOW_TENANT_ID, "claude-05", "owner")
        row = await _wait(pool, sid, state)
    finally:
        await svc.stop()
    assert row["error_kind"] == kind and row["error_excerpt"]
    assert driver.cancelled
    _untouched(home)
    status, _ = await _connection(pool, "claude-05")
    assert status == "needs_login"


async def test_a_driver_that_crashes_ends_failed(pool: DbPool, tmp_path: Path) -> None:
    home = await _account(pool, tmp_path, "claude-05")
    driver = ScriptDriver([], start_error=RuntimeError("the CLI is not installed"))
    svc = _service(pool, tmp_path, {"claude-05": driver})
    await svc.start()
    try:
        sid = await svc.start_login(MILVOW_TENANT_ID, "claude-05", "owner")
        row = await _wait(pool, sid, "failed")
    finally:
        await svc.stop()
    assert row["error_kind"] == "crash" and "not installed" in row["error_excerpt"]
    _untouched(home)


async def test_provider_verification_is_shown_with_its_link(pool: DbPool, tmp_path: Path) -> None:
    await _account(pool, tmp_path, "claude-05")
    driver = ScriptDriver([NeedsVerification(url="https://example.test/verify", message="verify your account")])
    svc = _service(pool, tmp_path, {"claude-05": driver})
    await svc.start()
    try:
        sid = await svc.start_login(MILVOW_TENANT_ID, "claude-05", "owner")
        row = await _wait(pool, sid, "needs_verification")
    finally:
        await svc.stop()
    assert row["auth_url"] == "https://example.test/verify" and row["finished_at"] is not None


async def test_one_session_per_account_and_cancel_keeps_the_account(pool: DbPool, tmp_path: Path) -> None:
    home = await _account(pool, tmp_path, "claude-05")
    driver = ScriptDriver([Url(url="u"), GATE, Success()])
    svc = _service(pool, tmp_path, {"claude-05": driver})
    await svc.start()
    try:
        first = await svc.start_login(MILVOW_TENANT_ID, "claude-05", "owner")
        await _wait(pool, first, "waiting_for_user")
        assert await svc.start_login(MILVOW_TENANT_ID, "claude-05", "owner") == first
        assert await svc.cancel(MILVOW_TENANT_ID, first, "owner") == "cancelled"
        row = await _wait(pool, first, "cancelled")
        assert not svc.running()
    finally:
        await svc.stop()
    assert row["finished_at"] is not None
    _untouched(home)


async def test_stopping_the_farm_ends_running_sessions_as_restart(pool: DbPool, tmp_path: Path) -> None:
    home = await _account(pool, tmp_path, "claude-05")
    svc = _service(pool, tmp_path, {"claude-05": ScriptDriver([Url(url="u"), GATE])})
    await svc.start()
    sid = await svc.start_login(MILVOW_TENANT_ID, "claude-05", "owner")
    await _wait(pool, sid, "waiting_for_user")
    await svc.stop()
    row = await _wait(pool, sid, "failed")
    assert row["error_kind"] == "farm_restart"
    _untouched(home)


async def test_waiting_sessions_queue_behind_max_concurrent(pool: DbPool, tmp_path: Path) -> None:
    await _account(pool, tmp_path, "claude-05")
    await _account(pool, tmp_path, "claude-06")
    first, second = ScriptDriver([Url(url="u1"), GATE, Success()]), ScriptDriver([Url(url="u2"), GATE])
    svc = _service(pool, tmp_path, {"claude-05": first, "claude-06": second}, max_concurrent=1)
    await svc.start()
    try:
        s1 = await svc.start_login(MILVOW_TENANT_ID, "claude-05", "owner")
        s2 = await svc.start_login(MILVOW_TENANT_ID, "claude-06", "owner")
        await _wait(pool, s1, "waiting_for_user")
        await asyncio.sleep(0.1)
        assert (await sessions.get(pool, s2) or {})["state"] == "queued"
        first.gate.set()
        await _wait(pool, s1, "done")
        await _wait(pool, s2, "waiting_for_user")
    finally:
        await svc.stop()


async def test_a_provider_without_a_driver_fails_at_once(pool: DbPool, tmp_path: Path) -> None:
    await _account(pool, tmp_path, "claude-05")

    def no_driver(provider_id: str, connection_id: str, meta: Any) -> Any:
        raise NotImplementedError("CLI drivers arrive in L1B")

    svc = LoginService(pool, driver_factory=no_driver, test_job=_ok_job, keys_dir=tmp_path / "keys")
    sid = await svc.start_login(MILVOW_TENANT_ID, "claude-05", "owner")
    row = await _wait(pool, sid, "failed")
    assert row["error_kind"] == "not_implemented" and "L1B" in row["error_excerpt"]


async def test_restart_and_expiry_sweeps(pool: DbPool, tmp_path: Path) -> None:
    await _account(pool, tmp_path, "claude-05")
    await _account(pool, tmp_path, "claude-06")
    left = await sessions.create(pool, connection_id="claude-05", provider_id="claude", method="loopback", created_by="t")
    late = await sessions.create(pool, connection_id="claude-06", provider_id="claude", method="loopback", created_by="t")
    async with tenant_connection(pool) as conn:
        await conn.execute(
            "update public.login_sessions set state = 'waiting_for_user', expires_at = now() - interval '1 minute' "
            "where id = %s",
            (late,),
        )
    assert await sessions.expire_overdue(pool) == 1
    assert (await sessions.get(pool, late) or {})["state"] == "expired"
    assert await sessions.fail_active_after_restart(pool) == 1
    row = await sessions.get(pool, left)
    assert row is not None and row["state"] == "failed" and row["error_kind"] == "farm_restart"
    assert not await sessions.update(pool, left, state="done"), "a finished session never changes again"
