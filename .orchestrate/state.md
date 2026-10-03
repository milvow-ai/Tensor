# Orchestrate state

## Current state
Effort: Harness Farm + Farm Console (see HANDOFF.md). Phase: **0 — pre-setup DONE 2026-10-04 except user blockers; plan v2 written; waiting for "go"**.
Next action: on "go" → start Bifrost (`scripts/start-bifrost.ps1`), write briefs M1a–M1d, run M1.

## Phase 0 findings (2026-10-04, PC: Windows 11, 15.3 GB RAM)
- Repo cloned from bundle to `C:\Users\Amaan\Harness Farm\Tensor`. Push as GitHub user `Mr-amaanx` → 403 (no write access to milvow-ai/Tensor).
- Disk: C: 4.3 GB free (too little), D: 95.9 GB free.
- Present: git, python 3.12, uv (from hermes\bin), node, pnpm, docker, wsl, hermes (HERMES_HOME=%LOCALAPPDATA%\hermes), claude, **agy** (Antigravity CLI binary). Missing: gemini CLI, supabase CLI, go.
- mem0: 41 memories from memory/mem0-import.jsonl imported (infer=false) + 1 new constraint memory.

- 2026-10-04: repo moved to `D:\Harness Farm\Tensor` (C: copy left, can be deleted). Caches on D: via user env: PLAYWRIGHT_BROWSERS_PATH, UV_CACHE_DIR, UV_PYTHON_INSTALL_DIR, npm_config_cache = `D:\dev-cache\*`; pnpm store-dir `D:\dev-cache\pnpm-store`.
- Versions: git 2.51, Python 3.12.5, uv 0.12.17, node 22.19, pnpm 9.12, docker 29.6.2 (daemon did NOT start), hermes 0.21.3 (upstream afaa53e5; library pin c8301ea6), agy 1.2.16, claude 2.1.268.
- Scaffold (§8.2.5): `uv run pytest` 1 passed, ruff + mypy clean (Gemini, 318k tokens, 246 s); `console/` = starter @1888b20, npm lock → pnpm-lock via `pnpm import`, `pnpm build` PASS (Next 16, React 19). `farm db check` waits for SUPABASE_DB_URL in .env. pgserver PG 16.2 smoke OK (data D:/farm-data/pgtest).
- Library: 25 repos at pinned commits (`bash scripts/fetch-library.sh` → 25× ok).
- Supabase: project `harness-farm`, ref `aftetufrpjgghxgnnowq`, ap-south-1, Postgres 17.11, $0/mo. User must put SUPABASE_URL / SUPABASE_SERVICE_ROLE_KEY / SUPABASE_DB_URL in `.env`.
- Hermes default profile = Bedrock (Kimi K2). **Every Farm Hermes call must use `-p farm-builder|farm-agent`** (pinned to OpenRouter).

## Verified CLI flags (2026-10-04)
- **Gemini (bulk):** `agy -p "<prompt>" --model gemini-3.8-flash-high --dangerously-skip-permissions --output-format json --print-timeout 2400s` run from the repo root. JSON has `status`, `response`, `usage`. Launcher: `D:\dev-cache\run-agy.ps1 -Brief <path> -Out <json>` (sets D: caches). No Gemini 3.8 Pro in agy; 3.1-pro-high exists.
- **Hermes (mechanical/agentic):** `hermes -p farm-builder -z "<brief>" --provider openrouter -m <model> --usage-file <path>`, launched with working dir = target dir AND `TERMINAL_CWD=<dir>` (the `--in` flag does not move tool cwd; without this Hermes writes to the home dir). Usage file has `total_including_auxiliary.estimated_cost_usd`.
- **Claude CLI:** `claude -p "<prompt>" --output-format json` → FAILED: "OAuth session expired". User must re-login in a terminal (`claude` → /login).
- **Bifrost:** `npx -y @maximhq/bifrost -app-dir D:\dev-cache\bifrost -port 8080 -host 127.0.0.1` via `D:\dev-cache\run-bifrost.ps1` (injects OPENROUTER_API_KEY from Hermes .env without printing). Config `D:\dev-cache\bifrost\config.json`: openrouter key `env.OPENROUTER_API_KEY`, `source_of_truth: config.json`. VKs: `vk-farm-p0` $0.05 (smoke), `vk-hermes-builder` $1.50/1Y, `vk-hermes-agent` $0.50/1Y (values generated, only in config.json + Hermes profile .env as BIFROST_VK). Tests: VK → OK; no VK → 401; model outside VK → 403; Hermes farm-builder through Bifrost → OK $0.00034, Haiku → 403. Write config.json WITHOUT BOM (PowerShell 5 utf8 adds one; Bifrost then fails to start). Repo launchers: `scripts/start-bifrost.ps1`, `scripts/run-hermes.ps1` (pins TERMINAL_CWD; tested: file landed in cwd, $0.0005), `scripts/run-agy.ps1`, gate `scripts/check.ps1`.

