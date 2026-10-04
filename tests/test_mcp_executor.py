"""Unit tests for McpExecutor: tool calls, token dirs, error mapping, sentinel leaks, generic servers."""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from uuid import uuid4

import pytest
from fastmcp import FastMCP
from fastmcp.client.auth.oauth import OAuthToken, TokenStorageAdapter
from key_value.aio._utils.sanitization import AlwaysHashStrategy
from key_value.aio.stores.filetree import FileTreeStore

from farm.executors.base import ConnectionView, ErrorKind, ExecRequest, Executor
from farm.executors.mcp import (
    McpExecutor,
    extract_value,
    map_request_params,
    map_tool_result,
)
from farm.secrets import resolve_auth, resolve_token_store
from tests.fake_mcp_server import create_fake_clay_server, create_fake_generic_server

SENTINEL_SECRET = "SENTINEL-secret-4f9c2a71"


@pytest.fixture
def temp_data_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    data_dir = tmp_path / "farm-data"
    data_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("FARM_DATA_DIR", str(data_dir))
    return data_dir


# --- 1. Per-account token-store dirs -------------------------------------------------------------


def test_per_account_token_store_dirs(temp_data_dir: Path) -> None:
    executor = McpExecutor(data_dir=temp_data_dir)

    conn1 = ConnectionView(
        id="clay-01",
        provider_id="clay",
        auth_ref="token-store:clay-01",
    )
    conn2 = ConnectionView(
        id="clay-02",
        provider_id="clay",
        auth_ref="token-store:clay-02",
    )

    dir1 = executor.get_token_store_dir(conn1)
    dir2 = executor.get_token_store_dir(conn2)

    assert dir1.is_dir()
    assert dir2.is_dir()
    assert dir1 != dir2
    assert dir1 == temp_data_dir / "tokens" / "clay-01"
    assert dir2 == temp_data_dir / "tokens" / "clay-02"

    # Verify resolve_token_store function
    assert resolve_token_store("token-store:clay-01") == dir1
    assert resolve_auth("token-store:clay-01", allow_token_store=True) == str(dir1)


# --- 2. Tool call & mapping ----------------------------------------------------------------------


@pytest.mark.asyncio
async def test_tool_call_successful_mapping(temp_data_dir: Path) -> None:
    server = create_fake_clay_server("clay-01")
    executor = McpExecutor(data_dir=temp_data_dir)

    conn = ConnectionView(
        id="clay-01",
        provider_id="clay",
        auth_ref="token-store:clay-01",
        meta={
            "server": server,
            "tool_map": {
                "find_person": {
                    "tool": "search-contacts",
                    "params": {
                        "query": "{name}",
                        "company": "company",
                        "limit": 5,
                    },
                    "result": {
                        "person": "contacts[0]",
                        "email": "contacts[0].email",
                        "total_results": "total",
                    },
                }
            },
        },
    )

    req = ExecRequest(
        request_id=uuid4(),
        capability="find_person",
        params={"name": "Sarah Connor", "company": "Cyberdyne"},
        connection=conn,
    )

    result = await executor.execute(req)

    assert result.ok is True
    assert result.error_kind is None
    assert result.found is True
    assert result.data is not None
    assert result.data["email"] == "sarah.connor@example.com"
    assert result.data["person"]["name"] == "Sarah Connor"
    assert result.data["total_results"] == 1
    assert result.units_used == {"credits": 1.0}
    assert result.latency_ms >= 0


# --- 3. Error / credit / auth mapping ------------------------------------------------------------


@pytest.mark.asyncio
async def test_auth_error_maps_to_needs_login(temp_data_dir: Path) -> None:
    server = create_fake_clay_server("clay-01")
    server.state.auth_error = True  # type: ignore[attr-defined]
    executor = McpExecutor(data_dir=temp_data_dir)

    conn = ConnectionView(
        id="clay-01",
        provider_id="clay",
        auth_ref="token-store:clay-01",
        meta={"server": server},
    )

    req = ExecRequest(
        request_id=uuid4(),
        capability="search-contacts",
        params={"query": "Alice"},
        connection=conn,
    )

    result = await executor.execute(req)

    assert result.ok is False
    assert result.error_kind == ErrorKind.NEEDS_LOGIN
    assert "clay-01" in str(result.error)
    assert "farm mcp login clay-01" in str(result.error)


@pytest.mark.asyncio
async def test_credits_exhausted_payload_maps_to_limit_reached(temp_data_dir: Path) -> None:
    server = create_fake_clay_server("clay-02")
    server.state.credits_exhausted = True  # type: ignore[attr-defined]
    executor = McpExecutor(data_dir=temp_data_dir)

    conn = ConnectionView(
        id="clay-02",
        provider_id="clay",
        auth_ref="token-store:clay-02",
        meta={"server": server},
    )

    req = ExecRequest(
        request_id=uuid4(),
        capability="search-contacts",
        params={"query": "Bob"},
        connection=conn,
    )

    result = await executor.execute(req)

    assert result.ok is False
    assert result.error_kind == ErrorKind.LIMIT_REACHED
    assert "Insufficient credits" in str(result.error) or "CREDITS_EXHAUSTED" in str(result.error)


