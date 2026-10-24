# Recent changes

Newest first. One entry per working session or merged round: what changed, why, and what is not done. Older notes from other
tools live in `updates/`. The plan and its status live in `.orchestrate/state.md`.

---

## 2026-10-24 (later) — Quality baseline: first merges, verified live

**Merged into `effort2/stage1-saas` (each behind the full gate: ruff, mypy, all tests, secret scan):**
- **Q0C** (`0b37430`) — read-only defect audit: 25 findings (1 critical, 10 major, 14 minor; 17 confirmed with reproductions)
  in `docs/audits/Q0-audit.md`. Docs only.
- **Q0B** (`df46565`) — one account-state classifier for all five AI CLIs: structured result first, error channels only, exact
  signatures from real recorded outputs. The old code scanned the AI's own answer for words like "403" or "capacity" and
  falsely flagged accounts. Proof: the old code fails 3 of 6 real-output replay tests; generated tests cover all five CLIs.
- **F3P** (`merge f3p`) — Linux operations scripts and systemd units by Hermes GLM-5, revised after review (7 defects), then two
  more secret-scan defects fixed by the lead (fake key literal; silent pass when git cannot list files). Proven: loud failure
  without git; 626 files scanned on a real clone.
- **Q0A** (`eebf230`) — "Unknown tool" root cause: FastMCP's catalog filter hid tools marked UI-only
  (`_meta.ui.visibility` without "model"); loose names that matched several tools now error instead of picking one; compact
  `search_tools` (≈2.8k characters for 10 hits instead of 161k for 25) + `describe_tool`.

**Gate:** `tests/quarantine.txt` holds four registered baseline test failures (D7). They still run and print, but do not block;
owners Q0D / Q0E. Base lint error (agy.py) fixed.

**Verified on the live Farm (restarted on the new code, PID 476392):**
- agy-01 (Gemini Pro) had a stale false "needs login" status from the old code; resumed through an audited `resume` command;
  `farm ai test agy-01` → PONG.
- `call_tool("clay__get-credits-available")` → answers (`hasWorkspaceCredits: true`).

**New register entries:** D34 `client_tokens` created outside migrations (→ F1B); D35 silent-pass secret scan (fixed); D36 tool
risk labels trust server hints — `clay__search-contacts-by-name` shows read-only but can spend credits (→ A2 overrides).

**Waiting:** UI kit (A3A) Round 0 — owner's review of the gallery screenshots. F1A (tenancy schema) merge next, then a backed-up
live database migration.

---

## 2026-10-24 — Effort 2 starts: SaaS foundation → Stage A → Tensor

**Branch:** `effort2/stage1-saas`, created from `claude/awesome-archimedes-vb1t7y` (3db3d81, which still had 8 unpushed commits).

**Design written (binding for every brief):**
- `docs/ARCHITECTURE.md` — multi-tenant SaaS platform: tenancy and two-layer isolation, process roles, queue and leases,
  sandboxes, vault, storage, cells, technology stack, engineering standards, 21 decisions (ADRs).
- `docs/AGENTS.md` — the Farm as the operating system for AI work: what an agent needs and what the Farm provides, delegation
  modes, runtimes, agent selection, token-efficiency levers, memory, cost accounting, the operator-permission fix.
- `docs/PRODUCT.md` v2 — the dashboard from the user's jobs; patterns learned from OpenRouter, Trigger.dev, Langfuse, Obot,
  MCPJungle, Linear and Vercel.
- `docs/design/DASHBOARD-COMPONENTS.md` + `docs/design/reference-dashboard.png` — the owner's visual reference turned into tokens,
  components and page layouts.
- `docs/QUALITY.md` — defect register and the quality baseline (Q0) that comes before any new building.
- `briefs/CONTEXT.md` — builder context now says multi-tenant SaaS.

**Code and tooling:**
- `farm/control/commands.py` — `test_connection` timeout 10 s → 120 s (Codex through WSL starts slowly). Found uncommitted from
  the 2026-10-15 session; committed as is.
- `scripts/round.ps1` — can drive Claude pool accounts (`-Builder claude`, default account claude-02, Sonnet), follows the
  current branch (`-Main`), and can run in the foreground (`-Foreground`) so the operator is notified when a round ends.

**Repository hygiene:**
- `config/registry.yaml` had the owner's live account list (13 email addresses) written over the generic seed file. It is kept
  out of commits with `git update-index --skip-worktree`; the owner's live registry belongs in the git-ignored
  `config/registry.owner.yaml`. `config/*.bak-*` is now ignored.

**Findings:**
- Builder check: Claude account 2 on Sonnet 5.5 works; Antigravity (Gemini Pro account) works even though the Farm lists it as
  "needs login" — confirms defect D1. Hermes GLM-5 works through the **default profile with `--provider custom:bedrock-mantle`**
  (owner's correction); only the old `farm-builder-bedrock` profile had an expired token. `scripts/run-hermes.ps1` and
  `scripts/round.ps1` now use that route for GLM-5 by default.
- D1 root cause (in code): the CLI drivers scan the AI's own answer for error words before checking structured success.

**Wave 1 (running):** Q0A tool lookup and compact search · Q0B account-state classifier · Q0C read-only defect audit · F1A
tenancy schema — all Claude account 2 (Sonnet); A3A dashboard UI kit — Antigravity (Gemini 3.8); F3P Linux operations
scripts and systemd units — Hermes GLM-5.

**Not done yet:** everything after wave 1 (see the plan); the live database has not been migrated; no deploy.
