"""``farm mcp import``: MCP servers from Claude Desktop, Claude Code, Codex or a file become providers.

Sources (every one is read, never changed):

* ``claude-desktop``  ``%APPDATA%\\Claude\\claude_desktop_config.json`` (``~/Library/Application
  Support/Claude/...`` on macOS, ``~/.config/Claude/...`` elsewhere): ``mcpServers``;
* ``claude-code``     ``~/.claude.json``: ``mcpServers`` and the ``mcpServers`` of every project;
* ``codex``           ``$CODEX_HOME/config.toml`` (``~/.codex/config.toml``): ``[mcp_servers.<name>]``;
* ``file:<path>``     a ``.toml`` in Codex's format, or JSON in the ``mcpServers`` format (Claude, Cursor).

Parsing of the standard format is FastMCP's (``fastmcp.mcp_config``); the Codex reader and the secret
handling are the Farm's. Each server becomes one provider (``executor: mcp``) with one connection
``<provider>-01``, and:

* **no secret value ever reaches the registry, the output or a log**: every literal ``env`` value and
  header value (and a URL that carries a credential) is written to the local ``.env`` as
  ``FARM_MCP_<PROVIDER>_<NAME>`` through ``farm.secrets.set_secret`` (the code path of ``farm set-secret``)
  and the registry keeps only the ``env:`` reference. ``${NAME}`` in the source becomes ``env:NAME``
  (nothing to move: the variable must exist in ``.env``). A credential in ``args`` cannot be moved: that
  server is skipped and says so;
* a remote server with no credentials of its own is assumed to use OAuth (``farm mcp login <connection>``);
* the registry file is edited in place: its comments and layout stay, the new providers are added at the end
  of ``providers:``, the result is validated before anything is written, and the file is replaced atomically;
* a provider that already exists is skipped, and reported;
* ``--dry-run`` reports server names, transports and the names of the variables, and writes nothing.
"""

from __future__ import annotations

import json
import os
import re
import sys
import tomllib
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import yaml
from fastmcp.mcp_config import RemoteMCPServer, StdioMCPServer, infer_transport_type_from_url
from pydantic import ValidationError

from farm.registry import RegistryError, load_registry, parse_registry
from farm.registry.models import McpProviderSpec, ProviderSpec, looks_like_secret
from farm.secrets import set_secret

SOURCES = ("claude-desktop", "claude-code", "codex")
SECRET_PREFIX = "FARM_MCP_"

_SLUG_UNSAFE = re.compile(r"[^a-z0-9_-]+")
_ENV_UNSAFE = re.compile(r"[^A-Z0-9]+")
_REFERENCE = re.compile(r"^\$\{?([A-Za-z_][A-Za-z0-9_]*)\}?$")
_BEARER_REFERENCE = re.compile(r"^Bearer\s+\$\{?([A-Za-z_][A-Za-z0-9_]*)\}?$", re.IGNORECASE)
_BEARER_LITERAL = re.compile(r"^Bearer\s+(\S.*)$", re.IGNORECASE)
_SAFE_PASSTHROUGH = frozenset({"PATH", "HOME", "USER", "USERNAME", "TEMP", "TMP", "LANG", "SHELL", "TERM"})


class ImportFailed(ValueError):
    """The source cannot be read, or the registry cannot take the import. Names the file, never a value."""


@dataclass(frozen=True)
class ImportedServer:
    name: str
    """The server's name in the source."""
    provider_id: str
    transport: str
    secrets: tuple[str, ...]
    """Names of the ``.env`` variables the Farm wrote (or, in a dry run, would write) for this server."""
    references: tuple[str, ...]
    """Names of existing variables the server needs in ``.env`` (``${NAME}`` or ``bearer_token_env_var``)."""
    notes: tuple[str, ...] = ()


@dataclass(frozen=True)
class SkippedServer:
    name: str
    reason: str


@dataclass
class ImportReport:
    source: str
    dry_run: bool
    imported: list[ImportedServer] = field(default_factory=list)
    skipped: list[SkippedServer] = field(default_factory=list)