@pytest.mark.asyncio
async def test_rate_limit_error_mapping(temp_data_dir: Path) -> None:
    server = create_fake_clay_server("clay-03")
    server.state.rate_limit_error = True  # type: ignore[attr-defined]
    executor = McpExecutor(data_dir=temp_data_dir)

    conn = ConnectionView(
        id="clay-03",
        provider_id="clay",
        auth_ref="token-store:clay-03",
        meta={"server": server},
    )

    req = ExecRequest(
        request_id=uuid4(),
        capability="search-contacts",
        params={"query": "Charlie"},
        connection=conn,
    )

    result = await executor.execute(req)

    assert result.ok is False
    assert result.error_kind == ErrorKind.RATE_LIMITED


@pytest.mark.asyncio
async def test_timeout_mapping(temp_data_dir: Path) -> None:
    server = FastMCP("hanging-server")

    @server.tool
    async def slow_tool() -> dict[str, str]:
        await asyncio.sleep(2.0)
        return {"status": "ok"}

    executor = McpExecutor(data_dir=temp_data_dir)
    conn = ConnectionView(
        id="slow-01",
        provider_id="slow",
        auth_ref="token-store:slow-01",
        meta={"server": server},
    )

    req = ExecRequest(
        request_id=uuid4(),
        capability="slow_tool",
        params={},
        connection=conn,
        timeout_s=0.1,
    )

    result = await executor.execute(req)

    assert result.ok is False
    assert result.error_kind == ErrorKind.TIMEOUT
    assert "timed out" in str(result.error).lower()


# --- 4. Sentinel no-leak -------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_sentinel_token_never_leaks(
    temp_data_dir: Path, caplog: pytest.LogCaptureFixture
) -> None:
    executor = McpExecutor(data_dir=temp_data_dir)
    server = create_fake_clay_server("clay-sentinel")

    conn = ConnectionView(
        id="clay-sentinel",
        provider_id="clay",
        auth_ref="token-store:clay-sentinel",
        meta={"server": server},
    )

    # Place a token containing the sentinel secret into the store
    store_dir = executor.get_token_store_dir(conn)
    key_val_store = FileTreeStore(
        data_directory=str(store_dir),
        key_sanitization_strategy=AlwaysHashStrategy(),
        collection_sanitization_strategy=AlwaysHashStrategy(),
    )
    adapter = TokenStorageAdapter(
        async_key_value=key_val_store,
        server_url="https://api.clay.com/v3/mcp",
    )
    token = OAuthToken(
        access_token=SENTINEL_SECRET,
        token_type="Bearer",
        refresh_token=f"{SENTINEL_SECRET}-refresh",
    )
    await adapter.set_tokens(token)

    # 1. Successful execution
    with caplog.at_level(logging.DEBUG):
        req_ok = ExecRequest(
            request_id=uuid4(),
            capability="search-contacts",
            params={"query": "Test"},
            connection=conn,
        )
        res_ok = await executor.execute(req_ok)
        assert res_ok.ok is True
        assert SENTINEL_SECRET not in res_ok.model_dump_json()
        assert SENTINEL_SECRET not in str(res_ok)
        assert SENTINEL_SECRET not in caplog.text

    # 2. Auth error execution
    server.state.auth_error = True  # type: ignore[attr-defined]
    server.state.auth_error_message = f"Unauthorized with token {SENTINEL_SECRET}"  # type: ignore[attr-defined]
    caplog.clear()

    with caplog.at_level(logging.DEBUG):
        req_err = ExecRequest(
            request_id=uuid4(),
            capability="search-contacts",
            params={"query": "Test"},
            connection=conn,
        )
        res_err = await executor.execute(req_err)
        assert res_err.ok is False
        assert SENTINEL_SECRET not in res_err.model_dump_json()
        assert SENTINEL_SECRET not in str(res_err)
        assert SENTINEL_SECRET not in caplog.text


# --- 5. Generic non-Clay server via mapping config ----------------------------------------------


@pytest.mark.asyncio
async def test_generic_non_clay_server_via_mapping(temp_data_dir: Path) -> None:
    generic_server = create_fake_generic_server("weather-server")
    executor = McpExecutor(data_dir=temp_data_dir)

    conn = ConnectionView(
        id="generic-weather-01",
        provider_id="weather-provider",
        auth_ref="token-store:generic-weather-01",
        meta={
            "server": generic_server,
            "tool_map": {
                "check_weather": {
                    "tool": "weather_service",
                    "params": {"city": "target_city"},
                    "result": {
                        "temperature_c": "temperature",
                        "weather": "conditions",
                        "location": "city",
                    },
                }
            },
        },
    )

    req = ExecRequest(
        request_id=uuid4(),
        capability="check_weather",
        params={"target_city": "Tokyo"},
        connection=conn,
    )

    result = await executor.execute(req)

    assert result.ok is True
    assert result.error_kind is None
    assert result.data is not None
    assert result.data["location"] == "Tokyo"
    assert result.data["temperature_c"] == 21
    assert result.data["weather"] == "Partly Cloudy"


