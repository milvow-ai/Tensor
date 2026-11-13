"""Native Google sign-in uses staged credentials, verified identity and ACP metadata only."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

import httpx
import pytest
import respx

from farm.connect.drivers.gemini import GeminiPasteDriver
from farm.connect.drivers.registry import driver_for
from farm.connect.sealed import load_or_create_keys, seal
from farm.db.pool import DbPool, tenant_connection
from farm.tenancy import MILVOW_TENANT_ID
from tests.connect.test_drivers import _account, _cli, _service, _wait


def test_native_gemini_is_distinct_from_antigravity() -> None:
    assert isinstance(driver_for("gemini", "gemini-cli-01", {"cli": "gemini"}), GeminiPasteDriver)
    with pytest.raises(NotImplementedError, match="Antigravity"):
        driver_for("gemini", "agy-01", {"cli": "agy"})


@pytest.mark.parametrize("matching", [True, False])
async def test_gemini_staged_login(pool: DbPool, tmp_path: Path, matching: bool) -> None:
    home = tmp_path / "gemini"
    stored = home / ".gemini" / "oauth_creds.json"
    stored.parent.mkdir(parents=True)
    stored.write_text('{"old":"credential"}', encoding="utf-8")
    await _account(pool, "gemini", "gemini-cli-01", home, expected_email="correct@example.com")

    def make(provider: str, connection_id: str, meta: dict[str, Any]) -> GeminiPasteDriver:
        return GeminiPasteDriver(meta, command=_cli("gemini"))

    service = _service(pool, tmp_path, make)
    _, public_key, _ = load_or_create_keys(tmp_path / "keys")
    email = "correct@example.com" if matching else "other@example.com"
    with respx.mock(assert_all_called=True) as network:
        network.get("https://www.googleapis.com/oauth2/v2/userinfo").mock(return_value=httpx.Response(200, json={
            "id": "account-stable-id", "email": email, "verified_email": True, "name": "Gemini User",
        }))
        session_id = await service.start_login(MILVOW_TENANT_ID, "gemini-cli-01", "operator@example.com")
        try:
            async with asyncio.timeout(10):
                row = await _wait(pool, session_id, "waiting_for_user")
                while not row["needs_input"]:
                    await asyncio.sleep(0.05)
                    row = await _wait(pool, session_id, "waiting_for_user")
            envelope = seal("fake-code", public_key, f"{MILVOW_TENANT_ID}|{session_id}|login_input")
            await service.submit_input(MILVOW_TENANT_ID, session_id, envelope, "operator@example.com")
            done = await _wait(pool, session_id, "done" if matching else "failed")
        finally:
            await service.stop()
    if matching:
        assert done["evidence"]["models"] == ["gemini-example-preview"]
        assert done["evidence"]["test_status"] == "not_run"
        assert json.loads(stored.read_text(encoding="utf-8"))["access_token"] == "fake-google-token"
        async with tenant_connection(pool) as connection:
            cursor = await connection.execute("select status,meta from public.connections where id='gemini-cli-01'")
            row = await cursor.fetchone()
            assert row is not None and row[0] == "active"
            assert row[1]["model_discovery"]["source"] == "gemini_cli_acp_model_list"
    else:
        assert done["error_kind"] == "identity_mismatch"
        assert json.loads(stored.read_text(encoding="utf-8")) == {"old": "credential"}