@dataclass
class _Plan:
    """One server, ready to be written: its provider block and the secrets that go to ``.env``."""

    server: ImportedServer
    block: dict[str, Any]
    secrets: dict[str, str] = field(default_factory=dict)
    """``.env`` name -> value. Lives only in memory, only until :func:`import_servers` has written it."""


# --- reading the sources ---------------------------------------------------------------------------------


def source_path(source: str, *, home: Path | None = None, environ: Mapping[str, str] | None = None) -> Path:
    """The file a ``--from`` value names."""
    home = home or Path.home()
    env = os.environ if environ is None else environ
    if source == "claude-desktop":
        if sys.platform == "win32":
            return (
                Path(env.get("APPDATA") or home / "AppData" / "Roaming")
                / "Claude"
                / "claude_desktop_config.json"
            )
        if sys.platform == "darwin":
            return home / "Library" / "Application Support" / "Claude" / "claude_desktop_config.json"
        return home / ".config" / "Claude" / "claude_desktop_config.json"
    if source == "claude-code":
        return home / ".claude.json"
    if source == "codex":
        return Path(env.get("CODEX_HOME") or home / ".codex") / "config.toml"
    if source.startswith("file:") and source[5:].strip():
        return Path(source[5:].strip())
    raise ImportFailed(f"unknown source '{source}': use claude-desktop, claude-code, codex or file:<path>")


def _servers_in_json(data: Any) -> dict[str, dict[str, Any]]:
    """``mcpServers`` (and ``mcp_servers``) at the top and in every project; the first of a name wins."""
    found: dict[str, dict[str, Any]] = {}

    def take(block: Any) -> None:
        if isinstance(block, dict):
            for name, definition in block.items():
                if isinstance(definition, dict):
                    found.setdefault(str(name), definition)

    if not isinstance(data, dict):
        raise ImportFailed("the file is not a JSON object")
    take(data.get("mcpServers"))
    take(data.get("mcp_servers"))
    projects = data.get("projects")
    if isinstance(projects, dict):
        for project in projects.values():
            if isinstance(project, dict):
                take(project.get("mcpServers"))
    if not found and any(isinstance(v, dict) and ("command" in v or "url" in v) for v in data.values()):
        take({k: v for k, v in data.items() if isinstance(v, dict) and ("command" in v or "url" in v)})
    return found


def read_servers(path: Path) -> dict[str, dict[str, Any]]:
    """The raw server definitions of a configuration file, by name."""
    try:
        text = path.read_text(encoding="utf-8-sig")
    except OSError as exc:
        raise ImportFailed(f"cannot read {path}: {exc.strerror or type(exc).__name__}") from None
    try:
        if path.suffix.lower() == ".toml":
            table = tomllib.loads(text).get("mcp_servers", {})
            return {str(k): dict(v) for k, v in table.items() if isinstance(v, dict)}
        return _servers_in_json(json.loads(text))
    except (json.JSONDecodeError, tomllib.TOMLDecodeError) as exc:
        raise ImportFailed(
            f"{path} is not valid {'TOML' if path.suffix == '.toml' else 'JSON'}: {exc}"
        ) from None


# --- one server -> one provider block --------------------------------------------------------------------


def _slug(name: str) -> str:
    slug = _SLUG_UNSAFE.sub("-", name.strip().lower()).strip("-_")
    return (slug or "mcp")[:58]


def _env_name(provider_id: str, label: str, taken: set[str]) -> str:
    scope = _ENV_UNSAFE.sub("_", provider_id.upper()).strip("_")
    base = f"{SECRET_PREFIX}{scope}_{_ENV_UNSAFE.sub('_', label.upper()).strip('_')}"
    name, n = base, 1
    while name in taken:
        n += 1
        name = f"{base}_{n}"
    taken.add(name)
    return name


def _as_strings(value: Any) -> list[str]:
    return [str(item) for item in value] if isinstance(value, list) else []


def _url_carries_credential(url: str) -> bool:
    """A user:password, a key in the query, or a long random path segment (``/s/<token>/mcp``)."""
    parts = urlsplit(url)
    if parts.username is not None or parts.password is not None or looks_like_secret(url):
        return True
    return any(
        len(segment) >= 24
        and re.fullmatch(r"[A-Za-z0-9_-]+", segment) is not None
        and any(c.isdigit() for c in segment)
        and any(c.isalpha() for c in segment)
        for segment in parts.path.split("/")
    )


