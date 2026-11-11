"""Memory is isolated and concurrent operators cannot silently overwrite each other."""

import asyncio
from uuid import uuid4

import pytest

from farm.db.pool import DbPool
from farm.knowledge import memory
from farm.tenancy import use_tenant


async def test_conflicting_writers_and_tombstones_preserve_versions(pool: DbPool) -> None:
    first = await memory.put(pool, "fosa", "architecture", "initial", 0)
    outcomes = await asyncio.gather(
        memory.put(pool, "fosa", "architecture", "one", first["version"]),
        memory.put(pool, "fosa", "architecture", "two", first["version"]),
        return_exceptions=True,
    )
    assert sum(isinstance(x, ValueError) for x in outcomes) == 1
    current = await memory.get(pool, "fosa", "architecture")
    assert current is not None and current["version"] == 2
    deleted = await memory.put(pool, "fosa", "architecture", "", 2, deleted=True)
    assert deleted["deleted"] and deleted["version"] == 3
    assert (await memory.list_notes(pool, "fosa"))["notes"] == []
    with pytest.raises(ValueError, match="conflict"):
        await memory.put(pool, "fosa", "architecture", "stale recreate", 0)
    restored = await memory.put(pool, "fosa", "architecture", "restored", 3)
    assert not restored["deleted"] and restored["version"] == 4


async def test_tenant_and_project_separation(pool: DbPool) -> None:
    await memory.put(pool, "fosa", "key", "private", 0)
    assert await memory.get(pool, "another", "key") is None
    other = uuid4()
    async with pool.connection() as conn:
        await conn.execute("insert into tenants(id,slug,name) values(%s,%s,'Other')", (other, str(other)))
    with use_tenant(other):
        assert await memory.get(pool, "fosa", "key") is None
        await memory.put(pool, "fosa", "key", "other private", 0)
    assert (await memory.get(pool, "fosa", "key"))["body"] == "private"


async def test_literal_prefix_pagination_and_bounds(pool: DbPool) -> None:
    for key in ("a%1", "a%2", "abc"):
        await memory.put(pool, "fosa", key, key, 0)
    page = await memory.list_notes(pool, "fosa", prefix="a%", limit=1)
    assert [n["key"] for n in page["notes"]] == ["a%1"]
    after = await memory.list_notes(pool, "fosa", prefix="a%", after=page["next"], limit=1)
    assert [n["key"] for n in after["notes"]] == ["a%2"] and after["next"] is None
    with pytest.raises(ValueError):
        await memory.list_notes(pool, "fosa", limit=101)
    with pytest.raises(ValueError):
        await memory.put(pool, "fosa", "huge", "♥" * 6000, 0)
    with pytest.raises(ValueError):
        await memory.put(pool, "../escape", "key", "x", 0)
