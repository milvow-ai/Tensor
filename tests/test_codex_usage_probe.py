import asyncio
import sys
from pathlib import Path

import pytest

import farm.manager.codex_usage as codex_usage
from farm.manager.codex_usage import _sanitize_rate, probe_codex


def test_sanitizes_only_numeric_rate_fields_and_buckets() -> None:
    got = _sanitize_rate(
        {
            "primary": {"usedPercent": 23, "windowDurationMins": 300, "resetsAt": 123456, "label": "leak"},
            "rateLimitsByLimitId": {
                "model-a": {"primary": {"usedPercent": 8, "token": "secret"}},
                "model-b": {"secondary": {"usedPercent": 9}},
            },
        }
    )
    assert got == {
        "primary": {"usedPercent": 23, "windowDurationMins": 300, "resetsAt": 123456},
        "rateLimitsByLimitId": {
            "model-a": {"primary": {"usedPercent": 8}},
            "model-b": {"secondary": {"usedPercent": 9}},
        },
    }


def test_unconfigured_account_never_uses_owner_home() -> None:
    result = asyncio.run(probe_codex({"cli_path": "missing-codex"}))
    assert result == {"status": "unconfigured", "error_code": "account_home_missing"}


@pytest.mark.parametrize(
    "mode",
    [
        "success",
        "rate_error",
        "eof",
        "timeout",
        "stdout_flood",
        "stderr_flood",
        "usage_unsupported",
        "usage_unknown",
        "usage_missing_buckets",
        "usage_null_buckets",
    ],
)
def test_fake_app_server_probe(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, mode: str) -> None:
    home = tmp_path / "existing-home"
    home.mkdir()
    script = tmp_path / "fake_server.py"
    script.write_text(
        """import json, sys, time
mode = MODE
for line in sys.stdin:
    request = json.loads(line)
    method = request.get("method")
    if method == "initialize":
        print(json.dumps({"id": 1, "result": {}}), flush=True)
    elif method == "account/rateLimits/read":
        if mode == "eof": break
        if mode == "timeout": time.sleep(30); break
        if mode == "stdout_flood": print("x" * 300000, flush=True); break
        if mode == "stderr_flood": sys.stderr.write("x" * 300000); sys.stderr.flush(); time.sleep(30); break
        if mode == "rate_error":
            print(json.dumps({"id": 2, "error": {"code": -1}}), flush=True)
        else:
            rate = {"id": 2, "result": {"rateLimits": {"primary": {"usedPercent": 17}}}}
            print(json.dumps(rate), flush=True)
            if mode == "success":
                print(json.dumps({"id": 3, "result": {"dailyUsageBuckets": [{"startDate": "2026-11-05", "tokens": 12}]}}), flush=True)
            elif mode == "usage_missing_buckets":
                print(json.dumps({"id": 3, "result": {}}), flush=True)
            elif mode == "usage_null_buckets":
                print(json.dumps({"id": 3, "result": {"dailyUsageBuckets": None}}), flush=True)
    elif method == "account/usage/read":
        code = -32601 if mode == "usage_unsupported" else -1
        if mode in ("usage_unsupported", "usage_unknown"):
            print(json.dumps({"id": 3, "error": {"code": code}}), flush=True)
""".replace("MODE", repr(mode)),
        encoding="utf-8",
    )
    monkeypatch.setattr(codex_usage, "_argv", lambda _meta: [sys.executable, str(script)])
    result = asyncio.run(probe_codex({"home": str(home)}, timeout=0.35))
    if mode == "success":
        assert result["status"] == "ok"
        assert result["rate_limits"]["primary"]["usedPercent"] == 17
        assert result["daily_usage_buckets"] == [{"startDate": "2026-11-05", "tokens": 12}]
    elif mode == "usage_unsupported":
        assert result["daily_usage_status"] == "unsupported"
    elif mode == "usage_unknown":
        assert result["daily_usage_status"] == "unknown"
    elif mode in ("usage_missing_buckets", "usage_null_buckets"):
        assert result["daily_usage_status"] == "unknown"
        assert "daily_usage_buckets" not in result
    elif mode == "timeout":
        assert result["error_code"] == "timeout"
    elif mode in ("stdout_flood", "stderr_flood"):
        assert result["error_code"] == "output_limit"
    elif mode == "eof":
        assert result["error_code"] == "protocol_io"
    else:
        assert result["error_code"] == "rate_limits_failed"


def test_cancellation_reaps_child(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    home = tmp_path / "home"
    home.mkdir()
    script = tmp_path / "sleep_server.py"
    script.write_text(
        """import json,sys,time
for line in sys.stdin:
 r=json.loads(line)
 if r.get("id")==1: print(json.dumps({"id":1,"result":{}}),flush=True)
 if r.get("method")=="account/rateLimits/read": time.sleep(30)
""",
        encoding="utf-8",
    )
    monkeypatch.setattr(codex_usage, "_argv", lambda _meta: [sys.executable, str(script)])

    async def scenario() -> None:
        task = asyncio.create_task(probe_codex({"home": str(home)}, timeout=10))
        await asyncio.sleep(0.1)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert not codex_usage._ACTIVE

    asyncio.run(scenario())


def test_missing_native_home_is_not_created(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    missing = tmp_path / "never-created"
    monkeypatch.setattr(codex_usage, "_argv", lambda _meta: [sys.executable, "unused"])
    result = asyncio.run(probe_codex({"home": str(missing)}))
    assert result["error_code"] == "account_home_invalid"
    assert not missing.exists()


def test_absent_rate_windows_remain_unknown() -> None:
    assert _sanitize_rate({}) == {}
    assert _sanitize_rate({"primary": None, "secondary": None}) == {}


@pytest.mark.skipif(sys.platform != "win32", reason="Windows WSL launcher")
def test_wsl_explicit_config_home_is_not_appended_twice(monkeypatch: pytest.MonkeyPatch) -> None:
    prefix = ["wsl.exe", "-d", "Ubuntu-24.04", "-u", "codex1", "-e", "env", "-u", "DBUS_SESSION_BUS_ADDRESS"]
    argv = codex_usage._argv({"cli_prefix": prefix, "home": "/home/codex1/.codex"})
    assert argv is not None
    assert "CODEX_HOME=/home/codex1/.codex" in argv
    assert "CODEX_HOME=/home/codex1/.codex/.codex" not in argv
    assert argv[-4:] == ["codex", "app-server", "--listen", "stdio://"]


@pytest.mark.skipif(sys.platform != "win32", reason="Windows WSL launcher")
def test_wsl_home_discovery_binds_named_account(monkeypatch: pytest.MonkeyPatch) -> None:
    import subprocess

    calls = []

    def discover(argv, **kwargs):
        calls.append(argv)
        return subprocess.CompletedProcess(argv, 0, b"/home/codex1\n")

    monkeypatch.setattr(codex_usage.subprocess, "run", discover)
    prefix = ["wsl.exe", "-d", "Ubuntu-24.04", "-u", "codex1", "-e", "env", "-u", "DBUS_SESSION_BUS_ADDRESS"]
    argv = codex_usage._argv({"cli_prefix": prefix})
    assert argv is not None and "CODEX_HOME=/home/codex1/.codex" in argv
    assert calls == [["wsl.exe", "-d", "Ubuntu-24.04", "-u", "codex1", "-e", "printenv", "HOME"]]
