"""OPEN1: importing MCP servers from Claude Desktop / Claude Code / Codex, and the secret rules around it.

The real ``config/registry.yaml`` is the target of most tests (its comments and layout must survive); the
fixtures are the three real file formats with sentinel secrets that must never appear anywhere but ``.env``.
"""

from __future__ import annotations

import difflib
import json
import logging
import os
import shutil
from pathlib import Path

import pytest
from structlog.testing import capture_logs
from typer.testing import CliRunner

from farm.control.cli import app
from farm.mcp.importer import ImportFailed, format_report, import_servers
from farm.registry import RegistryError, load_registry, parse_registry
from farm.registry.models import looks_like_secret
from farm.secrets import redact, set_secret

FIXTURES = Path(__file__).parent / "fixtures" / "mcp"
REAL_REGISTRY = Path(__file__).parent.parent / "config" / "registry.example.yaml"
DESKTOP = f"file:{FIXTURES / 'claude_desktop_config.json'}"
CODE = f"file:{FIXTURES / 'claude_code.json'}"
CODEX = f"file:{FIXTURES / 'codex_config.toml'}"
SENTINELS = [
    "SENTINEL-desktop-github-token-7f3a",
    "SENTINEL-code-bearer-91d2",
    "SENTINEL-code-team-44ab",
    "SENTINEL-code-url-secret",
    "SENTINEL-code-query-5e5e",
    "SENTINEL-code-project-secret-5c1e",
    "SENTINEL-codex-context7-key-2b8d",
    "SENTINEL-codex-arg-secret-77aa",
]


@pytest.fixture
def target(tmp_path: Path) -> tuple[Path, Path]:
    """A copy of the real registry and the path of a ``.env`` that does not exist yet."""
    registry = tmp_path / "registry.yaml"
    shutil.copy(REAL_REGISTRY, registry)
    return registry, tmp_path / ".env"


def env_lines(env: Path) -> dict[str, str]:
    return dict(line.split("=", 1) for line in env.read_text(encoding="utf-8").splitlines() if line)


# --- the three formats -------------------------------------------------------------------------------------


def test_claude_desktop_servers_become_providers_and_their_secrets_go_to_env(
    target: tuple[Path, Path],
) -> None:
    registry, env = target

    report = import_servers(DESKTOP, registry_path=registry, env_path=env)

    assert [s.provider_id for s in report.imported] == ["filesystem", "github", "notion"]
    provider = load_registry(registry).providers["github"]
    assert provider.mcp is not None and provider.mcp.transport == "stdio" and provider.mcp.command == "npx"
    assert provider.mcp.env == {
        "GITHUB_PERSONAL_ACCESS_TOKEN": "env:FARM_MCP_GITHUB_GITHUB_PERSONAL_ACCESS_TOKEN",
        "GITHUB_API_URL": "env:FARM_MCP_GITHUB_GITHUB_API_URL",
    }
    assert [(c.id, c.auth_ref) for c in provider.connections] == [("github-01", "cli:none")]
    assert env_lines(env) == {
        "FARM_MCP_GITHUB_GITHUB_PERSONAL_ACCESS_TOKEN": "SENTINEL-desktop-github-token-7f3a",
        "FARM_MCP_GITHUB_GITHUB_API_URL": "https://api.github.com",
    }
    # ${NOTION_AUTH_DIR} in the source: a reference to a variable that must exist in .env, nothing to move
    notion = load_registry(registry).providers["notion"].mcp
    assert notion is not None and notion.env == {"MCP_REMOTE_CONFIG_DIR": "env:NOTION_AUTH_DIR"}
    assert report.imported[2].references == ("NOTION_AUTH_DIR",)


