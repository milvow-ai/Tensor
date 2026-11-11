from uuid import uuid4

from farm.db.pool import DbPool
from farm.gateway.middleware import current_identity
from farm.resources.trajectory import Trajectory
from farm.tenancy import MILVOW_TENANT_ID, TokenIdentity, use_tenant
from farm.tenancy.provenance import operator_snapshot
from tests.conftest import START


async def test_authenticated_person_is_persisted_separately_from_client_branding(pool: DbPool) -> None:
    person = uuid4()
    async with pool.connection() as conn:
        await conn.execute("insert into users(id,email) values(%s,'actual@example.com')", (person,))
    identity = TokenIdentity(
        token_id=uuid4(),
        tenant_id=MILVOW_TENANT_ID,
        user_id=person,
        user_email="actual@example.com",
        name="codex-work",
        scopes=("mcp",),
    )
    token = current_identity.set(identity)
    try:
        with use_tenant(MILVOW_TENANT_ID, person):
            trail = await Trajectory.start(
                pool, lambda: START, capability="test", caller="spoofed label", strategy=None
            )
        async with pool.connection() as conn:
            cur = await conn.execute(
                "select caller,operator_email,operator_client from runs where id=%s", (trail.run_id,)
            )
            assert await cur.fetchone() == ("spoofed label", "actual@example.com", "codex-work")
        with use_tenant(MILVOW_TENANT_ID, uuid4()):
            assert operator_snapshot() == (None, None, None)
    finally:
        current_identity.reset(token)