## Hermes profiles (2026-10-04, `hermes -p <p> config get …` verified)
| Profile | Model | Toolsets | Terminal | Caps | Other |
|---|---|---|---|---|---|
| farm-builder | bifrost → openrouter/deepseek/deepseek-v4-flash (VK $1.50) | terminal, file, code_execution, todo | local (cwd pinned per run) | 60 turns, 1800 s | keyless off, memory off, aux = v4-flash, .env = BIFROST_VK only (no provider key), no MCP |
| farm-agent | same, via Bifrost VK $0.50 | todo (+ Farm MCP at M4, sampling off) — no terminal | n/a | 25 turns, 600 s | same |

## Model eval (2026-10-04, same Farm-util coding task, hidden 15-case check)
v4-flash 15/15 $0.0016 · v4-pro 15/15 $0.0084 · qwen3.8-27b:free 15/15 $0 (429s when run in parallel) · nemotron-3-super:free 15/15 $0 · cohere/north-mini-code:free 14/15 (missed Feb clamp; 16 calls). Pinned-cwd rerun left 0 stray files.
Routing: free model first → v4-flash fallback → v4-pro for important/judgment steps. OpenRouter spend so far ≈ $0.02 of $3.

## Active workers
| Worker | Agent | Model | Owns | Status | Output file |
|---|---|---|---|---|---|
| (none yet) | | | | | |

## Plan — Phase 1 (Farm) + Phase 2 (Console)  [v2 2026-10-04 after 2-lens review; waiting for user "go"]

Owners: **G** = Gemini 3.8 Flash High via `scripts/run-agy.ps1` (bulk code; quota fallback → gemini-3.1-pro-high → lead writes small pieces) · **H** = Hermes `farm-builder` via `scripts/run-hermes.ps1` (Bifrost VK $1.50 cap; v4-flash default, free models one at a time, v4-pro for judgment) — fixtures, docs, repetitive edits · **S** = `scripts/check.ps1` (ruff, mypy, pytest, secret grep, expected files; zero tokens) · **WC** = worker-check (Sonnet), once per milestone · **L** = lead (briefs, diff review, commit, push, state).
Round = brief → G/H → S (fix loop ≤ 2) → WC at milestone end → L reviews `git diff --stat` + key hunks → commit → push → state + token/cost tally (`D:\dev-cache\runs\ledger.tsv`).
Tests never spend credits: respx/vcrpy, network blocked in pytest. Exception: once keys exist, L records ONE happy-path cassette per adapter from a free-tier call (redacted) — fault cases stay doc-derived. Test DB = `pgserver` (PG 16, data in FARM_DATA_DIR=D:/farm-data); runtime DB = Supabase `harness-farm` (PG 17) — SQL kept PG16-compatible.

