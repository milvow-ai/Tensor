"""Live smoke tests for agy and hermes CLI agent executors.

Marked with @pytest.mark.live and excluded from normal check.ps1 runs unless RUN_LIVE_TESTS=1.
"""

import os
from decimal import Decimal
from uuid import uuid4

import pytest

from farm.executors.base import ConnectionView, ExecRequest
from farm.executors.cli_agent.agy import AgyCliExecutor
from farm.executors.cli_agent.hermes import HermesCliExecutor

live_only = pytest.mark.skipif(
    os.environ.get("RUN_LIVE_TESTS") != "1",
    reason="Live smoke test: run explicitly with RUN_LIVE_TESTS=1",
)


@pytest.mark.live
@live_only
@pytest.mark.asyncio
async def test_live_agy_executor() -> None:
    conn = ConnectionView(
        auth_ref="cli:test",
        id="agy-01",
        provider_id="gemini",
        meta={"model": "gemini-3.8-flash-low"},
    )
    req = ExecRequest(
        request_id=uuid4(),
        capability="ask_ai",
        params={"task": "Respond with only the word PONG", "mode": "answer"},
        connection=conn,
    )
    executor = AgyCliExecutor()
    res = await executor.execute(req)

    assert res.ok, f"Agy execution failed: {res.error}"
    assert res.data is not None
    assert "PONG" in res.data["text"].upper()
    assert res.units_used.get("total_tokens", 0) > 0
    resp = res.data["text"].strip()
    print(f"\n[LIVE agy-01] Response: {resp}, Tokens: {res.units_used}, Cost: ${res.cost_usd}")


@pytest.mark.live
@live_only
@pytest.mark.asyncio
async def test_live_hermes_executor() -> None:
    conn = ConnectionView(
        auth_ref="cli:test",
        id="hermes-01",
        provider_id="hermes",
        meta={"profile": "farm-agent"},
    )
    req = ExecRequest(
        request_id=uuid4(),
        capability="ask_ai",
        params={"task": "Respond with only the word PONG", "mode": "answer"},
        connection=conn,
    )
    executor = HermesCliExecutor()
    res = await executor.execute(req)

    assert res.ok, f"Hermes execution failed: {res.error}"
    assert res.data is not None
    assert "PONG" in res.data["text"].upper()
    assert res.cost_usd >= Decimal(0)
    resp = res.data["text"].strip()
    print(f"\n[LIVE hermes-01] Response: {resp}, Tokens: {res.units_used}, Cost: ${res.cost_usd}")
