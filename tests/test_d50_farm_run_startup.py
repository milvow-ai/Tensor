"""D50: `farm run` could not start after F1B, and the failure hung the process instead of exiting.

The HTTP process clears the ambient tenant (each request sets its own), but building its tool surface reads
tenant #1's MCP providers through a tenant checkout, so start-up raised ``TenantContextMissing``. The error was
then hidden: the shared pool was never closed, its worker tasks never finished cancelling, and ``asyncio.run``
waited for them forever. Every test runs under ``use_tenant(Milvow)`` (conftest), which is why neither showed.
"""

from __future__ import annotations

import asyncio
import concurrent.futures
import socket
from pathlib import Path

import httpx
import pytest

import farm.db.pool as pool_module
from farm.control.run import run_farm
from farm.db.pool import DbPool, open_pool


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


async def test_farm_run_serves_with_no_ambient_tenant(
    pool: DbPool, db_url: str, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("FARM_DB_URL", db_url)  # the migrated test database, not the developer's own
    monkeypatch.setenv("FARM_DATA_DIR", str(tmp_path))
    port = _free_port()
    stop = asyncio.Event()
    farm = asyncio.create_task(run_farm(host="127.0.0.1", port=port, shutdown_event=stop))
    try:
        async with httpx.AsyncClient() as http:
            for _ in range(300):
                if farm.done():
                    farm.result()  # re-raises the start-up error (TenantContextMissing on the old code)
                    pytest.fail("farm run returned before serving")
                try:
                    if (await http.get(f"http://127.0.0.1:{port}/health")).status_code == 200:
                        break
                except httpx.HTTPError:
                    pass
                await asyncio.sleep(0.1)
            else:
                pytest.fail("farm run did not start serving within 30 s")
    finally:
        stop.set()
        await asyncio.wait_for(farm, timeout=60)


def test_a_failing_entry_point_exits_with_its_error_and_closes_the_pool(
    db_url: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("FARM_DB_URL", db_url)

    async def fails_after_opening_the_pool() -> None:
        await open_pool()
        raise RuntimeError("start-up failed")

    # In a thread with a deadline: on the old code `run` never returned.
    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as ex:
        future = ex.submit(pool_module.run, fails_after_opening_the_pool())
        with pytest.raises(RuntimeError, match="start-up failed"):
            future.result(timeout=30)
    assert pool_module._global_pool is None