def test_claude_code_reads_the_top_level_and_every_project(target: tuple[Path, Path]) -> None:
    registry, env = target

    report = import_servers(CODE, registry_path=registry, env_path=env)

    assert [s.provider_id for s in report.imported] == ["linear", "sentry", "api", "hook", "local-tools"]
    providers = load_registry(registry).providers
    # a remote server with no credentials of its own is an OAuth connector: log in once
    linear = providers["linear"]
    assert linear.mcp is not None and (linear.mcp.transport, linear.mcp.auth) == ("http", "oauth")
    assert [(c.auth_ref, c.status) for c in linear.connections] == [("token-store:linear-01", "needs_login")]
    assert providers["sentry"].mcp is not None and providers["sentry"].mcp.transport == "sse"
    # a bearer token becomes the account's credential, another header a reference; the project's server too
    api = providers["api"]
    assert api.mcp is not None and api.mcp.auth == "env"
    assert api.mcp.headers == {"X-Team": "env:FARM_MCP_API_HEADER_X_TEAM"}
    assert api.connections[0].auth_ref == "env:FARM_MCP_API_TOKEN"
    assert env_lines(env)["FARM_MCP_API_TOKEN"] == "SENTINEL-code-bearer-91d2"
    assert env_lines(env)["FARM_MCP_LOCAL_TOOLS_APP_SECRET"] == "SENTINEL-code-project-secret-5c1e"
    # a URL that is itself the secret lives in .env whole
    hook = providers["hook"].mcp
    assert hook is not None and hook.url == "env:FARM_MCP_HOOK_URL"
    assert "SENTINEL-code-url-secret" in env_lines(env)["FARM_MCP_HOOK_URL"]
    # the first definition of a name wins (local-tools is in two projects)
    assert providers["local-tools"].mcp is not None and providers["local-tools"].mcp.args == ["tools.js"]


def test_codex_toml_with_its_own_keys(target: tuple[Path, Path]) -> None:
    registry, env = target

    report = import_servers(CODEX, registry_path=registry, env_path=env)

    assert [s.provider_id for s in report.imported] == ["context7", "figma"]
    providers = load_registry(registry).providers
    context7 = providers["context7"].mcp
    assert context7 is not None and context7.tools.allow == ["resolve-library-id", "get-library-docs"]
    assert context7.env == {"CONTEXT7_API_KEY": "env:FARM_MCP_CONTEXT7_CONTEXT7_API_KEY"}
    figma = providers["figma"].mcp
    assert figma is not None and figma.auth == "env" and figma.timeout_s == 90
    assert figma.headers == {
        "X-Figma-Region": "env:FARM_MCP_FIGMA_HEADER_X_FIGMA_REGION",
        "X-Figma-Token": "env:FIGMA_PERSONAL_TOKEN",
    }
    assert providers["figma"].connections[0].auth_ref == "env:FIGMA_OAUTH_TOKEN"  # bearer_token_env_var
    assert providers["figma"].config["timeout_s"] == 90  # what the router reads
    skipped = {s.name: s.reason for s in report.skipped}
    assert skipped["old"] == "disabled in the source"
    assert "credential" in skipped["leaky"]  # a secret in args cannot be moved: that server is not imported


def test_the_named_sources_are_found_where_the_clients_keep_them(
    target: tuple[Path, Path], tmp_path: Path
) -> None:
    registry, env = target
    home = tmp_path / "home"
    (home / ".codex").mkdir(parents=True)
    shutil.copy(FIXTURES / "codex_config.toml", home / ".codex" / "config.toml")
    shutil.copy(FIXTURES / "claude_code.json", home / ".claude.json")
    appdata = tmp_path / "appdata" / "Claude"
    appdata.mkdir(parents=True)
    shutil.copy(FIXTURES / "claude_desktop_config.json", appdata / "claude_desktop_config.json")
    environ = {"APPDATA": str(tmp_path / "appdata")}

    names = []
    for source in ("claude-desktop", "claude-code", "codex"):
        report = import_servers(source, registry_path=registry, env_path=env, home=home, environ=environ)
        names += [s.name for s in report.imported]

    assert names == [
        "filesystem",
        "github",
        "notion",
        "linear",
        "sentry",
        "api",
        "hook",
        "local-tools",
        "context7",
        "figma",
    ]


# --- no secret anywhere but .env ---------------------------------------------------------------------------


def test_no_secret_value_reaches_the_registry_the_report_or_the_logs(
    target: tuple[Path, Path], caplog: pytest.LogCaptureFixture
) -> None:
    registry, env = target
    shown: list[str] = []
    with capture_logs() as structured, caplog.at_level(logging.DEBUG):
        for source in (DESKTOP, CODE, CODEX):
            shown.append(format_report(import_servers(source, registry_path=registry, env_path=env)))

    everything = (
        registry.read_text(encoding="utf-8") + "\n".join(shown) + caplog.text + json.dumps(structured)
    )
    for sentinel in SENTINELS:
        assert sentinel not in everything, sentinel
    written = env.read_text(encoding="utf-8")
    assert all(
        s in written for s in SENTINELS if "arg-secret" not in s
    )  # all of them are in .env, only there


