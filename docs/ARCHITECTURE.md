# Harness Farm — SaaS architecture (binding)

Owner decision 2026-10-24: Harness Farm is built as a **multi-tenant SaaS platform from the foundation**, deployed first for
Milvow alone, then for 10 tenants, and grown to 100, 1,000, 10,000 and 100,000 tenants **by adding servers and cells, never by
redesigning**. Stage A (`do(task)` delegation) is built on this foundation; Tensor is the first app on top of it.

This file is the contract for every brief. If a brief conflicts with it, this file wins; if this file is wrong, the lead changes
it first (dated, with the reason) and only then the code. Companions: `docs/AGENTS.md` (agents, delegation, token efficiency,
memory, cost accounting) and `docs/PRODUCT.md` (user journey and dashboard).

Status labels used below: **[exists]** in the code today · **[change]** exists but must change · **[new]** to build.

---

## 1. Product in one line

> Connect every AI, account and tool once. Use one MCP from any AI app. You think; the Farm does the work — it picks the worker,
> tool and account, runs the task, controls cost, and keeps memory and results — separately and privately for every tenant.

## 2. Principles

1. **Design for 100k tenants, deploy for one.** Decisions that are cheap now and avoid a rewrite later are made now (tenant id on
   every row, cell id, process roles, ports). Expensive infrastructure (load balancers, orchestrators, extra regions) is added
   only when a gate's measured problem asks for it.
2. **Isolation by construction, twice.** Every query carries a tenant context in code, *and* Postgres row-level security
   enforces it again. A bug in one layer must not leak data.
3. **Ports and adapters.** Queue, storage, sandbox, vault, auth and billing are interfaces. Vendor SDKs live only inside adapters,
   so any of them can be swapped without touching the core.
4. **Stateless control plane.** Any API node can serve any request. State lives in Postgres and object storage only.
5. **The tenant owns its accounts, keys and data.** The Farm manages them; it never resells them. API-key connections are the
   first-class path; CLI subscription logins are supported, one session per account.
6. **Humans approve risk.** Tools that send, delete, publish or spend pause the task for approval unless the tenant's policy
   allows them.
7. **One core, many apps.** The Farm stays general. Tensor and later apps use it through the same MCP/API as any tenant.
8. **Everything observable, per tenant.** Every decision carries `tenant_id`, `task_id` and `run_id` in logs, traces and rows.

## 3. System overview

```
             Any AI app (Claude, Codex, Cursor…)   Console (web)   Apps (Tensor)
                          │ MCP / HTTPS + token        │ HTTPS          │ MCP/API + app token
                          ▼                            ▼                ▼
  ┌──────────────────────────────── CELL  (one now; add cells to grow) ───────────────────────────────┐
  │  CONTROL PLANE (stateless, scale out)                                                              │
  │   api      : MCP gateway + REST · auth → tenant context → policy → tools → router / task layer     │
  │   consumer : owner commands (farm_commands), claimed with row locks                                │
  │   scheduler: maintenance, ramps, renewals, balance sync — one active leader (advisory lock)        │
  │                                                                                                    │
  │  EXECUTION PLANE (scale out)                                                                       │
  │   worker   : claims jobs from the queue → leases account + tenant slots → runs a SANDBOX per task:  │
  │              tenant's account (API key or decrypted CLI home), task-scoped Farm token + tools,     │
  │              task files, resource limits                                                           │
  │   runner   : the same worker in "runner mode" on a tenant's own machine (local tools and logins),  │
  │              connecting OUT to the cell                                                            │
  │                                                                                                    │
  │  DATA                                                                                              │
  │   Postgres (tenant_id + RLS on every tenant row; monthly partitions for events)                    │
  │   Object storage (t/<tenant>/…: task files, evidence, encrypted CLI homes, exports)                │
  │   Vault (envelope encryption: master key → per-tenant data key → secrets)                          │
  └────────────────────────────────────────────────────────────────────────────────────────────────────┘
        │ outbound only
        ▼
  AI providers (APIs / CLIs) · MCP servers (remote HTTP + OAuth, or stdio inside sandboxes) · data APIs · mail
```

