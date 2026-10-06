# Brief GUIDE1 — any session learns what the Farm can do right now, in one call

Rules: follow `briefs/CONTEXT.md` §6. Touch only the files under "Owns". Never git add/commit/push. Never read `.env`.
Run every command in the foreground and wait for it; never start one in the background and poll it. Report once.
A real `farm run` serves on 127.0.0.1:8787 with heartbeats in D:/farm-data: tests never use that port or those files.

## Goal (owner, 2026-10-06)
Any AI session in any project (Claude Code, Codex, Cursor, Gemini CLI…) that connects to `harness-farm` must immediately know **what the Farm can do
for it right now** and **when to use it**, without reading docs.

## Owns
`farm/gateway/guide.py` (new), `farm/gateway/server.py` (INSTRUCTIONS + register the guide), `farm/gateway/mcp_tools.py` (search limit, namespace
listing, unknown-tool bug), `farm/control/cli.py` (`farm guide`, `farm connect --skill`), `docs/skills/harness-farm/SKILL.md` (new),
tests `tests/test_guide.py`, `tests/test_mcp_discovery.py`.

## Build
1. **`farm_guide(section?: "all"|"mcp"|"ai"|"rules"|"recipes")` tool** (read-only), also served as MCP resource `farm://guide` and MCP prompt
   `use-harness-farm`. Generated live from the database, compact (≤ ~1,500 tokens for "all"):
   - **MCP servers**: name, accounts usable now / total, tool count, 5–8 most useful tool names with one-line descriptions, which tools are
     read-only vs may spend credits (from annotations), how to pin an account (`_farm.account`).
   - **AI workers**: per pool: accounts usable now (status, plan tier free/paid if known), models, whether effort is supported, limits/reset,
     max_parallel; which one is the default for research, coding, bulk text.
   - **Rules**: budgets/hard stop, free-first when configured, every call recorded (run_id, get_run), cost shown per call.
   - **Recipes** (3–6 lines each): parallel research on several AI accounts (`ai_start_many` → `ai_wait` → `ai_result`), second opinion,
     multi-turn follow-up (`ai_reply`), using an MCP server without spending credits (read-only tools), checking capacity before a big job.
2. **INSTRUCTIONS**: first sentence becomes "Harness Farm gives you extra tools and AI workers; call farm_guide() first to see what is available
   right now." Keep the rest short.
3. **Discovery fixes**: `search_tools` returns up to 25 results (setting); add `list_server_tools(server)` returning every tool name + one-line
   description for one MCP server; fix the bug where a synced, registered tool (e.g. Clay `get-credits-available`, exposed `clay__get-credits-available`)
   answers "Unknown tool" through `call_tool` and is missing from search — find the root cause (registration vs search index vs naming) and test it.
4. **Skill for other sessions**: `docs/skills/harness-farm/SKILL.md` (Claude Code skill format: frontmatter `name`, `description` saying *when* to
   use the Farm — delegate research/bulk/second opinions to AI workers, use connected MCP servers — and a short body: call `farm_guide` first,
   the recipes, cost/credit etiquette). `farm connect <ide> --skill` prints where to copy it for that IDE (Claude Code: `~/.claude/skills/`; Codex:
   an `AGENTS.md` snippet); `--write` only after a y/N prompt.
5. `farm guide` CLI prints the same text for humans.

## Acceptance tests (no network)
- With fake MCP providers (one with 30 tools) + fake AI accounts: `farm_guide()` lists servers, counts, usable accounts, models/effort, recipes;
  stays under the size limit; `section="ai"` returns only AI.
- `list_server_tools("fake")` returns all 30; `search_tools` can return 25; every registered tool is callable via `call_tool` (regression test for the
  unknown-tool bug); the resource and prompt exist.
- The skill file has valid frontmatter; `farm connect claude-code --skill` prints the copy path and never writes without `--write` + confirmation.

## Done when
`powershell -File scripts/check.ps1` → `RESULT: all passed`. Reply ≤ 10 lines: files, tests, gate tail, the unknown-tool root cause.
