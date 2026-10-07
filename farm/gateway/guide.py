"""Live capability and worker guide for Harness Farm (GUIDE1 / GUIDE1b).

Generates a compact Markdown guide (<= ~1,500 tokens) live from the database:
- What Harness Farm is: one MCP server connector, pass-through MCP, AI CLI workers, built-in tools
- MCP servers: names, usable/total accounts, tool counts, key tools ordered read-only first with hints
- AI workers: pools, usable accounts, models, effort support, limits/reset, max_parallel, defaults
- Rules: budgets, hard stop, free-first, run tracking (run_id, get_run), cost per call
- Recipes: parallel research, second opinion, multi-turn follow-up, credit-safe calls, capacity check
- Need something else: filing integration requests for missing MCPs, CLIs, APIs, or accounts
"""

from __future__ import annotations

import fnmatch
from datetime import datetime
from typing import Any, Literal

from farm.ai.accounts import AccountInfo, load_accounts
from farm.db.pool import DbPool
from farm.mcp import store
from farm.resources import health

Section = Literal["all", "mcp", "ai", "rules", "recipes", "need"]

HEADER_TEXT = """# What Harness Farm is
One MCP server (connector) your IDE attaches to. Behind it:
- Pass-through MCP servers (remote HTTP and local stdio)
- AI CLI workers (Claude Code, Codex, Gemini/Antigravity, Hermes)
- Built-in Farm tools
Plus the dashboard (web app) and the `farm` CLI (owner admin).
Not a plugin; the skill file teaches sessions to use it."""

DEFAULT_RECOMMENDATIONS = """- Defaults:
  - Coding: Claude, Codex
  - Research: Claude, Gemini
  - Bulk text: Gemini, Hermes"""

RULES_TEXT = """## Rules & Etiquette
- Budgets & Hard Stops: Calls respect workspace budgets and stop cleanly on limits.
- Free-First: When configured, the Farm uses subscription/free quota before pay-per-token.
- Run Tracking: Every capability and MCP call is recorded with a unique run_id; inspect with get_run(run_id).
- Cost Transparency: Every response includes USD cost and units consumed.
- Cache: Pure/read-only capability requests return cached answers at zero cost."""

RECIPES_TEXT = """## Recipes

### Parallel Research
```python
# Run multiple research prompts across distinct AI workers in parallel
jobs = await ai_start_many([
    {"task": "Topic A analysis", "ai": "claude"},
    {"task": "Topic B analysis", "ai": "gemini"},
])
await ai_wait([j["job_id"] for j in jobs])
results = [await ai_result(j["job_id"]) for j in jobs]
```

### Second Opinion
```python
# Validate a draft or architecture proposal with an alternative model
first = await ask_ai(task="Draft data ingestion plan", ai="claude")
second = await ask_ai(task=f"Critique this architecture:\\n{first['result']['text']}", ai="codex")
```

### Multi-turn Follow-up
```python
# Continue a conversation with the same worker session
job = await ai_start(task="Analyze this bug report", ai="claude")
initial = await ai_result(job["job_id"])
follow_up = await ai_reply(
    conversation_id=job["conversation_id"],
    message="How do we write a regression test for it?",
)
```

### Credit-Safe MCP Calls
```python
# Call read-only tools to inspect data without consuming credits
# Add _farm: {"account": "<conn_id>"} to pin a specific account
res = await call_tool("clay__get-credits-available", arguments={})
```

### Capacity Check
```python
# Check available account headroom before launching large batches
capacity = await get_capacity()
```"""

NEED_SOMETHING_ELSE_TEXT = """## Need something else?
Call `request_integration(name, kind, purpose, task_context?, urgency?, links?)` with what you need and why.
Examples:
- An MCP server by name (e.g. GitHub, Slack, Notion)
- A CLI tool or agent
- An API for data, enrichment, or verification
- Another account for an existing provider pool"""