def _problems(exc: ValidationError) -> str:
    """The reasons of a validation error as one line (field paths and messages; never the values)."""
    return "; ".join(
        (f"{'.'.join(map(str, e['loc']))}: " if e["loc"] else "")
        + str(e["msg"]).removeprefix("Value error, ")
        for e in exc.errors(include_input=False, include_url=False)
    )


def _canonical(definition: Mapping[str, Any]) -> dict[str, Any]:
    """Claude / Codex spellings -> the ``mcpServers`` format FastMCP parses, plus the Codex-only extras."""
    d = dict(definition)
    kind = d.get("type")
    if kind in ("http", "streamable-http", "sse") and "transport" not in d:
        d["transport"] = kind
    if "http_headers" in d and "headers" not in d:
        d["headers"] = d.pop("http_headers")
    return d


def _plan_server(name: str, definition: Mapping[str, Any], provider_id: str) -> _Plan:
    """Raises ``ValueError`` (a reason without values) for a server that cannot be imported."""
    d = _canonical(definition)
    transport: str
    taken: set[str] = set()
    secrets: dict[str, str] = {}
    references: list[str] = []
    notes: list[str] = []
    mcp: dict[str, Any] = {}
    auth_ref = "cli:none"
    status: str | None = None
    connection_id = f"{provider_id}-01"

    def move_to_env(label: str, value: str) -> str:
        env_name = _env_name(provider_id, label, taken)
        secrets[env_name] = value
        return f"env:{env_name}"

    def reference_or_move(label: str, value: str) -> str:
        if re.fullmatch(r"env:[A-Za-z_][A-Za-z0-9_]*", value):
            references.append(value.removeprefix("env:"))
            return value
        match = _REFERENCE.match(value)
        if match:
            references.append(match.group(1))
            return f"env:{match.group(1)}"
        return move_to_env(label, value)

    if "command" in d:
        try:
            stdio = StdioMCPServer.model_validate(
                {k: v for k, v in d.items() if k in StdioMCPServer.model_fields}
            )
        except ValidationError:
            raise ValueError("not a valid stdio server definition") from None
        mcp = {"transport": "stdio", "command": stdio.command}
        if stdio.args:
            mcp["args"] = list(stdio.args)
            notes.extend(
                f"arg #{i} uses ${{...}}, which the Farm does not expand"
                for i, a in enumerate(stdio.args, 1)
                if "${" in a
            )
        if stdio.cwd:
            mcp["cwd"] = stdio.cwd
        env: dict[str, str] = {}
        for key, value in stdio.env.items():
            env[str(key)] = reference_or_move(str(key), str(value))
        for var in _as_strings(d.get("env_vars")):  # Codex: variables passed through from the environment
            if var not in _SAFE_PASSTHROUGH and var not in env:
                env[var] = f"env:{var}"
                references.append(var)
        if env:
            mcp["env"] = env
        transport = "stdio"
    elif "url" in d:
        try:
            remote = RemoteMCPServer.model_validate(
                {k: v for k, v in d.items() if k in RemoteMCPServer.model_fields}
            )
        except ValidationError:
            raise ValueError("not a valid http / sse server definition") from None
        transport = remote.transport or infer_transport_type_from_url(remote.url)
        if transport == "streamable-http":
            transport = "http"
        url = remote.url
        secret_url = _url_carries_credential(url)
        mcp = {"transport": transport, "url": move_to_env("URL", url) if secret_url else url}
        headers: dict[str, str] = {}
        auth = "none"
        for header, value in remote.headers.items():
            text = str(value)
            if header.lower() == "authorization":
                ref = _BEARER_REFERENCE.match(text)
                literal = _BEARER_LITERAL.match(text)
                if ref:
                    auth, auth_ref = "env", f"env:{ref.group(1)}"
                    references.append(ref.group(1))
                    continue
                if literal:
                    auth, auth_ref = "env", move_to_env("TOKEN", literal.group(1))
                    continue
            headers[str(header)] = reference_or_move(f"HEADER_{header}", text)
        for header, var in (d.get("env_http_headers") or {}).items():  # Codex: header -> variable name
            headers[str(header)] = f"env:{var}"
            references.append(str(var))
        if isinstance(remote.auth, str) and remote.auth != "oauth":  # FastMCP's bearer-token spelling
            auth, auth_ref = "env", move_to_env("TOKEN", remote.auth)
        elif remote.auth == "oauth":
            auth = "oauth"
        if d.get("bearer_token_env_var"):  # Codex
            auth, auth_ref = "env", f"env:{d['bearer_token_env_var']}"
            references.append(str(d["bearer_token_env_var"]))
        if auth == "none" and not headers and not secret_url and "?" not in url:
            auth = "oauth"  # no credential of its own: the usual OAuth connector (log in once)
        if auth == "oauth":
            auth_ref, status = f"token-store:{connection_id}", "needs_login"
            notes.append(f"log in with: farm mcp login {connection_id}")
        mcp["auth"] = auth
        if headers:
            mcp["headers"] = headers
    else:
        raise ValueError("neither a command nor a url: nothing to connect to")

    allow, deny = _as_strings(d.get("enabled_tools")), _as_strings(d.get("disabled_tools"))
    if allow or deny:
        mcp["tools"] = {**({"allow": allow} if allow else {}), **({"deny": deny} if deny else {})}
    if isinstance(d.get("tool_timeout_sec"), int | float) and d["tool_timeout_sec"] > 0:
        mcp["timeout_s"] = float(d["tool_timeout_sec"])

    connection: dict[str, Any] = {"id": connection_id, "auth_ref": auth_ref, "priority": 1}
    if status:
        connection["status"] = status
    block = {
        "name": name,
        "kind": "tool",
        "executor": "mcp",
        "default_strategy": "failover",
        "mcp": mcp,
        "connections": [connection],
    }
    try:
        McpProviderSpec.model_validate(mcp)
        ProviderSpec.model_validate(block)
    except ValidationError as exc:
        raise ValueError(_problems(exc)) from None
    server = ImportedServer(
        name=name,
        provider_id=provider_id,
        transport=transport,
        secrets=tuple(secrets),
        references=tuple(dict.fromkeys(references)),
        notes=tuple(notes),
    )
    return _Plan(server, block, secrets)


