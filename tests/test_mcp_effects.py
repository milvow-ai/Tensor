"""An uncertain write must never execute again on another pooled account."""

from pathlib import Path

import mcp_types
import pytest
from fastmcp import Client, FastMCP

from farm.db.pool import DbPool
from farm.executors.base import ErrorKind, ExecRequest, ExecResult
from tests.farm_helpers import ScriptedExecutor
from tests.mcp_helpers import mcp_provider, mcp_registry, world_factory


@pytest.mark.parametrize("safe", [False, True])
@pytest.mark.parametrize("shared", [False, True])
async def test_tool_annotations_control_replay_after_dispatch(
    pool: DbPool, tmp_path: Path, safe: bool, shared: bool, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("FARM_SHARED_INSTANCE", "1" if shared else "0")
    server = FastMCP("effects")

    # Even an idempotent write must not move to an account that may own a different workspace.
    @server.tool(annotations={"readOnlyHint": safe, "idempotentHint": True})
    def effect() -> str:
        return "done"

    called: list[str] = []

    def execute(req: ExecRequest) -> ExecResult:
        called.append(req.connection.id)
        if req.connection.id == "effects-01":
            return ExecResult(ok=False, error_kind=ErrorKind.TIMEOUT,
                error="reply lost: private customer task", request_sent=True)
        return ExecResult(
            ok=True,
            data={
                "call_tool_result": mcp_types.CallToolResult(
                    content=[mcp_types.TextContent(type="text", text="done")],
                    structured_content={"result": "done"},
                ).model_dump(mode="json", by_alias=True)
            },
        )

    async with world_factory(pool, tmp_path) as make:
        world = await make(
            mcp_registry(effects=mcp_provider(accounts=2)),
            {
                "effects-01": server,
                "effects-02": server,
            },
        )
        await world.sync()
        world.ctx.executors["mcp"] = ScriptedExecutor(execute)
        gateway = await world.gateway()
        async with Client(gateway) as client:
            answer = await client.call_tool_mcp("effects__effect", {})
        if safe:
            assert not answer.is_error and called[-1] == "effects-02"
        else:
            assert answer.is_error and "reconcile" in answer.content[0].text
            assert called == ["effects-01"]
        async with pool.connection() as conn:
            row = await (await conn.execute(
                "select last_error from connection_health where connection_id='effects-01'"
            )).fetchone()
        assert row and ("private customer task" not in row[0]) is shared


async def test_a_write_can_move_accounts_when_transport_proves_it_was_not_dispatched(
    pool: DbPool, tmp_path: Path
) -> None:
    from tests.fake_mcp_server import create_fake_rich_server
    from tests.mcp_helpers import broken_transport

    async with world_factory(pool, tmp_path) as make:
        servers = {f"fake-0{n}": create_fake_rich_server(f"fake-0{n}") for n in (1, 2)}
        world = await make(mcp_registry(fake=mcp_provider(accounts=2)), servers)
        await world.sync()
        await world.use("fake-01", broken_transport())
        gateway = await world.gateway()
        async with Client(gateway) as client:
            # whoami has no read-only annotation: fallback must rely on transport evidence.
            answer = await client.call_tool_mcp("fake__whoami", {})
        assert not answer.is_error and answer.content[0].text == "fake-02"