| # | Scope | Briefs (owner) | Done when (S, then WC) | Box |
|---|---|---|---|---|
| M1 | Alembic schema (§4.5 core, workspace_id, reserve/commit/release from data-model-v0.sql) · registry YAML→Pydantic→JSON Schema · FastMCP gateway + full middleware chain auth → policy → cache → single-flight → trajectory · quota ledger · `verify_email` Reoon→ZeroBounce · adapter template | M1a schema+ledger (G) · M1b registry (G) · M1c gateway+router+adapters (G) · M1d fault fixtures (H) | pytest green; recorded test: Reoon exhausted → ZeroBounce, reservation released, trajectory row; **N concurrent identical calls → 1 provider call, 1 reservation, N equal results**; `fastmcp.Client` stdio script calls `verify_email` (S); migration applies to Supabase; one manual Claude MCP call after CLI re-login | 1 d |
| M2 | health/circuit, cooldowns, backoff+jitter, timeouts, DBOS queue per connection **with executor id**, ranking, strategies failover/most_remaining/round_robin/pin; adapters Apollo, Hunter, PageSpeed, Adzuna, LLM via Bifrost (route: Groq free → OpenRouter free → v4-flash; Ollama/Bedrock off) | M2a health+ranking+strategies (G) · M2b DBOS queues (G) · M2c 5 adapters (G) · M2d fault cassettes (H) | fault suite 429/401/empty/timeout green; ranking tests; **bulkhead: saturating A does not delay B; open circuit on A routes to B**; **kill worker mid-workflow → restart same executor id → resumes, no double charge**; 0 live calls | 1–1.5 d |
| M3 | pool of N conns/provider, parallel_split/sticky/fit_check, re-queue + idempotency, Clay via FastMCP proxy + per-conn OAuth token store (**Clay spike first**), balance sync = API + manual entry + estimated/actual ledger (Hermes dashboard read → M4), pacing, budgets + hard stop, renewals, Telegram, `farm` CLI incl. `farm set-secret` | M3a pool+re-queue (G) · M3b account manager+budgets (G) · M3c Clay proxy+token store (G) · M3d CLI+Telegram (H) | 7-account Clay sim: 70 items, kill 1 account mid-batch → done, 0 double charges; cap blocks paid calls; reminder fires in time-travel test; 1 API balance snapshot + 1 manual entry with estimated/actual split; `set-secret` writes .env without echo | 1.5 d |
| M4 | facts/freshness, entity keys, Crawl4AI evidence (screenshot, HTML, sha256, robots) in FARM_DATA_DIR **+ thumbnail → Supabase Storage**, recipes as DBOS workflows, Hermes `agent_task` (farm-agent: Farm MCP only, no terminal, Bifrost VK $0.50, sampling off) + submit_* Pydantic, policy, Hermes balance read | M4a knowledge+evidence+thumbs (G) · M4b recipes (G) · M4c Hermes executor (G; L reviews policy) | real `research_company` on 1 public site → facts with evidence ids + resolvable thumbnail path; 2nd call = cache hit, cost 0; Hermes run validated, profile dump = Farm MCP only, cost on its VK; blocked tool call logged | 1–1.5 d |
| M5 | `farm doctor` (incl. free disk ≥ 2 GB on C: and D:), pg_dump backup, Windows run guide, `docs/FARM-OPERATIONS.md`, farm_commands consumer for **pause, resume, priority, budget edit, add connection** + validation | M5a doctor+backup+consumer (G) · M5b docs (H) | `farm doctor` all green on this PC; one executed + acked row per command type; one invalid row rejected | 0.5–1 d |
| C1 | Console: starter, Farm branding (dark sidebar, green accent), Supabase Auth magic link owner-only + RLS, **Overview** (capacity, spend vs budget + forecast, alerts, health, capability→resource map) + **Accounts** (meters, pause/resume, priority, strategy → farm_commands); bundle-size check | C1a shell+auth (G) · C1b SQL views (G) · C1c Overview+Accounts (G) | Playwright screenshots desktop + phone; a toggle pauses a real connection via the queue; bundle-size check green | 1–1.5 d |
| C2 | **Billing** + **Runs** (trajectory viewer, fallbacks, evidence thumbnails from Storage) | C2a Billing (G) · C2b Runs (G) · C2c forecast tests (H) | numbers = SQL views; forecast unit-tested; a Runs row renders its Storage thumbnail | 1 d |
| C3 | **Routing**, **Memory & evidence**, **Integrations** (RJSF from registry JSON Schema + Test connection), **Policies**; deploy Vercel Hobby | C3a–d per screen (G) · deploy (L, user OK) | live on Vercel behind login; Lighthouse ≥ 90 perf + a11y; no secrets in build (grep); budget edit + add-connection round-trip through queue; every table paginated; Realtime only on Accounts/Runs (grep) | 1–1.5 d |

