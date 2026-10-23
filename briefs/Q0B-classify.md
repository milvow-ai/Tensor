# Q0B — AI account state classification, fixed as a class

Rules for every step: touch only the files under **Owns**. Never run `git add / commit / push / reset / checkout / stash`, deploy,
delete anything outside this worktree, or send data off this machine. Never read or print any `.env` file. Run every command in
the foreground and wait for it.

**Goal.** AI accounts are falsely marked "needs login" or "limit reached" after **successful** runs (`docs/QUALITY.md` D1, D4;
the "success JSON stored as last_error" symptom is the same defect). The CLI drivers scan stdout + stderr — including the AI's own
answer — for error words *before* checking the structured success status. Replace that with one classifier that reads structured
results first, reads errors only from error channels, and matches exact signatures taken from real outputs. Every false flag
stops real work, so this is the most expensive defect class in the Farm.

**Read first:** `briefs/CONTEXT.md` (§7 AI CLI facts) · `docs/QUALITY.md` §2 ·
`farm/executors/cli_agent/{base,claude,codex,agy,gemini_cli,hermes}.py` · `farm/executors/base.py` (`ErrorKind`, `ExecResult`) ·
`tests/test_cli_agent_*.py`.

**Already known:**
- Verified in code: `agy.py` (~lines 147–199) and `claude.py` (~101–156) run auth / limit regexes over `stdout + stderr` before
  parsing the structured result; the patterns include `capacity`, `/login`, `rate limit`, `\b403\b`, `resets in`. `codex.py`,
  `hermes.py` and `gemini_cli.py` share the structure — confirm.
- Live 2026-10-24: agy-01 is listed `needs_login`, but `agy -p "Reply with exactly: PONG" --output-format json` returns
  `{"status":"SUCCESS","response":"PONG\n",...}`. claude-02 / -04 have a successful run's JSON stored as `last_error`.
- Real failure texts — use verbatim as fixtures:
  - agy limit (stderr): `error: Individual quota reached. Please upgrade your subscription to increase your limits. Resets in 91h35m7s.`
    then `AGY_ERROR: {"short_error":"RESOURCE_EXHAUSTED (code 429): Individual quota reached. Please upgrade your subscription to increase your limits. Resets in 91h35m7s.","status":"RESOURCE_EXHAUSTED","error_code":429,"code_kind":"http","retryable":true,"error_id":"6f7e8358-c2ce-4aae-9602-0ea940095c47-1"}`
  - agy not eligible: `Eligibility check failed: Your current account is not eligible for Antigravity. Verify your account to continue.`
  - Hermes auth (stdout, usage file `failed: true`): `Custom endpoint rejected your API key, so the model can't be reached. Update it in Settings → Providers, or run ` + "`hermes setup`" + ` in a terminal.` + blank line + `Provider said: HTTP 401: The security token included in the request has expired.`
  - Codex (stderr): `ERROR codex_core::session: failed to record rollout items: thread 01a139a6-0907-7112-b4c2-be3622cb7157 not found`
    — a Codex session error, **not** an auth failure: it must never classify as `NEEDS_LOGIN`.

**Steps:**
1. Record real outputs as fixtures under `tests/fixtures/cli/<cli>/` (stdout, stderr, exit code, one README line each). At most
   six live runs, each one short prompt, no others:
   - Claude success: `CLAUDE_CONFIG_DIR=D:/farm-data/ai/claude-02 claude -p "Reply with exactly: PONG" --model haiku --output-format json`
   - Claude success whose answer contains every old trigger word: same command with the prompt
     `Reply with exactly this sentence: The rate limit is 429, capacity is low, it resets in 5 hours, please log in with /login, error 401 and 403 unauthorized, quota exceeded, overloaded.`
   - Claude not logged in: the PONG prompt with `CLAUDE_CONFIG_DIR` pointing to a new empty temp folder (delete it afterwards).
     If the CLI waits for interactive input instead of failing, stop it and record that fact instead.
   - agy success and agy trigger-word success: `agy -p "<prompt>" --model gemini-3.8-flash-high --output-format json` with the two
     prompts above.
   Session ids in fixtures are fine; never record anything from a `.env` file.
2. New `farm/executors/cli_agent/classify.py`: one classifier per CLI with this order — (a) structured success → ok;
   (b) structured failure → error kind from the structured error fields and stderr only, through a per-CLI table of exact
   phrases taken from the fixtures; (c) no structured output → exit code + stderr only; (d) anything unrecognised →
   `ErrorKind.UNKNOWN`. The answer text is never scanned for error words. Keep reset-time parsing ("Resets in 91h35m7s" must
   yield the right timestamp).
3. Rewire `claude.py`, `codex.py`, `agy.py`, `gemini_cli.py`, `hermes.py` to use it; delete the old regex blocks; keep their
   public behaviour (ExecResult fields, usage and cost parsing) unchanged.
4. Tests (`tests/test_q0b_*.py`): every fixture classifies correctly; a Hypothesis property test that any structured success —
   including answers made of all the old trigger words — classifies as success for every CLI; reset-time parsing; the Codex
   rollout error is not `NEEDS_LOGIN`.
5. `uv add --dev hypothesis`.
6. Run `powershell -NoProfile -ExecutionPolicy Bypass -File scripts/check.ps1`.

**Owns:** `farm/executors/cli_agent/classify.py` (new) · `farm/executors/cli_agent/{claude,codex,agy,gemini_cli,hermes}.py` ·
`farm/executors/cli_agent/base.py` (only to move a shared helper) · `tests/fixtures/cli/**` (new) · `tests/test_q0b_*.py` (new) ·
`tests/test_cli_agent_*.py` (only where a test asserted the old, wrong behaviour — list each change in your reply) ·
`pyproject.toml` + `uv.lock` (hypothesis only).

**Must not touch:** `farm/resources/**`, `farm/ai/**`, `farm/gateway/**`, `farm/mcp/**`, migrations, `config/*`, `console/`,
any other file.

**Done when:** `check.ps1` ends with `RESULT: all passed`, and the false-flag fixture tests fail on the old code and pass on the
new (say how you showed it).

**Reply (15 lines or fewer):** the rule order you implemented · files changed · fixtures recorded (and any you could not
record) · `check.ps1` tail · open issues.
