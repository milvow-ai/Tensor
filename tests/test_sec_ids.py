"""Security tests for safe identifiers and path traversal prevention (Requirement 5).

Shared slug pattern ^[a-z0-9][a-z0-9_-]{0,62}$ enforced across:
1. Command payloads (Pydantic validation)
2. Registry models (ConnectionSpec, CapabilitySpec, ProviderSpec)
3. Database CHECK constraints (migration 0004)
4. McpExecutor get_token_store_dir path containment assertion
"""

import os
import tempfile
from pathlib import Path

import psycopg.errors
import pytest
from pydantic import ValidationError

from farm.control.commands import (
    AddConnectionPayload,
    PausePayload,
    ResumePayload,
    SetPriorityPayload,
    SetRoutePayload,
    TestConnectionPayload,
    UpdateConnectionPayload,
)
from farm.db.pool import DbPool
from farm.executors.base import ConnectionView
from farm.executors.mcp.client import McpExecutor
from farm.registry.models import ConnectionSpec

BAD_IDS = [
    r"..\..\x",
    "../../x",
    "a/b",
    "UPPERCASE",
    "MixedCase",
    "",
    "-starts-with-hyphen",
    "_starts-with-underscore",
    "a" * 64,  # 64 chars exceeds max 63
    "has spaces",
    "has.dots",
]

VALID_IDS = [
    "a",
    "a1",
    "claude-01",
    "gemini_pro_v1",
    "a" * 63,
]


def test_command_payload_id_validation() -> None:
    """Command payloads must reject invalid IDs and accept valid slugs."""
    for bad in BAD_IDS:
        with pytest.raises(ValidationError):
            AddConnectionPayload(provider_id="claude", id=bad, auth_ref="env:MY_KEY")

        with pytest.raises(ValidationError):
            AddConnectionPayload(provider_id=bad, id="valid-id", auth_ref="env:MY_KEY")

        with pytest.raises(ValidationError):
            PausePayload(connection_id=bad)

        with pytest.raises(ValidationError):
            ResumePayload(connection_id=bad)

        with pytest.raises(ValidationError):
            SetPriorityPayload(connection_id=bad, priority=10)

        with pytest.raises(ValidationError):
            UpdateConnectionPayload(connection_id=bad)

        with pytest.raises(ValidationError):
            SetRoutePayload(capability=bad, provider_id="claude")

        with pytest.raises(ValidationError):
            SetRoutePayload(capability="ask_ai", provider_id=bad)

        with pytest.raises(ValidationError):
            TestConnectionPayload(connection_id=bad)

    for valid in VALID_IDS:
        payload = AddConnectionPayload(provider_id="claude", id=valid, auth_ref="env:MY_KEY")
        assert payload.id == valid


def test_registry_model_id_validation() -> None:
    """Registry models must reject invalid IDs and accept valid slugs."""
    for bad in BAD_IDS:
        with pytest.raises(ValidationError):
            ConnectionSpec(id=bad, auth_ref="env:MY_KEY")

    for valid in VALID_IDS:
        spec = ConnectionSpec(id=valid, auth_ref="env:MY_KEY")
        assert spec.id == valid


def test_mcp_executor_token_store_path_traversal() -> None:
    """McpExecutor.get_token_store_dir must raise ValueError on path traversal attempts."""
    with tempfile.TemporaryDirectory() as td:
        data_dir = Path(td)
        tokens_dir = data_dir / "tokens"
        tokens_dir.mkdir(parents=True)

        executor = McpExecutor(data_dir=data_dir)

        # Crafted IDs that attempt to escape tokens/
        traversal_ids = [
            r"..\..\escaped",
            "../../escaped",
            "..",
            ".",
        ]

        for crafted in traversal_ids:
            conn = ConnectionView(
                id=crafted,
                provider_id="mcp",
                auth_ref="env:KEY",
                meta={},
            )
            with pytest.raises(ValueError) as excinfo:
                executor.get_token_store_dir(conn)
            assert "Path traversal detected" in str(excinfo.value) or "Token store directory cannot be the tokens root" in str(excinfo.value)

        # Ensure no escaped directory was created
        assert not (data_dir / "escaped").exists()

        # Valid ID stays inside tokens/
        valid_conn = ConnectionView(
            id="safe-conn-01",
            provider_id="mcp",
            auth_ref="env:KEY",
            meta={},
        )
        resolved = executor.get_token_store_dir(valid_conn)
        assert resolved.is_relative_to(tokens_dir)
        assert resolved.exists()


@pytest.mark.asyncio
async def test_db_check_constraints_safe_ids(pool: DbPool) -> None:
    """Database CHECK constraints (0004_safe_ids) must reject invalid IDs on providers and connections."""
    async with pool.connection() as conn:
        # 1. Invalid provider ids rejected by DB check constraint
        for bad in (r"..\..\x", "UPPER", "bad/slash", "a" * 64):
            with pytest.raises(psycopg.errors.CheckViolation):
                await conn.execute(
                    "insert into public.providers (id, name, kind, executor) values (%s, 'Test', 'tool', 'api')",
                    (bad,),
                )
                await conn.commit()

        # 2. Valid provider insert succeeds
        await conn.execute(
            "insert into public.providers (id, name, kind, executor) values ('safe-p1', 'Test', 'tool', 'api')",
        )

        # 3. Invalid connection ids rejected by DB check constraint
        for bad in (r"..\..\x", "UPPER", "bad/slash", "a" * 64):
            with pytest.raises(psycopg.errors.CheckViolation):
                await conn.execute(
                    "insert into public.connections (id, provider_id, auth_ref) values (%s, 'safe-p1', 'env:KEY')",
                    (bad,),
                )
                await conn.commit()

        # 4. Valid connection insert succeeds
        await conn.execute(
            "insert into public.connections (id, provider_id, auth_ref) values ('safe-c1', 'safe-p1', 'env:KEY')",
        )


def test_token_store_data_dir_differs_from_default() -> None:
    """McpExecutor and resolve_token_store must share data_dir and work when FARM_DATA_DIR differs from default."""
    with tempfile.TemporaryDirectory() as td:
        custom_dir = Path(td) / "custom_farm_data"
        custom_dir.mkdir(parents=True)

        orig_env = os.environ.get("FARM_DATA_DIR")
        try:
            os.environ["FARM_DATA_DIR"] = str(custom_dir)

            # 1. Test via McpExecutor with default data_dir (reading FARM_DATA_DIR)
            executor1 = McpExecutor()
            conn1 = ConnectionView(
                id="conn-1",
                provider_id="mcp",
                auth_ref="token-store:acme-store",
                meta={},
            )
            store_dir1 = executor1.get_token_store_dir(conn1)
            assert store_dir1.is_relative_to(custom_dir / "tokens")
            assert store_dir1.exists()

            # 2. Test via McpExecutor with explicit data_dir differing from FARM_DATA_DIR
            other_dir = Path(td) / "explicit_data_dir"
            other_dir.mkdir(parents=True)
            executor2 = McpExecutor(data_dir=other_dir)
            store_dir2 = executor2.get_token_store_dir(conn1)
            assert store_dir2.is_relative_to(other_dir / "tokens")
            assert store_dir2.exists()
        finally:
            if orig_env is not None:
                os.environ["FARM_DATA_DIR"] = orig_env
            else:
                os.environ.pop("FARM_DATA_DIR", None)

