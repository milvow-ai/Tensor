"""The Farm on the team's Linux server (SERVER1, docs/RUNBOOK-SERVER.md).

* Defaults that only fit the PC have a Linux counterpart (data dir), and a server keeps its secrets outside the
  read-only code checkout (``FARM_ENV_FILE``).
* ``farm registry for-server`` turns the PC's registry into the server's: no Windows path, no WSL prefix, one
  login home per AI account, local stdio servers (figma-bridge) disabled. A registry validates on either OS.
* The systemd units run the Farm as user ``farm`` from the paths the installer creates; the scripts they start
  are executable and LF in git.
* Behind Tailscale Serve the Farm answers for its tailnet name, and ``farm connect`` prints that URL; a login
  on a server prints its URL and takes a fixed callback port; ``farm db backup`` keeps the password out of argv.
* Linux only: the watchdog restarts a hung Farm and alerts once; a state backup restores byte for byte.
"""

from __future__ import annotations

import http.server
import json
import os
import re
import shutil
import subprocess
import sys
import threading
from collections.abc import Awaitable, Callable, Iterator
from pathlib import Path
from typing import Any

import httpx
import psycopg
import pytest
import respx
import yaml
from alembic import command
from fastmcp import Client
from fastmcp.client.auth import OAuth
from fastmcp.client.transports import StreamableHttpTransport
from psycopg.types.json import Jsonb
from typer.testing import CliRunner

from farm import settings
from farm.context import FarmContext
from farm.control import backup
from farm.control.cli import alembic_config
from farm.control.cli import app as cli_app
from farm.control.run import create_farm_app, create_token
from farm.db.pool import DbPool
from farm.executors.cli_agent.base import looks_secret
from farm.executors.mcp.client import McpExecutor
from farm.executors.mcp.connect import FarmOAuth, oauth_callback_port
from farm.registry import ConnectionSpec, Registry, RegistryError, parse_registry
from farm.registry.server import server_registry_text, windows_paths
from farm.secrets import set_secret
from farm.tenancy import MILVOW_TENANT_ID
from tests.farm_helpers import ScriptedExecutor, ok_result

type Make = Callable[..., Awaitable[FarmContext]]

ROOT = Path(__file__).resolve().parent.parent
UNITS = ROOT / "deploy" / "systemd"
LINUX = ROOT / "scripts" / "linux"
SERVER_DATA = "/var/lib/harness-farm"
linux_only = pytest.mark.skipif(not sys.platform.startswith("linux"), reason="runs the server's bash scripts")


# --- defaults and secrets ------------------------------------------------------------------------------------------


