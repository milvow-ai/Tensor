"""Registry <-> database: sync is atomic, idempotent and audited; export is its exact inverse."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

import pytest
import pytest_asyncio

from farm.db.pool import DbPool, open_pool
from farm.registry import Registry, RegistryError, load_registry
from farm.registry.sync import export_registry, sync_registry
from tests.farm_helpers import normalise

REAL_REGISTRY = Path(__file__).parent.parent / "config" / "registry.yaml"
EXAMPLE_REGISTRY = Path(__file__).parent.parent / "config" / "registry.example.yaml"
FIXTURE_REGISTRY = Path(__file__).parent / "fixtures" / "registry.yaml"


@pytest_asyncio.fixture(autouse=True)
async def _leave_the_database_clean(clean_pool: DbPool) -> None:
    """Syncing writes the shared singleton farm_settings row: every test here empties the database again."""


async def count(pool: DbPool, table: str) -> int:
    async with pool.connection() as conn:
        cur = await conn.execute(f"select count(*) from public.{table}")
        row = await cur.fetchone()
    assert row is not None
    return int(row[0])


@pytest.mark.parametrize(
    "path", [REAL_REGISTRY, EXAMPLE_REGISTRY, FIXTURE_REGISTRY], ids=["blank", "example", "fixture"]
)
async def test_sync_then_export_round_trips(pool: DbPool, path: Path) -> None:
    registry = load_registry(path)
    report = await sync_registry(pool, registry)
    assert report.changes > 0 and not report.orphans
    assert normalise(await export_registry(pool)) == normalise(registry)


async def test_second_sync_changes_nothing_and_writes_no_audit(pool: DbPool, registry: Registry) -> None:
    await sync_registry(pool, registry)
    audit = await count(pool, "audit_events")
    assert audit > 0

    again = await sync_registry(pool, registry)

    assert again.changes == 0
    assert await count(pool, "audit_events") == audit


async def test_changed_value_is_updated_and_audited_with_before_and_after(
    pool: DbPool, registry: Registry
) -> None:
    await sync_registry(pool, registry)
    registry.providers["reoon"].connections[0].priority = 7

    report = await sync_registry(pool, registry, actor="tester")

    assert report.updated == {"connections": 1} and report.created == {} and report.deleted == {}
    async with pool.connection() as conn:
        cur = await conn.execute(
            "select actor, action, target, before->>'priority', after->>'priority' from public.audit_events "
            "where actor = 'tester'"
        )
        assert await cur.fetchall() == [("tester", "registry.update", "connections:reoon-01", "1", "7")]


async def test_units_and_routes_removed_from_the_file_are_deleted_but_providers_are_orphans(
    pool: DbPool, registry: Registry
) -> None:
    await sync_registry(pool, registry)
    del registry.providers["reoon"].connections[0].units["credits"]
    registry.capabilities["verify_email"].routes = ["zerobounce"]
    del registry.providers["claude"]
    del registry.capabilities["ask_ai"]
    registry.budgets.per_provider.pop("reoon")

    report = await sync_registry(pool, registry)

    assert report.deleted == {"consumption_units": 1, "capability_routes": 1, "budgets": 1}
    assert report.orphans == {
        "providers": ["claude"],
        "connections": ["claude-02"],
        "capabilities": ["ask_ai"],
    }
    assert await count(pool, "providers") == 4  # nothing that owns history is deleted


async def test_dry_run_reports_the_changes_and_rolls_them_back(pool: DbPool, registry: Registry) -> None:
    report = await sync_registry(pool, registry, dry_run=True)

    assert report.dry_run and report.created["providers"] == 4
    assert await count(pool, "providers") == 0
    assert await count(pool, "audit_events") == 0


async def test_a_failing_sync_leaves_the_database_untouched(pool: DbPool, registry: Registry) -> None:
    await sync_registry(pool, registry)
    audit = await count(pool, "audit_events")
    registry.providers["reoon"].connections[0].priority = 9
    # not valid for the table's check constraint: the sync must fail as a whole
    registry.providers["clay"].connections[0].units["credits"].period = "bogus"

    with pytest.raises(Exception, match="consumption_units"):
        await sync_registry(pool, registry)

    async with pool.connection() as conn:
        cur = await conn.execute("select priority from public.connections where id = 'reoon-01'")
        assert await cur.fetchone() == (1,)  # the valid change in front of the bad row was rolled back too
    assert await count(pool, "audit_events") == audit


async def test_export_of_a_migrated_but_unsynced_database_is_a_blank_registry(pool: DbPool) -> None:
    """Blank start: an empty Farm is valid, and nothing is made up for it (no capability is injected)."""
    exported = await export_registry(pool)

    assert exported.providers == {} and exported.capabilities == {}
    assert exported.settings.owner_email


async def test_export_of_a_database_that_was_never_seeded_is_a_clear_error(pool: DbPool) -> None:
    async with pool.connection() as conn:
        await conn.execute("delete from public.farm_settings")
    try:
        with pytest.raises(RegistryError, match="farm registry sync"):
            await export_registry(pool)
    finally:  # the singleton row is shared by every test of the session
        async with pool.connection() as conn:
            await conn.execute("insert into public.farm_settings (id) values (1) on conflict (id) do nothing")


async def test_export_of_a_database_that_was_never_migrated_is_a_clear_error(
    scratch_db: Callable[[], str],
) -> None:
    empty = await open_pool(scratch_db())
    try:
        with pytest.raises(RegistryError, match="farm db migrate"):
            await export_registry(empty)
    finally:
        await empty.close()


async def test_capability_schemas_are_stored_for_the_console(pool: DbPool, registry: Registry) -> None:
    await sync_registry(pool, registry)
    async with pool.connection() as conn:
        cur = await conn.execute(
            "select input_schema->'properties' ? 'email', output_schema->'properties' ? 'status' "
            "from public.capabilities where name = 'verify_email'"
        )
        assert await cur.fetchone() == (True, True)
        cur = await conn.execute("select input_schema from public.capabilities where name = 'ask_ai'")
        assert await cur.fetchone() == ({},)  # no model yet: routable, but no generated tool