Today **[exists]** `farm run` composes the gateway, command consumer, AI job manager, heartbeat, scheduler and catalog refresh in
one process on the owner's Windows PC. That composition stays as **all-in-one mode** for development and single-host deploys;
the roles above are the same code started with different entry points (`farm api`, `farm worker`, `farm scheduler`,
`farm consumer`) **[change]**.

## 4. Tenancy and identity

| Concept | Meaning | Status |
|---|---|---|
| **tenant** | A customer organisation (Milvow is tenant #1). Has `cell_id`, plan, limits, data key | **[new]** (the schema has `workspace_id` with one default value) |
| **user** | A person; belongs to tenants through memberships | **[new]** |
| **membership** | user × tenant × role (`owner`, `admin`, `member`) | **[new]** |
| **API token** | Per user per tenant for MCP clients; stored hashed, shown once, prefix `hf_` | **[change]** client tokens exist in the DB (`farm token create`); bind them to tenant + user + scopes |
| **task token** | Short-lived, scoped to one task: allowed tools, budget, files, expiry; given to sandboxed workers | **[new]** |
| **app token** | For apps on the platform (Tensor) acting inside a tenant | **[new]** |

Rules:
- **`workspace_id` becomes `tenant_id`** (rename or alias in one migration, expand/contract). Every tenant-owned table has it
  `NOT NULL`, with no default, as the first column of its main indexes. Platform tables (plans, cells, migrations) do not.
- **Tenant context** is resolved once per request from the token at the entry point (gateway auth, job runner, command
  consumer, scheduler loop) and carried in a `ContextVar`. *(Revised 2026-10-24, lead: threading a typed parameter through
  ~25k lines is weeks of churn for no extra safety.)* Safety comes from three fail-closed layers instead:
  1. the database checkout (`tenant_connection()`) **refuses** to hand out a connection when no tenant is set, and sets
     `app.tenant_id` on it (reset on return);
  2. every tenant-owned column defaults to `current_setting('app.tenant_id', true)::uuid` — an insert without a tenant fails
     the NOT NULL constraint instead of landing in another tenant;
  3. RLS policies `USING / WITH CHECK (tenant_id = current_setting('app.tenant_id', true)::uuid)` for the runtime role.
  Cross-tenant maintenance (expiring reservations, sweeps, reactivation) runs as a separate `farm_system` role with
  `BYPASSRLS`, only inside functions explicitly marked as platform maintenance.
- **Views** read by the Console are `security_invoker = on`, so RLS applies to the person reading them (a view owned by the
  migration role would otherwise bypass RLS and show every tenant).
- **Database roles** **[change]**: today the Farm connects as the database owner and bypasses RLS. Target: `farm_admin`
  (migrations only) and `farm_app` (runtime, RLS enforced). Every transaction runs `SET LOCAL app.tenant_id = …`; RLS policies
  compare against it. Console users reach rows through RLS on their memberships.
- **Isolation test suite** **[new]**: two tenants with identical data; every MCP tool, REST route, Console view and command kind
  is exercised as tenant A against tenant B's ids and must fail closed. Runs in CI on every change that touches data access.
- **`cell_id`** on tenants from day one; one cell exists. Moving a tenant between cells is a later, explicit operation.

## 5. Request paths

### 5.1 Tool call (existing behaviour, made tenant-aware)
`MCP request → auth (token → tenant, user, scopes) → policy (may this caller use this tool?) → tool → router` → the existing
router flow (cache → single-flight → eligibility → strategy → budget stop → reserve → execute → commit/release → health →
fallback → envelope) **[exists]**, with every lookup, ledger row, cache key and run row scoped to the tenant **[change]**.
Connections may additionally carry `scope: [client:<id>]` for apps that serve sub-clients (Tensor mailboxes) **[exists]**.

### 5.2 `do(task)` — the Stage A path **[new]**
```
do(task, files?, budget?, approval_policy?)
  → task row (queued) → planner: choose worker type, model, tool allow-list, budget, files
  → job enqueued → worker claims it → leases: account session + tenant concurrency slot
  → sandbox: tenant account + task token (Farm MCP with only the allowed tools) + task files
  → worker agent works; every tool call goes back through the Farm (policy, budget, audit)
  → risky tool → task pauses (waiting_approval) → owner approves in Console/Telegram → resumes
  → result + files + cost → task done; caller reads task_status / task_result / task_reply
```
- **Delegation depth is 1**: a task token can never call `do` (no recursive delegation).
- **Workers are existing agent CLIs and APIs** (Claude Code, Codex, Gemini, Hermes) — the Farm does not build its own agent
  loop; it chooses, equips, limits and watches them. Today workers run with no tools (`--allowedTools ""`, `--toolsets ""`) or
  file edits only **[change]**.
- The planner is an AI step with a fixed JSON schema plus deterministic rules (capacity, budget, tenant policy). Its plan is
  stored with the task and visible in the Console.

### 5.3 Owner command (existing, made tenant-aware)
Console → `farm_commands` row (tenant, user) → consumer claims it (`FOR UPDATE SKIP LOCKED`) → validate (kind, schema, role,
no raw secrets) → apply + `audit_events` → status **[exists]** · tenant + role checks **[change]**.

## 6. Execution plane

| Piece | Design | Status |
|---|---|---|
| **Queue** | `QueuePort`. Implementation now: Postgres jobs table, claim with `FOR UPDATE SKIP LOCKED`, lease + heartbeat (the `ai_jobs` pattern generalised). Upgrade path: pgmq / DBOS / NATS behind the same port when measured load needs it | `ai_jobs` **[exists]**; general queue **[change]** |
| **Global limits** | `leases` table: `(resource_key, holder, expires_at)`; used for per-account `max_parallel` (one session per CLI login), per-tenant concurrency, per-pool limits, scheduler leadership. Renewed by heartbeat; expired leases are reclaimed | **[new]** — today limits are per process ("connections.concurrency is enforced per Farm process", `farm/ai/jobs.py`) |
| **Sandbox** | `SandboxPort` with `LocalProcessSandbox` (dev, Windows, runner) and `DockerSandbox` (hosted Linux). One sandbox per task: own workspace, CPU/memory/time limits, no host filesystem, only the task's secrets. Stronger isolation (gVisor/Firecracker) behind the same port later | **[new]** — today: subprocesses on the host, edit roots on `D:/` |
| **Accounts as assets** | API keys in the vault. CLI logins stored as **encrypted home bundles** in object storage; a worker checks one out under a lease, runs, writes refreshed tokens back, releases | **[new]** — today homes are folders on one machine |
| **Tenant MCP servers** | Remote HTTP + OAuth: per tenant connection with its own token store **[exists, change to tenant scope]**. Stdio servers: run only inside the tenant's sandbox **[new]** |
| **Runner mode** | `farm worker --runner` on a tenant's machine: connects out to the cell, receives only that tenant's jobs, runs local tools and logins. Not needed for Gate A; built when hosted tenants need local tools | **[new, later]** |

## 7. Data and storage

- **Postgres** is the system of record. Plain Postgres features only in the core (RLS, partitions, advisory locks, SKIP LOCKED);
  Supabase-specific features stay in the Console adapter. High-volume tables (`run_events`, `usage_events`, task step events)
  are **partitioned by month** with retention per plan **[new]**.
- **Object storage** behind `StoragePort`: local disk (dev) and any S3-compatible service (prod). Keys `t/<tenant_id>/…`;
  access through short-lived signed URLs; per-tenant storage quota **[new]**.
- **Vault** behind `VaultPort`: per-tenant data key (AES-256-GCM) wrapped by a master key from the environment now, a KMS later.
  `auth_ref` gains `vault:<name>`; values are decrypted only inside the executor or sandbox that needs them and never logged,
  returned or stored in plain text **[new]**. Existing `env:` / `token-store:` / `cli:` refs stay for the platform tenant
  **[exists]**.
- **Memory** per tenant: `memory_items` (tenant, scope = tenant/user/project, text, tags, source, created_by), tools
  `memory_add` / `memory_search` / `memory_forget`; vector search (pgvector) added when keyword search is not enough. The planner
  injects relevant memory into a worker's brief **[new]**.
- **Registry** per tenant in the database; YAML becomes import/export per tenant (`farm registry sync --tenant …`) **[change]**.
- **Export and deletion** per tenant (data portability, offboarding) **[new]**.

## 8. Tool surface

- **Compact by default**: `do`, `task_status`, `task_result`, `task_reply`, `task_cancel`, `files_*`, `memory_*`, `farm_guide`,
  `search_tools` (names + one line each), `describe_tool` (full schema on demand), `call_tool`. Direct listing of a tenant's
  favourite tools is a per-tenant setting **[change]** — today one search returned 161,000 characters for 25 tools.
- **Per-tenant catalog**: the tool list is built per request from the tenant's connections, not once at startup **[change]**.
- **Tool risk labels** come from MCP annotations plus the Farm's own labels (read-only / changes data / may spend) **[exists]**
  and drive the approval policy **[new]**.

## 9. Security

- Threat model priorities: cross-tenant data access; secret exposure; a sandboxed worker escaping or reaching another tenant;
  token theft; prompt injection through tool results driving risky actions.
- Controls: two-layer isolation (§4); vault (§7); sandbox per task with no host filesystem (§6); task tokens with allow-lists
  and expiry; approvals for risky tools (§8); rate limits per tenant and per token; audit log of every command, approval and
  policy decision; secret scanning and dependency scanning in CI; admin access to servers only over a private network.
- Existing protections stay: env allow-list for child processes, redaction, id validation, confinement (SEC1) **[exists]**.

## 10. Observability and operations

- Logs: structlog JSON with `tenant_id`, `user_id`, `task_id`, `run_id` **[change]**. Traces: OpenTelemetry across api → queue →
  worker → tools **[new]**. Metrics endpoint per role (queue depth, lease waits, task latency, errors, cost) **[new]**. AI calls
  traced in Langfuse; errors in Sentry (both connected today as MCP servers; SDK integration **[new]**).
- Delivery: one container image, role chosen by command; Compose for a single host; infrastructure as code later. CI on
  **Ubuntu and Windows** (lint, mypy, tests, isolation suite, Console checks) **[new]** — today there is no CI and tests have only
  run on Windows. Windows stays supported for development and runner mode; Linux is the production target.
- Migrations: Alembic, raw SQL, **expand → migrate → contract** so a running cell is never broken by a deploy.
- Backups: nightly database dump + object storage versioning; a restore drill before every gate **[change]**.

## 11. Scaling path

| Tenants | What we add | What does not change |
|---|---|---|
| 1 (Milvow, Gate A) | All-in-one on the owner's PC or one Linux host | The architecture |
| 10 | One Linux host (api + worker roles) + Postgres + object storage | Same |
| 100 | Separate DB host; 2+ api nodes behind a load balancer; 3–5 workers; autoscale workers by queue depth | Same |
| 1,000 | Managed Postgres + connection pooler + read replica; container scheduler for sandboxes; CDN for the Console | Same |
| 10,000 | Queue service behind `QueuePort`; worker pools per region and provider; edge rate limiting | Same |
| 100,000 | More cells; tenant → cell routing | Same |

## 12. Apps on the platform: Tensor

Tensor (outbound engine, built from `D:\funding-engine` code — code only, never its client data) is a separate service that acts
inside a tenant with an **app token**. Each Tensor client is a tenant (or a `client:<id>` scope inside one); its client pack
(offers, proof, ICP, rules, voice, sources, mailboxes, region) is tenant data. Tensor uses Farm capabilities (Clay search and
contacts, email verification, website capture, mailbox pool `send_email` / `read_inbox`) through the same MCP/API as anyone,
so its load scales with the platform. Tensor's own tables carry `tenant_id` and RLS like the Farm's.

## 13. Ports and current implementations

| Port | Now | Later (when measured) |
|---|---|---|
| `QueuePort` | Postgres table + SKIP LOCKED + leases | pgmq / DBOS / NATS |
| `StoragePort` | Local disk (dev), S3-compatible (prod) | Same API, any provider |
| `SandboxPort` | Local process (dev/runner), Docker (hosted) | gVisor / Firecracker |
| `VaultPort` | Envelope encryption, master key from env | Cloud KMS |
| `AuthPort` | Supabase Auth for the Console; Farm tokens for MCP | Any OIDC provider |
| `BillingPort` | Usage meters only (no billing code) | Stripe / Razorpay |

## 14. Architecture decisions (ADR)

| # | Decision | Reason | Revisit when |
|---|---|---|---|
| 1 | Shared database, shared schema, `tenant_id` + RLS ("pool" model), grouped into cells | Schema-per-tenant does not scale to 100k; pool + cells does | A tenant needs dedicated infrastructure (enterprise plan) |
| 2 | Two-layer isolation: typed tenant context in code + RLS with a non-owner runtime role | One bug must not leak data | Never relaxed |
| 3 | Same code, four roles (`api`, `worker`, `scheduler`, `consumer`) + all-in-one mode | Scale each part independently; one codebase | — |
| 4 | Postgres queue + lease table before adding Redis or a queue service | One fewer moving part until load is measured | Queue latency or lock contention measured high |
| 5 | Workers are existing agent CLIs/APIs in sandboxes, equipped with scoped Farm tools; no home-grown agent loop | They are already strong agents; our value is choosing, equipping and governing them | A task type needs a custom loop |
| 6 | Delegation depth 1 | Prevents runaway recursion and cost | — |
| 7 | API keys are the first-class account type; CLI logins supported as encrypted home bundles, one session each | Clean scaling across servers | — |
| 8 | Envelope-encrypted vault per tenant | Tenant keys never readable in bulk | KMS available |
| 9 | Compact tool surface + per-tenant catalog | Large tool lists break the calling AI's context | — |
| 10 | Console keeps Supabase Auth + RLS views now; writes only through `farm_commands` | Already built and working; RLS already the boundary | We move off Supabase |
| 11 | Tensor is a separate service using the Farm through MCP/API with an app token | Keeps the Farm general and proves the platform | — |
| 12 | `cell_id` on tenants from day one | Adding cells later needs no data rewrite | — |
| 13 | People sign in with Google through Supabase Auth, behind `AuthPort` | Fastest path; RLS on Supabase users already exists | We leave Supabase or need SSO |
| 14 | Agent runtimes: CLI one-shot now; ACP sessions and API agents (Hermes as harness) after the S0 spikes | Reuse strong existing agents; prove before relying (`docs/AGENTS.md` §4, §12) | Spike results |
| 15 | The Farm's own task tools are the async interface; MCP Tasks added when the major clients support it | MCP Tasks (spec 2025-11-25) is experimental and moving to an extension | Claude Code / Codex support it |
| 16 | Memory is native (Postgres; vector search when keyword search is not enough) behind `MemoryPort`; mem0 only as an adapter | Tenant isolation under RLS; one vendor fewer | Retrieval quality measured too low |
| 17 | Every agent call is metered with token types and both actual and notional cost; versioned price table | The Cost & Efficiency feature needs exact attribution | — |
| 18 | The backend stays Python (async); a hot path moves to Go behind the same API only if profiling demands it | ~25k lines + tests exist; the work is I/O-bound | Profiling shows a CPU-bound gateway |
| 19 | The web app stays Next.js + TypeScript + shadcn/ui; the Console is restructured (`docs/PRODUCT.md` §5), not rewritten | ~25k lines and 90 browser tests exist | — |
| 20 | Hosting on Hetzner + Cloudflare (DNS, TLS, basic WAF) + Caddy; the dashboard runs on the same hosts instead of Vercel Hobby | Lowest cost; allowed for commercial use; one private network | Load or region needs |
| 21 | Object storage is S3-compatible; the provider (Cloudflare R2 or Hetzner Object Storage) is picked at F2 on current prices | Cost; no lock-in | — |

## 15. Gap from today to Gate A (maps to the milestones in `.orchestrate/state.md`)

| Gap | Milestone |
|---|---|
| Tool lookup bug (B1), oversized search results, wrong `last_error` display, unpushed work, no auto-start | R1 |
| Tenants, users, memberships, roles; `tenant_id` enforced; runtime role under RLS; tokens bound to tenant; isolation suite | F1 |
| Process roles; lease table; queue claim with SKIP LOCKED; scheduler leader; `cell_id`; `StoragePort`; vault; registry per tenant | F2 |
| `SandboxPort` (process + Docker); accounts as encrypted assets; API-key mode for every CLI; Linux run | F3 |
| Prove the runtimes: ACP sessions, API agents through Hermes, MCP injection into one-shot CLIs, live metering fields | S0 |
| `delegate` / `do` (single step), task and step model, context packs, task tokens with scoped Farm tools, depth guard, compact results, compact tool surface + per-tenant catalog, files, metering per agent and role | A1 |
| Team plans, scripted checks in sandboxes, patch merge, escalation and review diversity, project memory, approvals | A2 |
| Dashboard v2 (`docs/PRODUCT.md`): Google sign-in, onboarding, Home, Tasks, Agents, Tools, Projects & Memory, Cost & Efficiency, Approvals & Policies, Team & Settings, Admin | A3 |
| Ship: CI on Ubuntu + Windows, container images, Compose on Hetzner, domain + Cloudflare, traces/metrics, restore drill, beta invites | A4 |

## 16. Technology stack

| Layer | Choice | Status |
|---|---|---|
| Backend | Python 3.12, asyncio, `uv` | **[exists]** |
| MCP server | FastMCP 4 | **[exists]** |
| REST API (dashboard, apps) | FastAPI in the `api` role, next to the MCP endpoint | **[new]** |
| Contracts | Pydantic v2; JSON Schema feeds the dashboard forms (RJSF) | **[exists]** |
| Database | PostgreSQL 16/17 (Supabase now), psycopg 3 async, raw-SQL Alembic migrations; pgvector when needed | **[exists] / [change]** |
| Queue, leases, leader lock | PostgreSQL (`SKIP LOCKED`, lease rows, advisory locks) | **[new]** |
| Cache and rate limits | In-process + Postgres; Valkey (Redis-compatible) only when measured | **[new]** |
| LLM API gateway | Bifrost (virtual keys and budgets per tenant) | **[exists → change]** |
| Sandboxes | Docker Engine (rootless) on Linux; local processes on Windows; gVisor later | **[new]** |
| Agent runtimes | Claude Code, Codex, Gemini CLI / agy, Hermes; ACP adapters after S0 | **[exists] / [spike]** |
| Object storage | S3-compatible (ADR 21); MinIO for local development | **[new]** |
| Secrets | `cryptography` AES-256-GCM envelope encryption; master key from the environment, KMS later | **[new]** |
| Web app | Next.js (App Router), React, TypeScript strict, Tailwind, shadcn/ui, TanStack Query / Table, Recharts, RJSF | **[exists]** |
| Auth | Supabase Auth (Google) for people; hashed `hf_` tokens for MCP clients; task and app tokens | **[change]** |
| Observability | structlog JSON; OpenTelemetry → Prometheus / Loki / Tempo + Grafana (self-hosted or a free cloud tier); Sentry; Langfuse | **[change]** |
| CI/CD | GitHub Actions; one Docker image per release; deploy with Compose over the private network; manual approval for production | **[new]** |
| Infrastructure | Hetzner, Cloudflare, Caddy; OpenTofu once there is more than one host | **[new]** |
| Payments | Stripe / Razorpay behind `BillingPort` | **[later]** |

## 17. Engineering standards (every change)

- **Python:** ruff, mypy strict, pytest; failure paths tested (kill, exhaust, race); the tenant isolation suite on every change
  that touches data access; a coverage floor on the core (ledger, router, tenancy, vault, leases).
- **Web:** Biome, `tsc` strict, vitest, Playwright on the key flows, a Lighthouse budget on Home and Tasks.
- **Security:** gitleaks (secrets), pip-audit and pnpm audit (dependencies), Trivy (images); OWASP ASVS level 2 as the target
  for the web app.
- **Process:** one branch per milestone; checks green before merge; a fresh check worker plus the lead's review; an ADR for every
  architecture change; migrations expand → migrate → contract; runbooks for operations; SLOs measured; incidents written up.
- **CI on Ubuntu and Windows.**

## 18. Running cost for the beta (estimate, 10–20 users)

| Item | Approx. per month |
|---|---|
| Hetzner: 1–2 hosts for api, workers and sandboxes (prices rose June 2026 — check current) | €30–100 |
| Postgres on the host, or Supabase Pro | €0 / ~$25 |
| Object storage, Cloudflare, monitoring (free tiers first) | €0–10 |
| AI usage | $0 for Milvow — tenants bring their own keys and accounts |
| **Total** | **≈ €40–130** |

Cost discipline for Milvow itself: tenants bring their own AI keys; subscription capacity first; cheap models for bulk work;
scripted checks; caching; scale hosts only when a measured limit is reached.
