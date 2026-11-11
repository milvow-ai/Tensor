from collections.abc import Awaitable, Callable, Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

import pytest

from farm.ai.conversations import ConversationStore
from farm.ai.jobs import JobStore
from farm.db.pool import DbPool, tenant_connection
from farm.gateway.middleware import current_identity
from farm.registry import Registry
from farm.registry.sync import sync_registry
from farm.resources.router import request_hash
from farm.resources.trajectory import Trajectory, fetch_run
from farm.tenancy import MILVOW_TENANT_ID, TokenIdentity, use_tenant
from farm.tenancy.members import add_member
from farm.tenancy.models import Role
from tests.conftest import START, seed_connection
from tests.test_ai_jobs import Aip, aip_factory, claude_ok, spec

__all__ = ["aip_factory"]


@contextmanager
def actor(user: UUID, email: str) -> Iterator[None]:
    token = current_identity.set(
        TokenIdentity(
            token_id=uuid4(),
            tenant_id=MILVOW_TENANT_ID,
            user_id=user,
            user_email=email,
            name="IDE",
            scopes=("mcp",),
        )
    )
    try:
        with use_tenant(MILVOW_TENANT_ID, user):
            yield
    finally:
        current_identity.reset(token)


async def test_shared_worker_results_inputs_traces_and_sessions_are_person_private(pool: DbPool) -> None:
    alice = await add_member(pool, "alice@example.com", Role.MEMBER, str(uuid4()))
    bob = await add_member(pool, "bob@example.com", Role.MEMBER, str(uuid4()))
    await seed_connection(pool, "provider", "account")
    job, conversation = uuid4(), uuid4()
    with actor(bob, "bob@example.com"):
        async with tenant_connection(pool) as conn:
            await conn.execute(
                "insert into ai_conversations(id,ai,account) values(%s,'claude','account')", (conversation,)
            )
            await conn.execute(
                "insert into ai_jobs(id,conversation_id,turn,ai,account,operator_user_id) "
                "values(%s,%s,1,'claude','account',%s)",
                (job, conversation, bob),
            )
            await conn.execute(
                "insert into ai_job_inputs(job_id,payload,sha256) values(%s,%s,%s)",
                (job, b"private task", "a" * 64),
            )
        trail = await Trajectory.start(
            pool, lambda: START, capability="mcp:provider", caller="IDE", strategy=None
        )
        await trail.event("result", text="private response")
        key_bob = request_hash("mcp:provider", {"task": "identical"})
        assert await JobStore(pool).get(job) is not None
    with actor(alice, "alice@example.com"):
        assert await JobStore(pool).get(job) is None
        assert await JobStore(pool).request_cancel(job, START) is None
        assert await ConversationStore(pool).get(conversation) is None
        assert await fetch_run(pool, trail.run_id) is None
        assert request_hash("mcp:provider", {"task": "identical"}) != key_bob
        async with tenant_connection(pool) as conn:
            assert await (
                await conn.execute("select count(*) from ai_job_inputs where job_id=%s", (job,))
            ).fetchone() == (0,)
            assert await (
                await conn.execute("select count(*) from run_events where run_id=%s", (trail.run_id,))
            ).fetchone() == (0,)
    with actor(bob, "bob@example.com"):
        assert await JobStore(pool).get(job) is not None
    async with pool.connection() as conn:
        assert await (await conn.execute("select current_setting('app.user_id',true)")).fetchone() in (
            (None,),
            ("",),
        )