def test_a_dry_run_names_servers_transports_and_variables_and_writes_nothing(
    target: tuple[Path, Path],
) -> None:
    registry, env = target
    before = registry.read_bytes()

    report = import_servers(CODE, registry_path=registry, env_path=env, dry_run=True)
    text = format_report(report)

    assert registry.read_bytes() == before and not env.exists()
    assert "dry run" in text and "api: provider 'api', http" in text
    assert "FARM_MCP_API_TOKEN" in text  # the name of the variable it would write
    assert not any(s in text for s in SENTINELS)  # never a value


def test_a_secret_in_args_skips_that_server_only_and_says_why_without_the_value(
    target: tuple[Path, Path],
) -> None:
    registry, env = target

    report = import_servers(CODEX, registry_path=registry, env_path=env)

    (leaky,) = [s for s in report.skipped if s.name == "leaky"]
    assert "SENTINEL" not in leaky.reason and "leaky" not in {s.name for s in report.imported}
    assert "leaky" not in load_registry(registry).providers
    assert "SENTINEL-codex-arg-secret-77aa" not in env.read_text(encoding="utf-8")


# --- the registry file -------------------------------------------------------------------------------------


def test_the_registry_keeps_its_comments_and_layout_new_providers_are_only_inserted(
    target: tuple[Path, Path],
) -> None:
    registry, env = target
    original = registry.read_text(encoding="utf-8")

    import_servers(DESKTOP, registry_path=registry, env_path=env)

    opcodes = difflib.SequenceMatcher(
        None, original.split("\n"), registry.read_text(encoding="utf-8").split("\n")
    ).get_opcodes()
    assert {tag for tag, *_ in opcodes} <= {"equal", "insert"}  # nothing of the owner's file was changed
    text = registry.read_text(encoding="utf-8")
    assert text.index("# imported by `farm mcp import`") < text.index("\ncapabilities:")  # inside providers:
    assert "# Tool pools (kind: tool)" in text


def test_a_registry_with_windows_line_endings_stays_that_way(tmp_path: Path) -> None:
    registry = tmp_path / "registry.yaml"
    registry.write_bytes(REAL_REGISTRY.read_bytes().replace(b"\r\n", b"\n").replace(b"\n", b"\r\n"))

    import_servers(DESKTOP, registry_path=registry, env_path=tmp_path / ".env")

    raw = registry.read_bytes()
    assert raw.count(b"\n") == raw.count(b"\r\n")  # every line break is still CRLF
    assert "filesystem" in load_registry(registry).providers


def test_providers_that_exist_are_skipped_and_reported_the_second_time(target: tuple[Path, Path]) -> None:
    registry, env = target
    import_servers(DESKTOP, registry_path=registry, env_path=env)
    after_first = registry.read_bytes()

    again = import_servers(DESKTOP, registry_path=registry, env_path=env)

    assert again.imported == []
    assert [(s.name, "already exists" in s.reason) for s in again.skipped] == [
        ("filesystem", True),
        ("github", True),
        ("notion", True),
    ]
    assert registry.read_bytes() == after_first


def test_only_imports_the_named_servers_and_reports_an_unknown_one(target: tuple[Path, Path]) -> None:
    registry, env = target

    report = import_servers(DESKTOP, registry_path=registry, env_path=env, only=["github", "nope"])

    assert [s.name for s in report.imported] == ["github"]
    assert [(s.name, s.reason) for s in report.skipped] == [
        ("nope", "not defined in claude_desktop_config.json")
    ]
    assert "filesystem" not in load_registry(registry).providers


def test_nothing_is_written_when_the_registry_cannot_take_the_import(tmp_path: Path) -> None:
    registry, env = tmp_path / "registry.yaml", tmp_path / ".env"
    registry.write_text("providers: [this is not a registry\n", encoding="utf-8")

    with pytest.raises(ImportFailed, match="registry is not valid"):
        import_servers(DESKTOP, registry_path=registry, env_path=env)

    assert not env.exists()