def test_the_default_data_dir_is_the_pcs_on_windows_and_xdg_elsewhere(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Linux used to get ``D:/farm-data``: a relative directory named ``D:`` in whatever the working dir was."""
    monkeypatch.delenv("FARM_DATA_DIR", raising=False)
    monkeypatch.delenv("XDG_DATA_HOME", raising=False)
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path / "home"))
    monkeypatch.setattr(sys, "platform", "win32")
    assert settings.data_dir_path() == Path("D:/farm-data")
    monkeypatch.setattr(sys, "platform", "linux")
    assert settings.data_dir_path() == tmp_path / "home" / ".local" / "share" / "harness-farm"
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "xdg"))
    assert settings.data_dir_path() == tmp_path / "xdg" / "harness-farm"
    assert McpExecutor()._data_dir == tmp_path / "xdg" / "harness-farm"  # no module keeps its own default
    monkeypatch.setenv("FARM_DATA_DIR", SERVER_DATA)
    assert settings.data_dir_path() == Path(SERVER_DATA)


def test_a_server_keeps_its_secrets_in_farm_env_file(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    secrets_file = tmp_path / "secrets.env"
    secrets_file.write_text("SERVER1_PROBE=from-the-server-file\n", encoding="utf-8")
    monkeypatch.setenv("SERVER1_PROBE", "unset-at-teardown")
    monkeypatch.delenv("SERVER1_PROBE")
    monkeypatch.setenv("FARM_ENV_FILE", str(secrets_file))
    assert settings.env_file() == secrets_file
    settings.load_env()
    assert os.environ["SERVER1_PROBE"] == "from-the-server-file"
    assert set_secret("SERVER1_ADDED", "added-by-set-secret") == secrets_file  # `farm set-secret` writes it too
    assert "SERVER1_ADDED=added-by-set-secret" in secrets_file.read_text(encoding="utf-8")
    monkeypatch.delenv("FARM_ENV_FILE")
    assert settings.env_file() == settings.DEFAULT_ENV_PATH


# --- the server's registry -----------------------------------------------------------------------------------------

PC_REGISTRY = r"""
settings:
  owner_email: owner@example.com
providers:
  claude:
    kind: ai
    executor: cli_agent
    connections:
      - id: claude-01
        auth_ref: "cli:claude-01"
        meta: {cli: claude, config_dir: "C:/Users/someone/.claude", models: [sonnet]}
      - id: claude-02
        auth_ref: "cli:claude-02"
        status: paused
        meta:
          cli: claude
          config_dir: "D:/farm-data/ai/claude-02"
          allow_edit: true
          edit_roots: ["D:/"]
          env: {CLAUDE_CODE_GIT_BASH_PATH: 'C:\Program Files\Git\bin\bash.exe', KEEP_ME: "1"}
  gemini:
    kind: ai
    executor: cli_agent
    connections:
      - id: agy-01
        auth_ref: "cli:agy-01"
        meta: {cli: agy, allow_edit: true, edit_roots: ["D:/"]}
      - id: agy-02
        auth_ref: "cli:agy-02"
        meta: {cli: agy, cli_prefix: [wsl.exe, -d, Ubuntu-24.04, -u, agy2, -e, env, -u, DBUS_SESSION_BUS_ADDRESS]}
      - id: gemini-cli-02
        auth_ref: "cli:gemini-cli-02"
        status: paused
        meta: {cli: gemini-cli, home: "D:/farm-data/ai/gemini-cli-02"}
  codex:
    kind: ai
    executor: cli_agent
    connections:
      - id: codex-01
        auth_ref: "cli:codex-01"
        meta: {cli: codex, cli_prefix: [wsl.exe, -d, Ubuntu-24.04, -u, codex1], cli_path: 'C:\tools\codex.cmd'}
  hermes:
    kind: ai
    executor: cli_agent
    connections:
      - id: hermes-01
        auth_ref: "cli:farm-agent"
        meta: {cli: hermes, profile: farm-agent}
  figma-bridge:
    kind: tool
    executor: mcp
    mcp: {transport: stdio, command: node, args: ['D:\figma mcp bridge\server\dist\index.js']}
    connections:
      - {id: figma-bridge-01, auth_ref: "cli:none"}
  local-tools:
    kind: tool
    executor: mcp
    mcp: {transport: stdio, command: npx, args: [-y, some-mcp]}
    connections:
      - {id: local-tools-01, auth_ref: "cli:none"}
  clay:
    kind: tool
    executor: mcp
    default_strategy: sticky
    mcp: {transport: http, url: "https://api.clay.com/v3/mcp", auth: oauth}
    connections:
      - {id: clay-milvow, auth_ref: "token-store:clay-milvow"}
capabilities:
  ask_ai:
    kind: ai
    routes: [claude, gemini, codex, hermes]
"""


def _connections(data: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {c["id"]: c for p in data["providers"].values() for c in p.get("connections") or []}


def test_the_pcs_registry_becomes_the_servers() -> None:
    text, notes = server_registry_text(PC_REGISTRY, source="config/registry.owner.yaml")
    data = yaml.safe_load(text)
    assert windows_paths(data) == []
    parse_registry(text)  # valid on this OS, whichever it is
    conns = _connections(data)

    homes = {
        "claude-01": ("config_dir", f"{SERVER_DATA}/ai/claude-01"),
        "claude-02": ("config_dir", f"{SERVER_DATA}/ai/claude-02"),
        "agy-01": ("home", f"{SERVER_DATA}/ai/agy-01"),  # was the PC's one global login
        "agy-02": ("home", f"{SERVER_DATA}/ai/agy-02"),  # was a WSL user
        "gemini-cli-02": ("home", f"{SERVER_DATA}/ai/gemini-cli-02"),
        "codex-01": ("home", f"{SERVER_DATA}/ai/codex-01"),
    }
    for conn_id, (key, path) in homes.items():
        assert conns[conn_id]["meta"][key] == path, conn_id
        assert "cli_prefix" not in conns[conn_id]["meta"] and "cli_path" not in conns[conn_id]["meta"]
    assert len({path for _, path in homes.values()}) == len(homes)  # no two accounts share a login
    assert conns["claude-02"]["meta"]["edit_roots"] == [f"{SERVER_DATA}/workspaces"]
    assert conns["claude-02"]["meta"]["env"] == {"KEEP_ME": "1"}
    assert conns["claude-01"]["meta"]["models"] == ["sonnet"]
    assert conns["hermes-01"]["meta"] == {"cli": "hermes", "profile": "farm-agent"}
    assert conns["claude-02"]["status"] == "paused" and "status" not in conns["agy-01"]  # statuses as on the PC

    figma = data["providers"]["figma-bridge"]
    assert figma["enabled"] is False and figma["mcp"]["args"] == [] and conns["figma-bridge-01"]["status"] == "paused"
    assert "enabled" not in data["providers"]["local-tools"] and "status" not in conns["local-tools-01"]
    assert data["providers"]["clay"] == yaml.safe_load(PC_REGISTRY)["providers"]["clay"]
    assert any(note.startswith("figma-bridge: disabled") for note in notes)
    assert text.startswith("# Harness Farm registry for the Linux server: generated from registry.owner.yaml")


def test_another_data_dir_and_a_path_the_rewrite_does_not_know_about() -> None:
    text, _ = server_registry_text(PC_REGISTRY, data_dir="/srv/farm/")
    assert _connections(yaml.safe_load(text))["agy-02"]["meta"]["home"] == "/srv/farm/ai/agy-02"
    unknown = PC_REGISTRY.replace("default_strategy: sticky", "default_strategy: sticky\n    config: {cache: 'D:/cache'}")
    with pytest.raises(RegistryError, match=r"providers\.clay\.config\.cache"):
        server_registry_text(unknown)


def test_farm_registry_for_server_writes_a_new_lf_file(tmp_path: Path) -> None:
    source, target = tmp_path / "registry.owner.yaml", tmp_path / "registry.server.yaml"
    source.write_text(PC_REGISTRY, encoding="utf-8")
    result = CliRunner().invoke(cli_app, ["registry", "for-server", str(source), str(target)])
    assert result.exit_code == 0, result.output
    assert "figma-bridge: disabled" in result.output and "codex-01: cli_prefix dropped" in result.output
    raw = target.read_bytes()
    assert b"\r" not in raw and not raw.startswith(b"\xef\xbb\xbf")
    assert source.read_text(encoding="utf-8") == PC_REGISTRY  # the PC's registry is not touched
    same = CliRunner().invoke(cli_app, ["registry", "for-server", str(source), str(source)])
    assert same.exit_code == 2


@pytest.mark.parametrize("root", ["D:/", "C:\\work", "/var/lib/harness-farm/workspaces"])
def test_edit_roots_absolute_on_either_os_are_valid_everywhere(root: str) -> None:
    """One registry (and one database) serves the Windows PC and the Linux server."""
    ConnectionSpec(id="x-01", auth_ref="cli:x-01", meta={"allow_edit": True, "edit_roots": [root]})


def test_a_relative_edit_root_is_still_refused() -> None:
    with pytest.raises(ValueError, match="absolute"):
        ConnectionSpec(id="x-01", auth_ref="cli:x-01", meta={"edit_roots": ["work/here"]})


# --- the systemd units and the files they start ---------------------------------------------------------------------


def _unit(name: str) -> dict[str, dict[str, list[str]]]:
    sections: dict[str, dict[str, list[str]]] = {}
    current: dict[str, list[str]] = {}
    for raw in (UNITS / name).read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith(("#", ";")):
            continue
        if line.startswith("["):
            current = sections.setdefault(line.strip("[]"), {})
            continue
        key, _, value = line.partition("=")
        current.setdefault(key.strip(), []).append(value.strip())
    return sections


def _starts_something_real(server_path: str) -> bool:
    """A script of this repo, or a console script ``uv sync`` puts in the server's venv."""
    assert server_path.startswith("/opt/harness-farm/"), server_path
    if server_path.startswith("/opt/harness-farm/.venv/bin/"):
        name = server_path.rsplit("/", 1)[1]
        return re.search(rf"(?m)^{re.escape(name)} = ", (ROOT / "pyproject.toml").read_text(encoding="utf-8")) is not None
    return (ROOT / server_path.removeprefix("/opt/harness-farm/")).is_file()


def test_the_farm_runs_as_user_farm_and_is_always_restarted() -> None:
    service = _unit("harness-farm.service")["Service"]
    assert service["User"] == ["farm"] and service["Group"] == ["farm"]
    assert service["ExecStart"] == ["/opt/harness-farm/.venv/bin/farm run"]
    assert service["Restart"] == ["always"]
    assert service["EnvironmentFile"] == ["/etc/harness-farm/farm.env"]
    assert service["ReadWritePaths"] == [SERVER_DATA]
    assert service["ProtectSystem"] == ["strict"] and service["NoNewPrivileges"] == ["true"]
    assert int(service["TimeoutStopSec"][0]) > 30  # longer than farm run's 30 s drain
    bifrost = _unit("harness-farm-bifrost.service")["Service"]
    assert bifrost["User"] == ["farm"] and bifrost["ReadWritePaths"] == [SERVER_DATA]


def test_every_unit_starts_a_file_of_this_repo_and_every_timer_names_its_unit() -> None:
    for unit in sorted(UNITS.glob("*.service")):
        start = _unit(unit.name)["Service"]["ExecStart"][0].split()[0]
        assert _starts_something_real(start), f"{unit.name}: {start}"
    for timer in sorted(UNITS.glob("*.timer")):
        target = _unit(timer.name)["Timer"]["Unit"][0]
        assert (UNITS / target).is_file(), f"{timer.name}: {target}"
    watchdog = (LINUX / "farm-watchdog.sh").read_text(encoding="utf-8")
    for unit_name in re.findall(r'_UNIT="\$\{[A-Z_]+_UNIT:-([a-z-]+)\}"', watchdog):
        assert (UNITS / f"{unit_name}.service").is_file(), unit_name
    assert "runuser -u" in watchdog  # the alert is sent as the Farm's user, never as root


def test_the_settings_file_has_no_secrets_and_keeps_the_farm_on_loopback() -> None:
    settings_lines = (ROOT / "deploy" / "farm.env.example").read_text(encoding="utf-8").splitlines()
    values = dict(line.split("=", 1) for line in settings_lines if line and not line.startswith("#"))
    assert values["FARM_HTTP_HOST"] == "127.0.0.1"  # Tailscale Serve publishes it; 8787 is never public
    assert values["FARM_DATA_DIR"] == SERVER_DATA
    assert values["FARM_ENV_FILE"].startswith(SERVER_DATA + "/") and values["FARM_REGISTRY_PATH"].startswith(SERVER_DATA + "/")
    assert [name for name, value in values.items() if looks_secret(name, value)] == []
    # EnvironmentFile's PATH replaces systemd's default for every unit: losing sbin broke the watchdog's alert
    # (runuser: command not found, found by the WSL end-to-end run).
    systemd_default = ["/usr/local/sbin", "/usr/local/bin", "/usr/sbin", "/usr/bin", "/sbin", "/bin"]
    assert all(part in values["PATH"].split(":") for part in systemd_default), values["PATH"]
    assert re.search(r"^PATH=.*/usr/sbin", (LINUX / "farm-watchdog.sh").read_text(encoding="utf-8"), re.M)


def _git(*args: str) -> str:
    return subprocess.run(["git", *args], cwd=ROOT, capture_output=True, text=True, check=True).stdout


@pytest.mark.skipif(shutil.which("git") is None or not (ROOT / ".git").exists(), reason="needs the git checkout")
def test_the_server_files_are_lf_and_the_scripts_executable_in_git() -> None:
    """A CRLF script fails as 'bad interpreter'; a 0644 one as status=203/EXEC (git clone keeps the index mode)."""
    listed = _git("ls-files", "--eol", "--", "scripts/linux", "deploy").splitlines()
    assert listed, "no server files in the index"
    for line in listed:  # "i/lf    w/lf    attr/text eol=lf      \t<path>"
        info, _, path = line.partition("\t")
        assert info.split()[0] == "i/lf" and "eol=lf" in info, path
    modes = {}
    for row in _git("ls-files", "-s", "--", "scripts/linux").splitlines():  # "<mode> <sha> <stage>\t<path>"
        info, _, path = row.partition("\t")
        modes[path] = info.split()[0]
    scripts = [path for path in modes if path.endswith(".sh") or path == "scripts/linux/farm"]
    assert scripts and all(modes[path] == "100755" for path in scripts), modes


# --- clients over the tailnet ---------------------------------------------------------------------------------------


def _bound_to_loopback(app: Any) -> Any:
    """What uvicorn on 127.0.0.1 gives the app: the server address is loopback, the Host header the tailnet name."""

    async def wrapped(scope: dict[str, Any], receive: Any, send: Any) -> None:
        if scope["type"] in ("http", "websocket"):
            scope = {**scope, "server": ("127.0.0.1", 8787)}
        await app(scope, receive, send)

    return wrapped


async def test_behind_tailscale_serve_the_farm_answers_for_its_tailnet_name(
    pool: DbPool, farm_factory: Make, registry: Registry
) -> None:
    """Tailscale Serve passes ``Host: harness-farm.<tailnet>.ts.net`` to the Farm on 127.0.0.1; a Host check
    (FastMCP's DNS-rebinding guard, off by default today) would answer 421 to every member."""
    token = await create_token(pool, "alice")
    ctx = await farm_factory(registry, executors={"api": ScriptedExecutor(lambda req: ok_result())})
    farm_app = await create_farm_app(ctx)
    app = _bound_to_loopback(farm_app)
    url = "https://harness-farm.tail1234.ts.net"

    def factory(headers: dict[str, str] | None = None, **kwargs: Any) -> httpx.AsyncClient:
        return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), headers=headers, **kwargs)

    base = getattr(farm_app, "app", farm_app)
    async with base.router.lifespan_context(base):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url=url) as http:
            assert (await http.get("/health")).status_code == 200
            assert (await http.post("/mcp", json={})).status_code == 401  # no token: the Farm's own check
        transport = StreamableHttpTransport(
            f"{url}/mcp", headers={"Authorization": f"Bearer {token}"}, httpx_client_factory=factory
        )
        async with Client(transport) as client:
            assert "verify_email" in {tool.name for tool in await client.list_tools()}


