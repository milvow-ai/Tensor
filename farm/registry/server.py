"""``farm registry for-server``: the PC's registry, rewritten for the Linux server.

The PC's registry points at Windows paths (``config_dir: D:/farm-data/ai/claude-02``, ``edit_roots:
["D:/"]``) and runs some accounts inside WSL (``cli_prefix: [wsl.exe, ...]``). On the server every account
runs natively as the user ``farm``, each with its own login directory under the data directory, so the
rewrite is mechanical:

* AI accounts: ``config_dir`` (Claude) and ``home`` (Codex, Antigravity, Gemini CLI) become
  ``<data dir>/ai/<connection id>``, and every account gets one: on Linux these CLIs keep their login under
  that directory (Antigravity without a D-Bus session writes ``$HOME/.gemini/...``), so no two accounts
  share a login. ``cli_prefix`` (WSL), Windows ``cli_path`` values and ``env`` entries that hold Windows paths
  are dropped; ``edit_roots`` with a Windows path become ``[<data dir>/workspaces]``.
* An MCP server that is started by a Windows path (stdio) cannot run there: its provider is disabled, its
  accounts are paused and the path is removed (``figma-bridge`` needs the Figma desktop app: it stays on the
  PC). Sync never deletes a provider, so leaving it out would leave it active on the server.
* Everything else (HTTP MCP servers, statuses, capabilities, budgets, settings) is copied unchanged.

The result is validated like any registry and holds no Windows path, or nothing is written.
"""

from __future__ import annotations

import copy
import re
from pathlib import PureWindowsPath
from typing import Any

import yaml

from farm.registry.loader import RegistryError, parse_registry

DEFAULT_SERVER_DATA_DIR = "/var/lib/harness-farm"

_WINDOWS_PATH = re.compile(r"^[A-Za-z]:[\\/]")
_HOME_KEY = {"claude": "config_dir", "codex": "home", "agy": "home", "gemini": "home", "gemini-cli": "home"}
"""Which ``meta`` key holds an account's own login directory, per CLI driver (Hermes uses a profile)."""


def is_windows_path(value: str) -> bool:
    """A drive path (``D:/x``, ``C:\\x``), anything with a backslash, or a Windows program (``wsl.exe``)."""
    return bool(_WINDOWS_PATH.match(value)) or "\\" in value or value.lower().endswith(".exe")


def windows_paths(node: Any, where: str = "") -> list[str]:
    """Where (``providers.claude.connections[0].meta.config_dir``) each Windows path in ``node`` is."""
    found: list[str] = []
    if isinstance(node, str):
        if is_windows_path(node):
            found.append(where or "<root>")
    elif isinstance(node, dict):
        for key, value in node.items():
            found += windows_paths(value, f"{where}.{key}" if where else str(key))
    elif isinstance(node, list):
        for index, value in enumerate(node):
            found += windows_paths(value, f"{where}[{index}]")
    return found


def _ai_account(provider_id: str, conn: dict[str, Any], data_dir: str, notes: list[str]) -> None:
    meta: dict[str, Any] = conn.setdefault("meta", {})
    conn_id = str(conn["id"])
    driver = str(meta.get("cli") or provider_id).lower()
    prefix = meta.get("cli_prefix")
    if prefix is not None:
        del meta["cli_prefix"]
        notes.append(f"{conn_id}: cli_prefix dropped (runs natively as user farm)")
    path = meta.get("cli_path")
    if isinstance(path, str) and is_windows_path(path):
        del meta["cli_path"]
        notes.append(f"{conn_id}: Windows cli_path dropped (found on PATH)")
    key = _HOME_KEY.get(driver)
    if key is not None:
        home = f"{data_dir}/ai/{conn_id}"
        for other in ("config_dir", "home"):
            value = meta.get(other)
            if other != key and isinstance(value, str) and is_windows_path(value):
                del meta[other]
        if meta.get(key) != home:
            meta[key] = home
            notes.append(f"{conn_id}: {key} -> {home}")
    roots = meta.get("edit_roots")
    if roots is not None:
        listed = roots if isinstance(roots, list) else [roots]
        if any(is_windows_path(str(root)) for root in listed):
            meta["edit_roots"] = [f"{data_dir}/workspaces"]
            notes.append(f"{conn_id}: edit_roots -> [{data_dir}/workspaces]")
    env = meta.get("env")
    if isinstance(env, dict):
        for name in [n for n, v in env.items() if isinstance(v, str) and is_windows_path(v)]:
            del env[name]
            notes.append(f"{conn_id}: env {name} dropped (a Windows path)")


def _stdio_mcp(provider_id: str, provider: dict[str, Any], notes: list[str]) -> None:
    mcp = provider.get("mcp")
    if not isinstance(mcp, dict) or mcp.get("transport", "stdio") != "stdio":
        return
    command = str(mcp.get("command") or "")
    args = [str(a) for a in mcp.get("args") or []]
    cwd = mcp.get("cwd")
    windows_cwd = bool(cwd) and is_windows_path(str(cwd))
    if not (is_windows_path(command) or any(map(is_windows_path, args)) or windows_cwd):
        return  # e.g. `npx -y some-mcp`: runs on Linux as it is
    if is_windows_path(command):
        mcp["command"] = PureWindowsPath(command).stem or command
    mcp["args"] = [a for a in args if not is_windows_path(a)]
    if windows_cwd:
        del mcp["cwd"]
    provider["enabled"] = False
    for conn in provider.get("connections") or []:
        conn["status"] = "paused"
    notes.append(f"{provider_id}: disabled and paused (a local stdio server started from a Windows path)")


def server_registry_data(
    data: dict[str, Any], *, data_dir: str = DEFAULT_SERVER_DATA_DIR
) -> tuple[dict[str, Any], list[str]]:
    """The server's registry (a copy of ``data``, rewritten as the module docstring says) and what changed."""
    data_dir = data_dir.rstrip("/")
    out = copy.deepcopy(data)
    notes: list[str] = []
    for provider_id, provider in (out.get("providers") or {}).items():
        if not isinstance(provider, dict):
            continue
        if provider.get("executor") == "cli_agent":
            for conn in provider.get("connections") or []:
                _ai_account(str(provider_id), conn, data_dir, notes)
        elif provider.get("executor") == "mcp":
            _stdio_mcp(str(provider_id), provider, notes)
    return out, notes


def server_registry_text(
    text: str, *, source: str = "<string>", data_dir: str = DEFAULT_SERVER_DATA_DIR
) -> tuple[str, list[str]]:
    """Rewrite registry YAML for the server; raises :class:`RegistryError` if either side is not valid."""
    parse_registry(text, source=source)  # the PC's registry must be valid before it is rewritten
    data = yaml.safe_load(text)
    out, notes = server_registry_data(data, data_dir=data_dir)
    left = windows_paths(out)
    if left:
        raise RegistryError(f"Windows paths left for the server in {source}: " + ", ".join(left))
    header = (
        f"# Harness Farm registry for the Linux server: generated from {PureWindowsPath(source).name} by\n"
        "# `farm registry for-server`. On the server it is <data dir>/registry.yaml (FARM_REGISTRY_PATH);\n"
        "# after the cutover that file is the source of truth. Secrets never live here.\n"
    )
    rendered = header + yaml.safe_dump(out, sort_keys=False, allow_unicode=True, width=110)
    parse_registry(rendered, source="server registry")
    return rendered, notes


__all__ = [
    "DEFAULT_SERVER_DATA_DIR",
    "is_windows_path",
    "server_registry_data",
    "server_registry_text",
    "windows_paths",
]