def test_a_registry_without_providers_yet_takes_the_first_ones(tmp_path: Path) -> None:
    registry = tmp_path / "new.yaml"
    registry.write_text(
        "settings: {owner_email: me@example.com}\nproviders: {}\ncapabilities: {}\n", encoding="utf-8"
    )

    import_servers(DESKTOP, registry_path=registry, env_path=tmp_path / ".env", only=["filesystem"])

    assert list(parse_registry(registry.read_text(encoding="utf-8")).providers) == ["filesystem"]


@pytest.mark.parametrize("source", ["nowhere", "file:", "file:missing.json"])
def test_an_unusable_source_is_a_clear_error(source: str, tmp_path: Path) -> None:
    with pytest.raises(ImportFailed):
        import_servers(source, registry_path=tmp_path / "r.yaml", env_path=tmp_path / ".env", home=tmp_path)


# --- the command line ----------------------------------------------------------------------------------------


def test_the_import_command_prints_names_and_never_values(target: tuple[Path, Path]) -> None:
    registry, env = target

    result = CliRunner().invoke(
        app, ["mcp", "import", "--from", DESKTOP, "--registry", str(registry), "--env-file", str(env)]
    )

    assert result.exit_code == 0, result.output
    assert "github" in result.output and "FARM_MCP_GITHUB_GITHUB_PERSONAL_ACCESS_TOKEN" in result.output
    assert not any(s in result.output for s in SENTINELS)
    assert "farm registry sync" in result.output  # what to do next


def test_the_import_command_fails_with_one_line_not_a_traceback(tmp_path: Path) -> None:
    result = CliRunner().invoke(app, ["mcp", "import", "--from", "file:" + str(tmp_path / "gone.json")])

    assert result.exit_code == 1 and "cannot read" in result.output
    assert "Traceback" not in result.output


# --- set-secret: the one way a credential reaches .env -----------------------------------------------------


def test_set_secret_adds_and_replaces_lines_and_keeps_the_rest_of_the_file(tmp_path: Path) -> None:
    env = tmp_path / ".env"
    env.write_text("# my keys\nA=1\nFARM_MCP_X_TOKEN=old\r\nB=2\n", encoding="utf-8")

    set_secret("FARM_MCP_X_TOKEN", "new-value-123", env_path=env)
    set_secret("FARM_MCP_Y_TOKEN", "other-value-456", env_path=env)

    assert env.read_bytes().decode().replace("\r\n", "\n") == (
        "# my keys\nA=1\nFARM_MCP_X_TOKEN=new-value-123\nB=2\nFARM_MCP_Y_TOKEN=other-value-456\n"
    )