# --- the registry file -----------------------------------------------------------------------------------


def render_provider(provider_id: str, block: Mapping[str, Any], source: str) -> list[str]:
    """The provider as YAML lines (indented for ``providers:``), with a line saying where it came from."""
    body = yaml.safe_dump({provider_id: dict(block)}, sort_keys=False, allow_unicode=True, width=100)
    return [f"  # imported by `farm mcp import` from {source}", *(f"  {line}" for line in body.splitlines())]


def insert_providers(text: str, rendered: Sequence[Sequence[str]]) -> str:
    """``text`` with the rendered providers added at the end of its ``providers:`` mapping.

    Nothing already in the file is touched: comments, order and line endings stay.
    """
    if not rendered:
        return text
    eol = "\r" if "\r\n" in text else ""
    lines = text.split("\n")
    start = next(
        (i for i, line in enumerate(lines) if re.match(r"^providers:\s*(\{\s*\}\s*)?(#.*)?\r?$", line)), None
    )
    if start is None:
        raise ImportFailed("the registry has no `providers:` section to add the servers to")
    lines[start] = (
        re.sub(r"\{\s*\}", "", lines[start], count=1).rstrip() + eol
    )  # `providers: {}` -> `providers:`
    added = [f"{line}{eol}" for chunk in rendered for line in ("", *chunk)]
    end = next((i for i in range(start + 1, len(lines)) if re.match(r"^[^\s#]", lines[i])), len(lines))
    while end - 1 > start and (not lines[end - 1].strip() or lines[end - 1].startswith("#")):
        end -= 1
    return "\n".join([*lines[:end], *added, *lines[end:]])


def _atomic_write(path: Path, text: str) -> None:
    temp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    try:
        temp.write_bytes(text.encode("utf-8"))
        os.replace(temp, path)
    finally:
        temp.unlink(missing_ok=True)


