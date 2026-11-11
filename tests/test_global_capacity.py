"""One busy instance stays bounded even when different accounts have spare slots."""

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import replace

from farm.context import FarmContext
from farm.executors.base import ExecRequest, ExecResult
from farm.resources.router import route
from tests.farm_helpers import AsyncFnExecutor, ok_result


async def test_distinct_accounts_share_the_instance_execution_limit(
    farm_factory: Callable[..., Awaitable[FarmContext]],
) -> None:
    active = peak = 0

    async def execute(req: ExecRequest) -> ExecResult:
        nonlocal active, peak
        active += 1
        peak = max(peak, active)
        try:
            await asyncio.sleep(0.1)
            return ok_result()
        finally:
            active -= 1

    ctx = await farm_factory(max_parallel=1, executors={"api": AsyncFnExecutor(execute)})
    cloned = replace(ctx, deadline_s=40)
    answers = await asyncio.gather(
        *(
            route(
                cloned if i else ctx,
                "verify_email",
                {"email": f"person{i}@example.com"},
                pin=account,
                caller="test",
            )
            for i, account in enumerate(("reoon-01", "reoon-02"))
        )
    )
    assert all(answer.ok for answer in answers)
    assert peak == 1 and active == 0