Budget (actuals per milestone): Claude lead ≤ 1.5M Phase 1, ≤ 1M Phase 2 (Pro; one WC ≤ 80k per milestone) · Gemini ~6–10M / 4–6M (subscription) · OpenRouter hard-capped by Bifrost VKs: builder $1.50, agent $0.50 (≈ $0.02 used in Phase 0) · provider credits $0 except free-tier cassette calls.
Stop rule: milestone > 150% of box → stop and report why.
Deviations from HANDOFF (recorded): extract/classify route Groq → OpenRouter free → v4-flash (Bedrock banned by user 2026-10-04; Ollama off for C: space); Hermes models via Bifrost VK instead of Bedrock; farm-agent has no terminal so Docker is off the critical path.

Blockers (user):
1. GitHub push 403 (git user Mr-amaanx has no write on milvow-ai/Tensor) — needed before the first push.
2. `claude` CLI login expired — re-login (`claude` → /login) before M1's manual MCP check.
3. Fill `D:\Harness Farm\Tensor\.env` (created, names only): SUPABASE_URL / SUPABASE_SERVICE_ROLE_KEY / SUPABASE_DB_URL before M1; REOON/ZEROBOUNCE before M1 cassette; the rest by M2.
4. Account inventory (7 Clay accounts: plan, API vs MCP) before M3.

## Contract
- Design decisions in HANDOFF.md §3–§5 are fixed; do not re-design.
- No spend, no account creation, no building before the user's "go".
- Keys only in the user's local .env; agents never see them.
- One writer per file; builders never commit; lead commits after checks pass.

## Decisions log
- 2026-10-03: Build order = Harness Farm → Farm Console → (later) Tensor — user decision.
- 2026-10-03: Stack = FastMCP 4 + DBOS + Postgres (Supabase) + Crawl4AI + Bifrost; Console = Next.js + shadcn starter on Vercel, controlled via Supabase command queue — previous session design.
- 2026-10-03: Builders = Gemini 3.8 (Antigravity CLI) bulk, Qwen via Hermes mechanical, Sonnet checks — user decision for token efficiency.
- 2026-10-03: Multi-account pools (e.g. 7 paid Clay accounts) are in scope; user owns them — user decision.
- 2026-10-04: Claude plan = Pro, weekly limit (53% left today) → lead stays lean — user.
- 2026-10-04: Gemini = Antigravity CLI (`agy`), separate Google account with Gemini Pro — user.
- 2026-10-04: Antigravity model = gemini-3.8-flash-high (no 3.8 Pro exists in agy) — user said "3.8 high".
- 2026-10-04: Hermes models go through Bifrost virtual keys (builder $1.50, agent $0.50); Hermes profiles hold no provider key — enforces the $3 cap and HANDOFF §4.7 — lead, after plan review.
- 2026-10-04: Supabase project `harness-farm` created (ap-south-1, $0) — user approved.
- 2026-10-04: Vercel = user's Hobby account; Console must be a real SaaS-style dashboard to see and adjust the Farm (not an artifact) — user.
- 2026-10-04: Test Postgres = pgserver (no Docker); Docker Desktop off the critical path — lead.
- 2026-10-04: **No AWS Bedrock** (AWS warning pending). Hermes uses OpenRouter free models; a very cheap paid model (DeepSeek-class) only for important steps; OpenRouter balance $3 total — user. Replaces §2/§7 "Qwen3-Coder-Next via Bedrock".

## Previous efforts
See .orchestrate/history-2026-10-02-03.md (research + design rounds).