# --- the import ------------------------------------------------------------------------------------------


def import_servers(
    source: str,
    *,
    registry_path: Path,
    env_path: Path | None = None,
    dry_run: bool = False,
    only: Sequence[str] | None = None,
    home: Path | None = None,
    environ: Mapping[str, str] | None = None,
) -> ImportReport:
    """Import the servers of ``source`` into the registry file and ``.env``; see the module docstring."""
    path = source_path(source, home=home, environ=environ)
    servers = read_servers(path)
    report = ImportReport(source=source, dry_run=dry_run)
    wanted = None if only is None else set(only)
    for missing in sorted((wanted or set()) - set(servers)):
        report.skipped.append(SkippedServer(missing, f"not defined in {path.name}"))

    original = (
        registry_path.read_bytes().decode("utf-8") if registry_path.is_file() else ""
    )  # bytes: keep CRLF
    try:
        existing = parse_registry(original, source=str(registry_path)) if original.strip() else None
    except RegistryError as exc:
        raise ImportFailed(f"the registry is not valid, fix it first ({exc})") from None
    providers = set(existing.providers) if existing else set()
    connections = {c.id for _, c in existing.iter_connections()} if existing else set()
    namespaces = {
        (p.mcp.namespace or pid) for pid, p in (existing.providers.items() if existing else []) if p.mcp
    }

    plans: list[_Plan] = []
    for name, definition in servers.items():
        if wanted is not None and name not in wanted:
            continue
        if definition.get("enabled") is False:
            report.skipped.append(SkippedServer(name, "disabled in the source"))
            continue
        provider_id = _slug(name)
        if provider_id in providers or provider_id in namespaces:
            report.skipped.append(
                SkippedServer(name, f"provider '{provider_id}' already exists in the registry")
            )
            continue
        if f"{provider_id}-01" in connections:
            report.skipped.append(SkippedServer(name, f"connection '{provider_id}-01' already exists"))
            continue
        try:
            plan = _plan_server(name, definition, provider_id)
        except ValueError as exc:
            report.skipped.append(SkippedServer(name, str(exc)))
            continue
        providers.add(provider_id)
        plans.append(plan)
        report.imported.append(plan.server)

    if plans and not dry_run:
        text = insert_providers(
            original, [render_provider(p.server.provider_id, p.block, source) for p in plans]
        )
        _check(text, registry_path)  # nothing is written unless the whole result validates
        for plan in plans:
            for env_name, value in plan.secrets.items():
                set_secret(env_name, value, env_path=env_path)
        registry_path.parent.mkdir(parents=True, exist_ok=True)
        _atomic_write(registry_path, text)
        load_registry(registry_path)  # what was written is what the Farm will load
    return report


def _check(text: str, registry_path: Path) -> None:
    try:
        parse_registry(text, source=str(registry_path))
    except RegistryError as exc:
        raise ImportFailed(
            f"the registry would not be valid after the import ({exc}); nothing was written"
        ) from None


def format_report(report: ImportReport) -> str:
    """The report as text for the terminal: server names, transports and variable *names*, never values."""
    lines = [
        f"farm mcp import --from {report.source}"
        + ("   (dry run: nothing is written)" if report.dry_run else "")
    ]
    verb = "would import" if report.dry_run else "imported"
    lines.append(f"{verb} {len(report.imported)} server(s)")
    for server in report.imported:
        lines.append(f"  {server.name}: provider '{server.provider_id}', {server.transport}")
        if server.secrets:
            lines.append(
                f"      .env {'would get' if report.dry_run else 'got'}: {', '.join(server.secrets)}"
            )
        if server.references:
            lines.append(f"      needs in .env: {', '.join(server.references)}")
        lines.extend(f"      {note}" for note in server.notes)
    if report.skipped:
        lines.append(f"skipped {len(report.skipped)}")
        lines.extend(f"  {skipped.name}: {skipped.reason}" for skipped in report.skipped)
    if report.imported and not report.dry_run:
        lines.append("next: farm registry sync, then farm mcp sync, then restart farm serve")
    return "\n".join(lines)
