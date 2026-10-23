# Q0A — Tool lookup and compact discovery (quality baseline)

Rules for every step: touch only the files under **Owns**. Never run `git add / commit / push / reset / checkout / stash`, deploy,
delete anything outside this worktree, or send data off this machine. Never read or print any `.env` file. Never run migrations
against a live database. Run every command in the foreground and wait for it.

**Goal.** Agents — and later Tensor — must find and call every pooled MCP tool, cheaply. Two live defects block that
(`docs/QUALITY.md` D2, D3). Fix both at the root, each with a regression test built from the real failure.

**Read first:** `briefs/CONTEXT.md` · `docs/QUALITY.md` (D2, D3) · `docs/ARCHITECTURE.md` §8 · `farm/gateway/mcp_tools.py` ·
`farm/mcp/naming.py` · `farm/mcp/expose.py` · `farm/mcp/store.py` · `farm/gateway/guide.py` · the tests that cover them
(grep for `search_tools`, `call_tool`).

**Already known (live, 2026-10-24):**
- `call_tool("clay__get-credits-available")` → `Unknown tool: 'clay__get-credits-available'`, although its row exists in
  `mcp_tools`. `call_tool("clay__list_subroutines")` works. `search_tools` does find hyphenated tools such as
  `clay__run_subroutine`, `clay__get-task`, `clay__search-companies`, `clay__load-more-search-results`.
- Hypothesis only — prove or reject it: the name-variant resolution in `mcp_tools.py` (around `_make_call_tool`: "Resolves
  unprefixed, colon/slash namespaced, and hyphen/underscore variants") maps the hyphenated name to another key, or the tool is
  missing from the transform's catalog because of the `expose` / direct-limit rules.
- `search_tools("clay run subroutine search companies people task")` returned 25 tools as full `list_tools` definitions:
  161,435 characters.

**Steps:**
1. Write a failing test for D2 with a new fake MCP server, `tests/fake_mcp_tools_server.py`, exposing tools named with
   hyphens, underscores and both (`get-credits-available`, `list_subroutines`, `get_task-context`), some listed directly and some
   only discoverable. Assert each is callable through `call_tool` by its exact `<namespace>__<name>`, and through each documented
   variant. Run it on the current code and confirm it fails.
2. Fix the root cause in the module that owns it. Exact names stay authoritative; a variant resolves only when it is
   unambiguous, and an ambiguous variant returns an error that lists the candidates.
3. D3 — compact `search_tools`: per hit return `name`, `server`, a one-line `description` (≤ 160 characters, cut at a word
   boundary), `risk` (`read-only` / `changes-data` / `may-spend`, from the existing labels) and `score`; no input schemas. Add
   `describe_tool(name)` that returns the full definition (schema, annotations). Keep BM25 ranking; default 10 results, max 25.
4. Update `farm_guide` text only where it describes search output.
5. Tests: every D2 name form; ambiguity error; 25 search results ≤ 6,000 characters; `describe_tool` returns the exact schema.
6. Run `powershell -NoProfile -ExecutionPolicy Bypass -File scripts/check.ps1`.

**Owns:** `farm/gateway/mcp_tools.py` · `farm/mcp/naming.py` · `farm/mcp/expose.py` · `farm/mcp/store.py` (not its
`workspace_id` line — F1A renames that line in parallel) · `farm/gateway/guide.py` · `tests/fake_mcp_tools_server.py` (new) ·
`tests/test_q0a_*.py` (new).

**Must not touch:** `tests/fake_mcp_server.py`, migrations, `farm/executors/**`, `farm/db/**`, `config/*`, `console/`,
`pyproject.toml`, `uv.lock`, any other file.

**Done when:** `check.ps1` ends with `RESULT: all passed`, and the new tests fail on the old code and pass on the new (say how
you showed it).

**Reply (15 lines or fewer):** root cause of D2 in one sentence · files changed · new test names · `check.ps1` tail · open issues.
