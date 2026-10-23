# Recent changes

Newest first. One entry per working session or merged round: what changed, why, and what is not done. Older notes from other
tools live in `updates/`. The plan and its status live in `.orchestrate/state.md`.

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
  "needs login" — confirms defect D1; Hermes with GLM-5 on Bedrock fails because the AWS security token has expired (owner to
  refresh); Hermes through Bifrost (DeepSeek V4 Flash) is available.
- D1 root cause (in code): the CLI drivers scan the AI's own answer for error words before checking structured success.

**Wave 1 (running):** Q0A tool lookup and compact search · Q0B account-state classifier · Q0C read-only defect audit · F1A
tenancy schema — all Claude account 2 (Sonnet); A3A dashboard UI kit — Antigravity (Gemini 3.8).

**Not done yet:** everything after wave 1 (see the plan); the live database has not been migrated; no deploy.