def _effort_support_text(ai: str, meta: dict[str, object] | None) -> str:
    meta_dict = meta or {}
    raw = meta_dict.get("efforts")
    if isinstance(raw, list) and raw:
        return f"Yes ({', '.join(str(e) for e in raw)})"
    if isinstance(raw, str) and raw:
        return f"Yes ({raw})"
    if ai == "claude":
        return "Yes (low, medium, high, xhigh, max)"
    if ai == "codex":
        return "Yes (low, medium, high)"
    return "No"


def _is_tool_read_only(tool: store.StoredTool) -> bool:
    annotations = tool.definition.annotations
    if not annotations:
        return False
    if isinstance(annotations, dict):
        return bool(annotations.get("readOnlyHint") or annotations.get("read_only_hint"))
    return bool(getattr(annotations, "read_only_hint", False))


def _is_tool_credit_spending(provider_id: str, tool_name: str, annotations: Any) -> bool:
    if annotations:
        if isinstance(annotations, dict):
            if (
                annotations.get("may_spend_credits")
                or annotations.get("spendsCredits")
                or annotations.get("maySpendCredits")
            ):
                return True
        elif getattr(annotations, "may_spend_credits", False):
            return True

    t_lower = tool_name.lower()
    p_lower = provider_id.lower()
    # Clay enrichment tools: add-*-data-points, run_subroutine*
    if "clay" in p_lower or p_lower == "clay":
        if (
            fnmatch.fnmatch(t_lower, "add-*-data-points")
            or fnmatch.fnmatch(t_lower, "add_*_data_points")
            or t_lower.startswith("run_subroutine")
            or t_lower.startswith("run-subroutine")
        ):
            return True

    if (
        fnmatch.fnmatch(t_lower, "add-*-data-points")
        or fnmatch.fnmatch(t_lower, "add_*_data_points")
        or t_lower.startswith("run_subroutine")
        or t_lower.startswith("run-subroutine")
    ):
        return True

    return False


def _tool_hint(provider_id: str, tool: store.StoredTool) -> str:
    if _is_tool_read_only(tool):
        return "read-only"
    if _is_tool_credit_spending(provider_id, tool.name, tool.definition.annotations):
        return "may spend credits"
    return "changes data"


def _tool_sort_key(tool: store.StoredTool) -> tuple[int, int, str]:
    # 0 = read-only, 1 = changes data
    is_ro = _is_tool_read_only(tool)
    ro_rank = 0 if is_ro else 1

    name = tool.name.lower()
    # Useful inspection verbs first (get/list/search/query)
    verb_rank = 1
    for prefix in ("get", "list", "search", "query", "find", "read", "fetch", "check", "inspect"):
        if name.startswith(prefix) or f"_{prefix}" in name or f"-{prefix}" in name:
            verb_rank = 0
            break
    else:
        for prefix in ("create", "update", "delete", "remove", "destroy", "drop"):
            if name.startswith(prefix) or f"_{prefix}" in name or f"-{prefix}" in name:
                verb_rank = 2
                break

    return (ro_rank, verb_rank, tool.name)


async def format_mcp_section(pool: DbPool, now: datetime) -> str:
    providers = await store.list_providers(pool, enabled_only=True)
    if not providers:
        return "## MCP Servers\nNo MCP servers currently configured."

    lines: list[str] = [
        "## MCP Servers",
        "Tools are exposed as `<server>__<tool>`. "
        "Unlisted tools can be discovered with `search_tools` and called with `call_tool`.",
        "To pin an account: pass `_farm: {\"account\": \"<connection_id>\"}` in arguments.",
        "To view all tools for a server: call `list_server_tools(server=\"<server>\")`.",
        "",
    ]

    for p in providers:
        accounts = await store.list_accounts(pool, p.id)
        total_accounts = len(accounts)
        usable_accounts = sum(
            1 for a in accounts if health.block_reason(a.status, a.circuit, a.cooldown_until, now) is None
        )
        stored_tools = await store.load_tools(pool, [p.id])
        tool_count = len(stored_tools)

        lines.append(f"### {p.name} (`{p.namespace}`)")
        lines.append(f"- Accounts: {usable_accounts}/{total_accounts} usable now")
        lines.append(f"- Tools: {tool_count} total")

        if stored_tools:
            lines.append("- Key tools:")
            # Sort read-only/most useful first (get/list/search/query before create/update/delete)
            sorted_tools = sorted(stored_tools, key=_tool_sort_key)
            # Show up to 8 tools
            for t in sorted_tools[:8]:
                raw_desc = t.definition.description or "No description"
                one_line = raw_desc.splitlines()[0][:90]
                hint = _tool_hint(p.id, t)
                exposed_name = f"{p.namespace}__{t.name}"
                lines.append(f"  - `{exposed_name}`: {one_line} ({hint})")
        lines.append("")

    return "\n".join(lines).strip()