def test_farm_connect_prints_the_servers_tailnet_url(monkeypatch: pytest.MonkeyPatch) -> None:
    url = "https://harness-farm.tail1234.ts.net/mcp"
    monkeypatch.delenv("FARM_PUBLIC_URL", raising=False)
    result = CliRunner().invoke(cli_app, ["connect", "claude-code", "--url", url])
    assert result.exit_code == 0, result.output
    assert f"claude mcp add --transport http harness-farm {url}" in result.output
    assert "${FARM_TOKEN}" in result.output and "127.0.0.1" not in result.output
    monkeypatch.setenv("FARM_PUBLIC_URL", url)
    assert url in CliRunner().invoke(cli_app, ["connect", "claude-code"]).output
    assert CliRunner().invoke(cli_app, ["connect", "claude-code", "--url", "harness-farm:8787"]).exit_code == 2


async def test_a_login_on_a_server_prints_its_url_and_takes_a_fixed_callback_port(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("FARM_OAUTH_CALLBACK_PORT", "8765")
    oauth = FarmOAuth(
        mcp_url="https://mcp.example.com/mcp",
        client_name="Harness Farm (clay-milvow)",
        connection_id="clay-milvow",
        callback_port=oauth_callback_port(),
    )
    assert oauth.redirect_port == 8765  # `ssh -L 8765:localhost:8765` carries the redirect to the server
    opened: list[str] = []

    async def browser(self: OAuth, authorization_url: str) -> None:
        opened.append(authorization_url)

    monkeypatch.setattr(OAuth, "redirect_handler", browser)  # FastMCP's pre-flight + webbrowser.open
    await oauth.redirect_handler("https://auth.example.com/authorize?client_id=x")
    assert "https://auth.example.com/authorize?client_id=x" in capsys.readouterr().err
    assert opened == ["https://auth.example.com/authorize?client_id=x"]
    for wrong in ("80", "port", "70000"):
        monkeypatch.setenv("FARM_OAUTH_CALLBACK_PORT", wrong)
        with pytest.raises(ValueError, match="FARM_OAUTH_CALLBACK_PORT"):
            oauth_callback_port()


def test_on_a_server_codex_logs_in_with_a_device_code_and_agy_without_a_session_bus(
    scratch_db: Callable[[], str], monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    url = scratch_db()
    command.upgrade(alembic_config(url), "head")
    monkeypatch.setenv("FARM_DB_URL", url)
    monkeypatch.setenv("DBUS_SESSION_BUS_ADDRESS", "unix:path=/run/user/999/bus")
    codex_home, agy_home = tmp_path / "ai" / "codex-01", tmp_path / "ai" / "agy-02"
    with psycopg.connect(url, autocommit=True) as conn:
        conn.execute(
            "insert into public.providers (id, name, kind, executor) values "
            "('gemini', 'Gemini', 'ai', 'cli_agent'), ('codex', 'Codex', 'ai', 'cli_agent')"
        )
        for conn_id, provider, meta in (
            ("codex-01", "codex", {"cli": "codex", "home": str(codex_home)}),
            ("agy-02", "gemini", {"cli": "agy", "home": str(agy_home)}),
        ):
            conn.execute(
                "insert into public.connections (id, provider_id, auth_ref, meta) values (%s, %s, %s, %s)",
                (conn_id, provider, f"cli:{conn_id}", Jsonb(meta)),
            )
    launched: list[tuple[list[str], dict[str, str]]] = []
    monkeypatch.setattr("subprocess.run", lambda argv, **kw: launched.append((argv, kw["env"])))
    monkeypatch.setattr("farm.control.cli._headless", lambda: True)

    outputs = [CliRunner().invoke(cli_app, ["ai", "login", conn_id]) for conn_id in ("codex-01", "agy-02")]
    assert [o.exit_code for o in outputs] == [0, 0], [o.output for o in outputs]
    (codex_argv, codex_env), (agy_argv, agy_env) = launched
    assert codex_argv == ["codex", "login", "--device-auth"] and codex_env["CODEX_HOME"] == str(codex_home)
    assert (codex_home / "config.toml").read_text(encoding="utf-8") == 'cli_auth_credentials_store = "file"\n'
    assert agy_argv == ["agy"] and agy_env["HOME"] == str(agy_home) and "DBUS_SESSION_BUS_ADDRESS" not in agy_env


def test_farm_db_backup_keeps_the_password_off_the_command_line(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    password = "not-a-real-password-123"
    url = "postgresql://farm_owner:" + password + "@db.example.com:5432/postgres?sslmode=require"  # (secret scan)
    seen: list[tuple[list[str], dict[str, str]]] = []

    def pg_dump(cmd: list[str], **kw: Any) -> subprocess.CompletedProcess[str]:
        seen.append((cmd, kw["env"]))
        Path(cmd[cmd.index("-f") + 1]).write_bytes(b"PGDMP")
        return subprocess.CompletedProcess(cmd, 0, "", "")

    monkeypatch.setattr(backup, "find_pg_dump", lambda *a, **k: Path("pg_dump"))
    monkeypatch.setattr(backup.subprocess, "run", pg_dump)
    target = backup.create_backup(db_url=url, backup_directory=tmp_path / "backups")
    assert target.read_bytes() == b"PGDMP"
    ((cmd, env),) = seen
    assert not any(password in part for part in cmd)
    assert env["PGPASSWORD"] == password
    dbname = cmd[cmd.index("--dbname") + 1]
    assert "host=db.example.com" in dbname and "sslmode=require" in dbname and "user=farm_owner" in dbname


# --- Linux only: the watchdog and the state backup ------------------------------------------------------------------


@pytest.fixture
def fake_tools(tmp_path: Path) -> Iterator[tuple[dict[str, str], Path]]:
    """``systemctl`` and ``farm`` stand-ins first on PATH: they record what the scripts asked for."""
    bin_dir, calls = tmp_path / "bin", tmp_path / "calls.txt"
    bin_dir.mkdir()
    for name in ("systemctl", "farm"):
        (bin_dir / name).write_text(f'#!/bin/sh\necho "{name} $*" >> "{calls}"\n[ "$1" != is-enabled ]\n', encoding="utf-8")
        (bin_dir / name).chmod(0o755)
    env = {
        **os.environ,
        "PATH": f"{bin_dir}{os.pathsep}{os.environ['PATH']}",
        "FARM_DATA_DIR": str(tmp_path / "data"),
        "FARM_BIN": str(bin_dir / "farm"),
        "CHECK_INTERVAL_SECONDS": "60",
        "UNHEALTHY_THRESHOLD_SECONDS": "120",
    }
    yield env, calls


def _free_port() -> int:
    import socket

    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


def _watchdog(env: dict[str, str]) -> str:
    done = subprocess.run(["bash", str(LINUX / "farm-watchdog.sh")], env=env, capture_output=True, text=True, timeout=60)
    assert done.returncode == 0, done.stderr
    return done.stdout


@linux_only
def test_the_watchdog_restarts_a_farm_that_stopped_answering_and_alerts_once(
    fake_tools: tuple[dict[str, str], Path],
) -> None:
    env, calls = fake_tools
    env["FARM_HTTP_PORT"] = str(_free_port())  # nothing listens: /health fails
    first = _watchdog(env)
    assert "Farm unhealthy (60s / 120s)" in first and not calls.read_text().count("restart")
    second = _watchdog(env)  # the unhealthy time was kept between the timer's runs
    assert "Farm unhealthy (120s / 120s)" in second and "Restarting harness-farm..." in second
    recorded = calls.read_text().splitlines()
    assert recorded.count("systemctl restart harness-farm") == 1
    assert [line for line in recorded if line.startswith("farm alert send --kind farm_down")] != []
    assert not any("restart harness-farm-bifrost" in line for line in recorded)  # its unit is not enabled
    assert "farm_unhealthy=0" in (Path(env["FARM_DATA_DIR"]) / "watchdog" / "state").read_text()


@linux_only
def test_the_watchdog_leaves_a_healthy_farm_alone(fake_tools: tuple[dict[str, str], Path]) -> None:
    env, calls = fake_tools

    class Health(http.server.BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            self.send_response(200 if self.path == "/health" else 404)
            self.end_headers()

        def log_message(self, *args: Any) -> None:
            pass

    server = http.server.HTTPServer(("127.0.0.1", 0), Health)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        env["FARM_HTTP_PORT"] = str(server.server_address[1])
        state = Path(env["FARM_DATA_DIR"]) / "watchdog" / "state"
        state.parent.mkdir(parents=True)
        state.write_text("farm_unhealthy=60\nbifrost_unhealthy=0\n")
        out = _watchdog(env)
    finally:
        server.shutdown()
    assert "Farm recovered and is now healthy." in out
    assert "restart" not in calls.read_text() and "alert" not in calls.read_text()
    assert "farm_unhealthy=0" in state.read_text()


@linux_only
@pytest.mark.skipif(shutil.which("age") is None or shutil.which("age-keygen") is None, reason="needs age")
def test_a_state_backup_restores_byte_for_byte_and_leaves_caches_out(tmp_path: Path) -> None:
    data, backups = tmp_path / "data", tmp_path / "backups"
    files = {
        "secrets.env": "SUPABASE_DB_URL=x\n",
        "registry.yaml": "providers: {}\n",
        "ai/claude-02/.credentials.json": '{"token": "t"}',
        "tokens/clay-milvow/oauth.json": '{"refresh": "r"}',
        ".hermes/profiles/farm-agent/config.yaml": "model: x\n",
    }
    skipped = ["logs/watchdog.log", "workspaces/repo/main.py", ".local/bin/claude", "ai/agy-02/.cache/blob", "pg/PG_VERSION"]
    for rel in [*files, *skipped]:
        (data / rel).parent.mkdir(parents=True, exist_ok=True)
        (data / rel).write_text(files.get(rel, "skip me"), encoding="utf-8")
    key = tmp_path / "owner.key"
    subprocess.run(["age-keygen", "-o", str(key)], check=True, capture_output=True)
    public = next(line.split(": ")[1] for line in key.read_text().splitlines() if line.startswith("# public key: "))
    (tmp_path / "recipients.txt").write_text(public + "\n")
    env = {
        **os.environ,
        "FARM_DATA_DIR": str(data),
        "FARM_BACKUP_DIR": str(backups),
        "FARM_BACKUP_RECIPIENTS": str(tmp_path / "recipients.txt"),
    }

    made = subprocess.run(["bash", str(LINUX / "backup-state.sh")], env=env, capture_output=True, text=True, timeout=60)
    assert made.returncode == 0, made.stderr
    (archive,) = backups.glob("farm-state-*.tar.gz.age")
    assert b"SUPABASE_DB_URL" not in archive.read_bytes()  # encrypted, not just compressed

    restored = tmp_path / "restored"
    run = subprocess.run(
        ["bash", str(LINUX / "restore-state.sh"), str(archive), "--identity", str(key), "--to", str(restored)],
        env=env, capture_output=True, text=True, timeout=60,
    )
    assert run.returncode == 0, run.stderr
    for rel, content in files.items():
        assert (restored / rel).read_text(encoding="utf-8") == content
    assert not any((restored / rel).exists() for rel in skipped)
    drill = subprocess.run(
        ["bash", str(LINUX / "restore-state.sh"), "--drill", "--identity", str(key)],
        env=env, capture_output=True, text=True, timeout=60,
    )
    assert drill.returncode == 0 and "restore drill passed" in drill.stdout, drill.stderr

    damaged = bytearray(archive.read_bytes())
    damaged[len(damaged) // 2] ^= 0xFF
    archive.write_bytes(bytes(damaged))
    broken = subprocess.run(
        ["bash", str(LINUX / "restore-state.sh"), "--drill", "--identity", str(key)],
        env=env, capture_output=True, text=True, timeout=60,
    )
    assert broken.returncode != 0 and "damaged" in broken.stderr


# --- team access ----------------------------------------------------------------------------------------------------

SUPABASE = "https://example-project.supabase.co"


def test_a_member_gets_a_sign_in_and_a_membership_and_the_key_is_never_shown(
    scratch_db: Callable[[], str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """The Console signs in only existing users (shouldCreateUser: false) and RLS shows a tenant's rows only to
    its members (F1B): ``farm member add`` makes both, and reuses a sign-in that already exists."""
    url = scratch_db()
    command.upgrade(alembic_config(url), "head")
    monkeypatch.setenv("FARM_DB_URL", url)
    key = "service-role-" + "x" * 24
    monkeypatch.setenv("SUPABASE_URL", SUPABASE)
    monkeypatch.setenv("SUPABASE_SERVICE_ROLE_KEY", key)
    alice = "5b3c1e9a-0000-4000-8000-00000000a11c"
    with respx.mock(assert_all_called=False) as mock:
        create = mock.post(f"{SUPABASE}/auth/v1/admin/users").mock(
            side_effect=[
                httpx.Response(201, json={"id": alice, "email": "alice@example.com"}),
                httpx.Response(422, json={"msg": "A user with this email address has already been registered"}),
            ]
        )
        mock.get(f"{SUPABASE}/auth/v1/admin/users").mock(
            return_value=httpx.Response(
                200, json={"users": [{"id": "1d2e", "email": "bob@example.com"}, {"id": alice, "email": "Alice@Example.com"}]}
            )
        )
        first = CliRunner().invoke(cli_app, ["member", "add", "alice@example.com", "--name", "Alice"])
        again = CliRunner().invoke(cli_app, ["member", "add", "alice@example.com", "--role", "admin"])
    assert first.exit_code == 0 and "sign-in created" in first.output, first.output
    assert again.exit_code == 0 and "sign-in existed" in again.output, again.output
    assert json.loads(create.calls[0].request.content) == {"email": "alice@example.com", "email_confirm": True}
    assert create.calls[0].request.headers["apikey"] == key and key not in first.output + again.output
    with psycopg.connect(url) as conn:
        rows = conn.execute(
            "select u.email, u.auth_subject, u.display_name, m.tenant_id, m.role "
            "from public.users u join public.memberships m on m.user_id = u.id"
        ).fetchall()
    assert rows == [("alice@example.com", alice, "Alice", MILVOW_TENANT_ID, "admin")]

    assert "alice@example.com  admin  sign-in linked" in CliRunner().invoke(cli_app, ["member", "list"]).output
    assert CliRunner().invoke(cli_app, ["member", "remove", "alice@example.com"]).exit_code == 0
    assert CliRunner().invoke(cli_app, ["member", "remove", "alice@example.com"]).exit_code == 1
    assert "no members yet" in CliRunner().invoke(cli_app, ["member", "list"]).output
    assert CliRunner().invoke(cli_app, ["member", "add", "not-an-email"]).exit_code == 2
    assert CliRunner().invoke(cli_app, ["member", "add", "bob@example.com", "--role", "boss"]).exit_code == 2
