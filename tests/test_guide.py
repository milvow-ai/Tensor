"""Tests for farm_guide and skill file (GUIDE1b)."""

from __future__ import annotations

import re
from datetime import UTC, datetime
from pathlib import Path

import pytest
import yaml
from mcp_types import Tool as McpToolDef
from mcp_types import ToolAnnotations
from typer.testing import CliRunner

from farm.control.cli import app
from farm.db.pool import DbPool
from farm.gateway.guide import (
    _tool_hint,
    _tool_sort_key,
    generate_guide,
)
from farm.mcp.store import StoredTool

runner = CliRunner()


def _make_stored_tool(name: str, read_only: bool = False, description: str = "A test tool") -> StoredTool:
    annotations = ToolAnnotations(readOnlyHint=read_only) if read_only else None
    return StoredTool(
        provider="test-provider",
        definition=McpToolDef(
            name=name,
            description=description,
            inputSchema={"type": "object"},
            annotations=annotations,
        ),
        schema_hash="abc",
        synced_at=datetime.now(UTC),
    )



def test_tool_hints_correct_labels() -> None:
    # Read-only tool
    ro_tool = _make_stored_tool("get_user", read_only=True)
    assert _tool_hint("test-provider", ro_tool) == "read-only"

    # Regular mutating tool
    mut_tool = _make_stored_tool("create_user", read_only=False)
    assert _tool_hint("test-provider", mut_tool) == "changes data"

    # Clay enrichment tool: add-*-data-points
    clay_tool1 = _make_stored_tool("add-person-data-points", read_only=False)
    assert _tool_hint("clay", clay_tool1) == "may spend credits"

    # Clay enrichment tool: run_subroutine
    clay_tool2 = _make_stored_tool("run_subroutine_enrich", read_only=False)
    assert _tool_hint("clay", clay_tool2) == "may spend credits"


def test_tool_sorting_read_only_first() -> None:
    tools = [
        _make_stored_tool("delete_item", read_only=False),
        _make_stored_tool("create_item", read_only=False),
        _make_stored_tool("update_item", read_only=False),
        _make_stored_tool("list_items", read_only=True),
        _make_stored_tool("get_item", read_only=True),
        _make_stored_tool("search_items", read_only=True),
    ]

    sorted_tools = sorted(tools, key=_tool_sort_key)
    names = [t.name for t in sorted_tools]

    # All read-only tools must appear before mutating tools
    assert names[0] in ("get_item", "list_items", "search_items")
    assert names[1] in ("get_item", "list_items", "search_items")
    assert names[2] in ("get_item", "list_items", "search_items")
    assert names[3:] == ["create_item", "delete_item", "update_item"] or all(
        n in ("create_item", "delete_item", "update_item") for n in names[3:]
    )


async def test_farm_guide_header_and_need_section(pool: DbPool) -> None:
    now = datetime(2026, 10, 7, 12, 0, 0, tzinfo=UTC)
    guide = await generate_guide(pool, now, section="all")

    # Header present
    assert "What Harness Farm is" in guide
    assert "one MCP server" in guide.lower() or "One MCP server" in guide
    assert "AI CLI workers" in guide or "AI workers" in guide

    # Need something else present
    assert "Need something else?" in guide
    assert "request_integration" in guide

    # Size limit: compact guide <= ~1500 tokens (roughly <= 6000 characters)
    assert len(guide) < 8000


def test_skill_frontmatter_valid() -> None:
    skill_path = Path(__file__).resolve().parent.parent / "docs" / "skills" / "harness-farm" / "SKILL.md"
    assert skill_path.is_file(), "Skill file must exist"

    content = skill_path.read_text(encoding="utf-8")
    match = re.match(r"^---\s*\n(.*?)\n---\s*\n(.*)$", content, re.DOTALL)
    assert match is not None, "Skill file must have valid YAML frontmatter"

    fm_raw = match.group(1)
    fm = yaml.safe_load(fm_raw)
    assert fm.get("name") == "harness-farm"
    desc = fm.get("description", "").lower()
    assert "ai" in desc or "worker" in desc
    assert "mcp" in desc or "integration" in desc

    body = match.group(2)
    assert "farm_guide" in body
    assert "request_integration" in body
    assert "recipes" in body.lower()


def test_farm_connect_skill_prints_path_without_writing(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    fake_home = tmp_path / "home"
    fake_home.mkdir()
    monkeypatch.setattr(Path, "home", lambda: fake_home)

    target_skill = fake_home / ".claude" / "skills" / "harness-farm" / "SKILL.md"

    # Run connect claude-code --skill without --write
    res = runner.invoke(app, ["connect", "claude-code", "--skill"])
    assert res.exit_code == 0
    assert ".claude" in res.stdout
    assert "SKILL.md" in res.stdout
    assert not target_skill.exists(), "Should not write without --write"

    # Run connect claude-code --skill --write with confirmation rejected (N)
    res_rejected = runner.invoke(app, ["connect", "claude-code", "--skill", "--write"], input="n\n")
    assert res_rejected.exit_code == 0
    assert "Cancelled" in res_rejected.stdout
    assert not target_skill.exists(), "Should not write when confirmation is rejected"

    # Run connect claude-code --skill --write with confirmation accepted (y)
    res_accepted = runner.invoke(app, ["connect", "claude-code", "--skill", "--write"], input="y\n")
    assert res_accepted.exit_code == 0
    assert target_skill.is_file(), "Should write skill when confirmed"
    assert "harness-farm" in target_skill.read_text(encoding="utf-8")
