import httpx
import pytest

from farm.control.run import FarmAuthMiddleware, create_token
from farm.db.pool import DbPool
from tests.conftest import seed_connection


async def unused(scope: object, receive: object, send: object) -> None:
    raise AssertionError("reporting token must never reach the MCP application")


async def test_reporting_is_opt_in_scoped_and_never_returns_private_payloads(
    pool: DbPool,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    await seed_connection(pool, "provider", "account")
    async with pool.connection() as conn:
        await conn.execute("update providers set name='private-sentinel'")
        await conn.execute("update connections set label='private-sentinel', meta='{}'::jsonb")
    reporter = await create_token(pool, "metrics", scopes=("reports:usage",))
    operator = await create_token(pool, "operator", scopes=("mcp",))
    app = FarmAuthMiddleware(unused, pool)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url="http://test") as client:
        headers = {"Authorization": f"Bearer {reporter}"}
        assert (await client.get("/reports/usage", headers=headers)).status_code == 404
        monkeypatch.setenv("FARM_REPORTING_ENABLED", "1")
        monkeypatch.setenv("FARM_INSTANCE_ID", "person")
        assert (await client.get("/reports/usage")).status_code == 401
        assert (
            await client.get("/reports/usage", headers={"Authorization": f"Bearer {operator}"})
        ).status_code == 403
        result = await client.get("/reports/usage", headers=headers)
        assert result.status_code == 200 and "private-sentinel" not in result.text
        assert result.json()["instance"] == "person"
        assert result.json()["missing_metering_jobs"] == 0
        assert "no-store" in result.headers["cache-control"]
        assert (await client.post("/mcp", headers=headers)).status_code == 403
        assert (await client.post("/reports/usage", headers=headers)).status_code == 405