async def test_shared_instance_rejects_native_cli_registry_before_writing(
    pool: DbPool,
    registry: Registry,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from farm.registry import ConnectionSpec, ProviderSpec
    from farm.registry.loader import RegistryError

    registry.providers["claude"] = ProviderSpec(
        name="Claude",
        kind="ai",
        executor="cli_agent",
        connections=[ConnectionSpec(id="cli", auth_ref="cli:cli", meta={"cli": "claude"})],
    )
    monkeypatch.setenv("FARM_SHARED_INSTANCE", "1")
    with pytest.raises(RegistryError, match="personal"):
        await sync_registry(pool, registry)


async def test_queued_work_keeps_its_person_when_another_person_releases_the_slot(
    pool: DbPool,
    aip_factory: Callable[..., Awaitable[Aip]],
) -> None:
    world = await aip_factory()
    world.harness.set_response(stdout=claude_ok("answer"), delay_s=0.2)
    alice = await add_member(pool, "alice@example.com", Role.MEMBER, str(uuid4()))
    bob = await add_member(pool, "bob@example.com", Role.MEMBER, str(uuid4()))
    with actor(alice, "alice@example.com"):
        a = await world.manager.start(spec(ai="claude", account="claude-02"), caller="alice-ide")
    with actor(bob, "bob@example.com"):
        b = await world.manager.start(spec(ai="claude", account="claude-02"), caller="bob-ide")
    await world.manager.drain()
    async with pool.connection() as conn:
        rows = await (
            await conn.execute(
                "select j.operator_email,r.operator_email,j.state from ai_jobs j join runs r on r.id=j.run_id "
                "where j.id=any(%s) order by j.operator_email",
                ([a.job_id, b.job_id],),
            )
        ).fetchall()
    assert rows == [
        ("alice@example.com", "alice@example.com", "succeeded"),
        ("bob@example.com", "bob@example.com", "succeeded"),
    ]


async def test_shared_console_cannot_add_native_cli_and_does_not_expose_personal_memory(
    pool: DbPool, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from fastmcp import Client

    from farm.context import FarmContext
    from farm.control.commands import execute_provider_command
    from farm.gateway.server import build_server

    monkeypatch.setenv("FARM_SHARED_INSTANCE", "1")
    status, result = await execute_provider_command(pool, "add_provider", {
        "provider_id": "claude", "kind": "ai", "executor": "cli_agent", "cli": "claude",
    })
    assert status == "rejected" and "personal" in result["error"]
    async with pool.connection() as conn:
        assert await (await conn.execute("select count(*) from providers")).fetchone() == (0,)
    async with Client(await build_server(FarmContext(pool=pool, executors={}))) as client:
        assert not any(tool.name.startswith("memory_") for tool in await client.list_tools())


async def test_shared_http_gateway_identifies_each_member_and_revokes_access(
    pool: DbPool, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    import httpx
    from fastmcp import Client
    from fastmcp.client.transports import StreamableHttpTransport
    from fastmcp.exceptions import ToolError

    from farm.ai.jobs import JobManager
    from farm.control.run import create_farm_app, create_token
    from farm.tenancy.members import remove_member
    from tests.fake_mcp_server import create_fake_rich_server
    from tests.mcp_helpers import mcp_provider, mcp_registry, world_factory

    monkeypatch.setenv("FARM_SHARED_INSTANCE", "1")
    alice = await add_member(pool, "alice@example.com", Role.MEMBER, str(uuid4()))
    bob = await add_member(pool, "bob@example.com", Role.MEMBER, str(uuid4()))
    token_a = await create_token(pool, "alice-ide", user_id=alice, scopes=("mcp",))
    token_b = await create_token(pool, "bob-ide", user_id=bob, scopes=("mcp",))
    service_token = await create_token(pool, "unbound")
    async with world_factory(pool, tmp_path) as make:
        world = await make(mcp_registry(fake=mcp_provider()), {"fake-01": create_fake_rich_server()})
        await world.sync()
        manager = JobManager(world.ctx)
        app = await create_farm_app(world.ctx, job_manager=manager)
        assert [key for key in world.ctx.executors if key.startswith("ai-jobs:")] == [
            f"ai-jobs:{manager.instance_id}"
        ]

        def factory(headers: dict[str, str] | None = None, **options: Any) -> httpx.AsyncClient:
            return httpx.AsyncClient(transport=httpx.ASGITransport(app), headers=headers, **options)

        def client_for(token: str) -> Client[Any]:
            return Client(StreamableHttpTransport(
                "http://127.0.0.1/mcp", headers={"Authorization": f"Bearer {token}"}, httpx_client_factory=factory,
            ))

        base = getattr(app, "app", app)
        async with base.router.lifespan_context(base):
            async with client_for(token_a) as client:
                identity = (await client.call_tool("farm_operator", {})).structured_content
                assert identity and identity["email"] == "alice@example.com"
                response = await client.call_tool_mcp("fake__echo", {"message": "member tool call"})
                assert not response.is_error and response.content[0].text == "echo: member tool call"
            async with pool.connection() as conn:
                run = await (await conn.execute(
                    "select id,operator_email from runs where capability='mcp:fake'"
                )).fetchone()
                assert run and run[1] == "alice@example.com"
            async with client_for(token_b) as client:
                identity = (await client.call_tool("farm_operator", {})).structured_content
                assert identity and identity["email"] == "bob@example.com"
                with pytest.raises(ToolError, match="there is no run"):
                    await client.call_tool("get_run", {"run_id": str(run[0])})
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url="http://127.0.0.1") as http:
                assert (await http.post("/mcp", headers={"Authorization": f"Bearer {service_token}"})).status_code == 403
                assert await remove_member(pool, "alice@example.com")
                assert (await http.post("/mcp", headers={"Authorization": f"Bearer {token_a}"})).status_code == 401