# --- 6. Multiple calls and reconnect -------------------------------------------------------------


@pytest.mark.asyncio
async def test_executor_multiple_calls_and_reconnect(temp_data_dir: Path) -> None:
    server = create_fake_clay_server("clay-multi")
    executor = McpExecutor(data_dir=temp_data_dir)

    conn = ConnectionView(
        id="clay-multi",
        provider_id="clay",
        auth_ref="token-store:clay-multi",
        meta={"server": server},
    )

    # First call
    req1 = ExecRequest(
        request_id=uuid4(),
        capability="search-contacts",
        params={"query": "First"},
        connection=conn,
    )
    res1 = await executor.execute(req1)
    assert res1.ok is True

    # Second call
    req2 = ExecRequest(
        request_id=uuid4(),
        capability="search-contacts",
        params={"query": "Second"},
        connection=conn,
    )
    res2 = await executor.execute(req2)
    assert res2.ok is True

    assert len(server.state.recorded_calls) == 2  # type: ignore[attr-defined]


# --- 7. Protocol conformance & extra error / mapping tests ---------------------------------------


def test_executor_protocol_conformance() -> None:
    executor = McpExecutor()
    assert isinstance(executor, Executor)


@pytest.mark.asyncio
async def test_server_error_mapping(temp_data_dir: Path) -> None:
    server = create_fake_clay_server("clay-server-err")
    server.state.server_error = True  # type: ignore[attr-defined]
    executor = McpExecutor(data_dir=temp_data_dir)

    conn = ConnectionView(
        id="clay-server-err",
        provider_id="clay",
        auth_ref="token-store:clay-server-err",
        meta={"server": server},
    )

    req = ExecRequest(
        request_id=uuid4(),
        capability="search-contacts",
        params={"query": "Alice"},
        connection=conn,
    )

    result = await executor.execute(req)
    assert result.ok is False
    assert result.error_kind == ErrorKind.SERVER


@pytest.mark.asyncio
async def test_empty_not_found_error_mapping(temp_data_dir: Path) -> None:
    server = create_fake_clay_server("clay-empty")
    server.state.custom_error = "Contact not found: 404 No results"  # type: ignore[attr-defined]
    executor = McpExecutor(data_dir=temp_data_dir)

    conn = ConnectionView(
        id="clay-empty",
        provider_id="clay",
        auth_ref="token-store:clay-empty",
        meta={"server": server},
    )

    req = ExecRequest(
        request_id=uuid4(),
        capability="search-contacts",
        params={"query": "Ghost"},
        connection=conn,
    )

    result = await executor.execute(req)
    assert result.ok is False
    assert result.error_kind == ErrorKind.EMPTY
    assert result.found is False


@pytest.mark.asyncio
async def test_missing_server_url_bad_request(temp_data_dir: Path) -> None:
    executor = McpExecutor(data_dir=temp_data_dir)
    conn = ConnectionView(
        id="no-url-conn",
        provider_id="clay",
        auth_ref="token-store:no-url-conn",
        meta={},
    )
    req = ExecRequest(
        request_id=uuid4(),
        capability="search-contacts",
        params={},
        connection=conn,
    )
    result = await executor.execute(req)
    assert result.ok is False
    assert result.error_kind == ErrorKind.BAD_REQUEST
    assert "missing 'server_url'" in str(result.error)


def test_mapping_utilities() -> None:
    data = {
        "contacts": [
            {"id": "c1", "name": "Jane Doe", "info": {"email": "jane@example.com"}},
            {"id": "c2", "name": "John Doe", "info": {"email": "john@example.com"}},
        ],
        "meta": {"total": 2},
    }
    assert extract_value(data, "contacts[0].name") == "Jane Doe"
    assert extract_value(data, "contacts[1].info.email") == "john@example.com"
    assert extract_value(data, "meta.total") == 2
    assert extract_value(data, "nonexistent") is None

    # Param mapping with variable substitution and literals
    input_params = {"first": "Jane", "company": "Acme"}
    param_spec = {
        "query": "{first} Doe",
        "company_name": "company",
        "count": 10,
    }
    mapped = map_request_params(param_spec, input_params)
    assert mapped == {
        "query": "Jane Doe",
        "company_name": "Acme",
        "count": 10,
    }

    # Result mapping
    result_spec = {
        "first_email": "contacts[0].info.email",
        "total": "meta.total",
    }
    mapped_res = map_tool_result(result_spec, data)
    assert mapped_res == {
        "first_email": "jane@example.com",
        "total": 2,
    }