def test_set_secret_values_survive_the_loader_even_when_they_are_awkward(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from farm.settings import load_env

    env = tmp_path / ".env"
    monkeypatch.setattr(os, "environ", dict(os.environ))  # load_env writes here: undone with the test
    awkward = {
        "V_SPACED": "  padded value  ",
        "V_QUOTED": '"quoted"',
        "V_HASH": "a#b c#d",
        "V_EQUALS": "x=y=z",
    }
    for name, value in awkward.items():
        set_secret(name, value, env_path=env)

    load_env(env)

    assert {name: os.environ[name] for name in awkward} == awkward


@pytest.mark.parametrize(
    ("name", "value"),
    [
        ("not a name", "hunter2-a"),
        ("OK_NAME", ""),
        ("OK_NAME", "two\nhunter2-b"),
        ("OK_NAME", "nul\0hunter2-c"),
    ],
)
def test_set_secret_refuses_what_a_env_file_cannot_hold(tmp_path: Path, name: str, value: str) -> None:
    env = tmp_path / ".env"

    with pytest.raises(ValueError) as caught:
        set_secret(name, value, env_path=env)

    assert "hunter2" not in str(caught.value)  # the message never echoes the value
    assert not env.exists()


def test_set_secret_registers_the_value_so_logs_mask_it(tmp_path: Path) -> None:
    set_secret("FARM_MCP_Z_TOKEN", "super-secret-zzz-9876", env_path=tmp_path / ".env")

    assert "super-secret-zzz-9876" not in redact("the server said super-secret-zzz-9876 is wrong")


def test_the_set_secret_command_reads_a_hidden_value_and_prints_no_value(tmp_path: Path) -> None:
    env = tmp_path / ".env"

    result = CliRunner().invoke(
        app, ["set-secret", "FARM_MCP_CLI_TOKEN", "--env-file", str(env)], input="cli-secret-1357\n"
    )

    assert result.exit_code == 0, result.output
    assert "cli-secret-1357" not in result.output and "saved FARM_MCP_CLI_TOKEN" in result.output
    assert env.read_text(encoding="utf-8") == "FARM_MCP_CLI_TOKEN=cli-secret-1357\n"


# --- the registry refuses credentials ----------------------------------------------------------------------


def _doc(mcp: str, extra: str = "") -> str:
    return (
        "settings: {owner_email: me@example.com}\ncapabilities: {}\nproviders:\n  x:\n    kind: tool\n"
        f"    executor: mcp\n    mcp:\n{mcp}\n    connections: [{{id: x-01, auth_ref: 'cli:none'{extra}}}]\n"
    )


@pytest.mark.parametrize(
    ("mcp", "complaint"),
    [
        ("      command: npx\n      env: {TOKEN: abc123456789}", "env:NAME"),
        ("      command: npx\n      args: ['--api-key=abcdef123456']", "credential"),
        ("      command: npx\n      args: ['" + "ghp_" + "A" * 30 + "']", "credential"),
        ("      transport: http\n      url: 'https://x.example.com/mcp?api_key=abcdef123456'", "credential"),
        ("      transport: http\n      url: 'https://user:pw@x.example.com/mcp'", "credential"),
        (
            "      transport: http\n      url: https://x.example.com/mcp\n      headers: {Authorization: 'Bearer abcdefghijklmnop1234'}",
            "env:NAME",
        ),
        ("      command: npx\n      url: https://x.example.com/mcp", "belong to http"),
        (
            "      transport: http\n      url: https://x.example.com/mcp\n      command: npx",
            "belong to stdio",
        ),
        ("      transport: http\n      url: https://x.example.com/mcp\n      auth: oauth", "token-store"),
    ],
)
def test_the_registry_rejects_literal_credentials_and_incoherent_blocks(mcp: str, complaint: str) -> None:
    with pytest.raises(RegistryError, match=complaint) as caught:
        parse_registry(_doc(mcp))

    assert "abcdef123456" not in str(caught.value)  # the error names the field, never the value


def test_credentials_are_rejected_anywhere_in_the_registry_not_only_in_the_mcp_block() -> None:
    document = _doc("      command: npx").replace(
        "connections: [", "config: {note: 'sk-" + "a" * 30 + "'}\n    connections: ["
    )

    with pytest.raises(RegistryError, match=r"providers\.x\.config\.note"):
        parse_registry(document)


def test_a_connection_may_override_env_and_headers_with_references_only() -> None:
    good = _doc("      command: npx", ", meta: {mcp: {env: {TOKEN: 'env:FARM_MCP_X_TOKEN_2'}}}")
    assert parse_registry(good).providers["x"].connections[0].meta == {
        "mcp": {"env": {"TOKEN": "env:FARM_MCP_X_TOKEN_2"}, "headers": {}}
    }

    with pytest.raises(RegistryError, match="env:NAME"):
        parse_registry(_doc("      command: npx", ", meta: {mcp: {env: {TOKEN: literal-value-1}}}"))


@pytest.mark.parametrize(
    ("text", "secret"),
    [
        ("sk-" + "a" * 24, True),
        ("--token=abcdef123", True),
        ("API_TOKEN=abcdef123", True),
        ("https://u:p@host/x", True),
        ("Bearer " + "x" * 20, True),
        ("npx", False),
        ("-y", False),
        ("@modelcontextprotocol/server-github", False),
        ("--max-tokens=100000", False),
        ("--author=JohnDoe", False),
        ("--oauth-mode=device", False),
        ("https://mcp.linear.app/mcp", False),
    ],
)
def test_the_secret_shape_detector(text: str, secret: bool) -> None:
    assert looks_like_secret(text) is secret