async def format_ai_section(pool: DbPool, now: datetime) -> str:
    accounts = await load_accounts(pool)
    if not accounts:
        return f"## AI Workers\nNo AI workers currently configured.\n\n{DEFAULT_RECOMMENDATIONS}"

    lines: list[str] = [
        "## AI Workers",
        DEFAULT_RECOMMENDATIONS,
        "",
    ]

    by_ai: dict[str, list[AccountInfo]] = {}
    for a in accounts:
        by_ai.setdefault(a.ai, []).append(a)

    for ai_id, acc_list in sorted(by_ai.items()):
        total = len(acc_list)
        usable = [a for a in acc_list if a.unusable_reason(now) is None]
        models: set[str] = set()
        for a in acc_list:
            models.update(a.models)
        models_str = ", ".join(sorted(models)) if models else "default"

        meta_example = dict(acc_list[0].meta) if acc_list else {}
        effort_text = _effort_support_text(ai_id, meta_example)

        tiers = {
            str(a.meta.get("tier") or a.meta.get("plan") or "standard")
            for a in acc_list
            if isinstance(a.meta, dict) and (a.meta.get("tier") or a.meta.get("plan"))
        }
        tier_str = f" [tier: {', '.join(sorted(tiers))}]" if tiers else ""

        max_parallel = max((a.max_parallel for a in acc_list), default=1)

        # Check reset or cooldown
        resets: list[str] = []
        for a in acc_list:
            r = a.next_reset_at or a.cooldown_until
            if r and r > now:
                resets.append(r.strftime("%H:%M:%S"))
        reset_str = f"; next reset at {', '.join(resets)}" if resets else ""

        lines.append(f"### {ai_id.capitalize()} (`{ai_id}`)")
        lines.append(f"- Accounts usable now: {len(usable)}/{total}{tier_str}{reset_str}")
        lines.append(f"- Models: {models_str}")
        lines.append(f"- Effort reasoning: {effort_text}")
        lines.append(f"- Max parallel: {max_parallel}")
        lines.append("")

    return "\n".join(lines).strip()


async def generate_guide(pool: DbPool, now: datetime, section: Section = "all") -> str:
    """Generate the Harness Farm live capability guide formatted in Markdown."""
    parts: list[str] = []

    if section == "all":
        parts.append(HEADER_TEXT)

    if section in ("all", "mcp"):
        parts.append(await format_mcp_section(pool, now))

    if section in ("all", "ai"):
        parts.append(await format_ai_section(pool, now))

    if section in ("all", "rules"):
        parts.append(RULES_TEXT)

    if section in ("all", "recipes"):
        parts.append(RECIPES_TEXT)

    if section in ("all", "need"):
        parts.append(NEED_SOMETHING_ELSE_TEXT)

    return "\n\n".join(parts)


__all__ = [
    "HEADER_TEXT",
    "NEED_SOMETHING_ELSE_TEXT",
    "RECIPES_TEXT",
    "RULES_TEXT",
    "Section",
    "format_ai_section",
    "format_mcp_section",
    "generate_guide",
]
