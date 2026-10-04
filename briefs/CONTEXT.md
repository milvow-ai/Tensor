# Builder context — Harness Farm (read this before any brief)

Owner: lead. Builders read it, never edit it. If something here is wrong or missing, say so in your reply instead of guessing.

## 0. Quality bar (the owner's words: "real engineering, a real builder/developer product — not average, not vibe-coded")
Single owner, single workspace — no multi-tenant or scale-for-others work. But every line is production engineering:
- **Correct under failure first.** Concurrency, crashes, timeouts, partial failures, retries and restarts are designed and tested, not hoped for. Every money/quota path is atomic and idempotent.
- **Tests prove behaviour, not lines.** Each acceptance test reproduces the real failure it guards (kill the process, exhaust the account, race 200 tasks). No test that cannot fail. No mocking the thing under test.
- **Typed, explicit, small.** mypy strict, Pydantic at every boundary, no `Any` leaking out of adapters, no dead code, no TODOs left behind, no copy-paste between adapters (shared code lives in the template/base).
- **Observable.** Every decision the Farm makes is visible in `run_events`/logs with a reason; errors carry a kind, a cause and what the owner should do.
- **Safe by construction.** Secrets never cross a log, an error string, a DB row, a test output or the Console. Inputs validated; SQL parameterised; subprocesses with argv lists, timeouts and kill-on-timeout.
- **Real, not demo.** Real data paths, real CLIs, real Postgres. Fixtures exist only for tests and clearly marked local dev. Every UI state (loading, empty, error, needs-login, exhausted) designed. No lorem ipsum, no placeholder screens pretending to work.
- **Report honestly.** If something is not done, unverified or a deviation, say so in the reply. A smaller true result beats a bigger claimed one.

## 1. What we build
One MCP server ("Harness Farm") that Claude calls for **capabilities** (verify an email, enrich a company, ask another AI…).
The Farm picks a **provider pool** (a tool such as Clay, or an AI such as Claude) and an **account/connection** inside it, reserves quota,
executes, falls back on failure, records cost + trajectory, and returns a structured result. Pools of accounts keep it running 24/7.
A Next.js **Console** (`console/`) reads the same Postgres and sends commands through `farm_commands`.
The Farm makes no AI decisions itself: routing is deterministic code. Design source: `HANDOFF.md` §4–§5 (fixed).

## 2. Conventions (all Python code)
- Python 3.12, `uv`. Run everything as `uv run …` from the repo root. Package `farm/`. Tests in `tests/`.
- **async everywhere** (asyncio). DB = psycopg 3 async + `psycopg_pool.AsyncConnectionPool`. HTTP = `httpx.AsyncClient`.
- Pydantic v2 models for every boundary (registry, executor I/O, tool I/O). `structlog` for logs (JSON). typer for CLI.
- mypy strict and ruff must pass (`powershell -File scripts/check.ps1`). No `# type: ignore` without a reason comment.
- **Secrets:** never log, print, return or store a secret value. Connections hold an `auth_ref` (`env:NAME`, `token-store:ID`, `cli:<profile>`); resolve it only at call time via `farm.secrets.resolve_auth(auth_ref)`. Never read `.env` yourself.
- **No live network in tests.** Mock HTTP with `respx`. `pytest-socket` blocks sockets except 127.0.0.1/localhost (the test DB).
- Time: always UTC `datetime` with tz. Money: `Decimal`/`numeric(12,6)` USD. IDs: text slugs for providers/connections (`clay`, `clay-01`), `uuid` for everything generated.
- Windows paths: use `pathlib`; data lives under `FARM_DATA_DIR` (default `D:/farm-data`), never on C:.

## 3. Database (Postgres 16-compatible SQL; runtime target Supabase PG 17)
- Connection string: env `FARM_DB_URL`, else `SUPABASE_DB_URL`; if neither is set, **local mode** starts an embedded Postgres with `pgserver` at `FARM_DATA_DIR/pg` (`farm.db.pool.get_db_url()` handles all three).
- Migrations: Alembic in `farm/db/migrations`, revisions written as **raw SQL** via `op.execute(...)`. `uv run farm db migrate` = upgrade head.
- All tables live in schema **`public`** (dedicated Supabase project). Every table has `workspace_id uuid not null default '00000000-0000-0000-0000-000000000001'`. Every table gets RLS enabled; policies that reference `auth.*` are created only `if exists (select 1 from pg_namespace where nspname='auth')` (so local Postgres works). Policy: authenticated user whose `auth.jwt()->>'email'` equals `farm_settings.owner_email` may select all tables and insert into `farm_commands` only. The Farm process connects as the DB owner (bypasses RLS).
- Tables (columns are the contract; add indexes as needed; `created_at/updated_at timestamptz default now()` on mutable tables):

| Table | Key columns |
|---|---|
| `farm_settings` | singleton row: `owner_email text`, `global_monthly_budget_usd numeric`, `alert_thresholds int[] default {50,80,100}`, `timezone text default 'UTC'` |
| `providers` | `id text pk`, `name`, `kind text check in ('tool','ai')`, `executor text check in ('api','mcp','llm','cli_agent','agent','browser','local','human')`, `default_strategy text`, `enabled bool`, `config jsonb` |
| `connections` | `id text pk`, `provider_id fk`, `label`, `auth_ref text`, `scope text[] default {internal}`, `priority int default 100` (lower = first), `strategy text null` (override), `concurrency int default 1`, `rate_per_min int null`, `status text check in ('active','paused','needs_login','exhausted','disabled')`, `plan jsonb` (`name, price_usd, billing_day, renews_on`), `meta jsonb` (e.g. `{"cli":"claude","config_dir":"D:/farm-data/ai/claude-02","models":["sonnet","opus"]}`) |
| `consumption_units` | pk(`connection_id`,`unit`), `limit_value numeric null` (null = unlimited), `period text check in ('minute','hour','day','week','month','rolling_5h','total','none')`, `reset_anchor int null` (day of month for monthly), `next_reset_at timestamptz null`, `charged_on text check in ('attempt','success','found')`, `unit_cost_usd numeric default 0`, `estimate_per_call numeric default 1` |
| `quota_usage` | pk(`connection_id`,`unit`,`period_start`), `used numeric`, `reserved numeric`, `limit_value numeric` |
| `quota_reservations` | `id uuid pk`, `connection_id`, `unit`, `amount numeric`, `request_id uuid`, `period_start`, `status in ('reserved','committed','released','expired')`, `expires_at`, `actual numeric null` |
| `usage_events` | `id bigint identity`, `connection_id`, `unit`, `amount`, `kind in ('estimated','actual')`, `cost_usd`, `request_id`, `run_id`, `at` |
| `balance_snapshots` | `id`, `connection_id`, `unit`, `remaining numeric`, `source in ('api','manual','agent','cli')`, `at` |
| `connection_health` | `connection_id pk`, `circuit in ('closed','open','half_open')`, `consecutive_failures int`, `last_error_kind text`, `last_error text`, `last_error_at`, `cooldown_until timestamptz null`, `success_count bigint`, `failure_count bigint`, `last_success_at`, `latency_ms_p50 int null` |
| `budgets` | `id uuid`, `scope in ('global','provider','connection')`, `ref text null`, `monthly_usd numeric`, `hard_stop bool default true` |
| `billing_events` | `id`, `connection_id`, `kind in ('charge','renewal','refund','credit_purchase')`, `amount_usd`, `at`, `note` |
| `alerts` | `id uuid`, `kind`, `severity in ('info','warn','critical')`, `message`, `ref text`, `created_at`, `acked_at null` |
| `capabilities` | `name text pk`, `kind in ('tool','ai')`, `description`, `input_schema jsonb`, `output_schema jsonb`, `default_strategy text`, `cache_ttl_seconds int default 0` |
| `capability_routes` | pk(`capability`,`provider_id`), `position int`, `enabled bool` (route = ordered list of provider **pools**; connections inside a pool are ordered by the strategy) |
| `capability_requests` | `id uuid pk`, `request_hash text`, unique(`workspace_id`,`request_hash`), `capability`, `params jsonb`, `status in ('pending','running','succeeded','failed')`, `result jsonb`, `run_id uuid`, `created_at`, `expires_at` |
| `runs` | `id uuid pk`, `capability`, `request_id uuid`, `caller text` (claude/hermes/console/cli/test), `strategy`, `status in ('running','succeeded','failed','blocked')`, `cost_usd numeric`, `cached bool`, `connection_id null` (final), `error_kind`, `error`, `started_at`, `finished_at` |
| `run_events` | `id bigint identity`, `run_id`, `seq int`, `kind` (`plan`,`cache_hit`,`single_flight_join`,`policy_block`,`candidate`,`skip`,`reserve`,`reserve_failed`,`execute`,`success`,`failure`,`fallback`,`commit`,`release`,`requeue`), `connection_id null`, `data jsonb`, `at` |
| `entities` / `facts` / `evidence` | (M4) entities(`id uuid`, `kind`, `canonical_key unique`, `name`); facts(`id`, `entity_id`, `attribute`, `value jsonb`, `source_connection_id`, `observed_at`, `expires_at`, `confidence`, `evidence_ids uuid[]`); evidence(`id uuid`, `sha256`, `path`, `url`, `thumb_path null`, `captured_at`, `tool_version`) |
| `ai_sessions` | `session_id text pk`, `connection_id`, `ai text`, `model text`, `created_at`, `last_used_at` (keeps follow-ups on the same account) |
| `farm_commands` | `id uuid pk`, `kind text` (`pause`,`resume`,`set_priority`,`set_strategy`,`set_budget`,`add_connection`,`update_connection`,`remove_connection`,`set_route`,`test_connection`,`ack_alert`), `payload jsonb`, `status in ('queued','running','done','rejected','failed')`, `result jsonb`, `created_by text`, `created_at`, `done_at` |
| `audit_events` | `id bigint identity`, `actor`, `action`, `target`, `before jsonb`, `after jsonb`, `at` |

- SQL functions (atomic, the only way to touch quota):
  - `farm_period_start(period text, anchor int, at timestamptz) returns timestamptz` (month with anchor day clamped to month length; rolling_5h/total/none → fixed epoch so one row).
  - `farm_reserve(p_connection text, p_unit text, p_amount numeric, p_request uuid, p_ttl_seconds int) returns uuid` → null when `used + reserved + amount > limit_value` (null limit = unlimited). Single statement / row lock, no race.
  - `farm_commit(p_reservation uuid, p_actual numeric) returns void` → moves `amount` out of `reserved`, adds `actual` to `used` (actual 0 when charged_on='success' and the call failed). Idempotent.
  - `farm_release(p_reservation uuid) returns void` → idempotent. Expired reservations are released by `farm_expire_reservations()`.
- SQL views for the Console (`v_*`): created in the milestone that needs them; always aggregate in SQL, never in the client.

## 4. Python contracts (module → public API)
- `farm/settings.py`: `load_env()`, `require(name)`, `data_dir() -> Path`.
- `farm/secrets.py`: `resolve_auth(auth_ref) -> str` (`env:NAME` now; `token-store:`/`cli:` later; raises `AuthRefError` whose message never contains the value) and `redact(text) -> str` (masks any resolved secret value and `key=`/`api_key=`/`token=` query params — use it on every error string that may contain a URL).
- `farm/db/pool.py`: `get_db_url() -> str`, `async open_pool() -> AsyncConnectionPool`, `async close_pool()`; `farm/db/local.py`: embedded pgserver start/stop.
- `farm/registry/models.py`: Pydantic `Registry(providers: dict[str, ProviderSpec], capabilities: dict[str, CapabilitySpec], budgets: BudgetSpec, settings: SettingsSpec)`; `ProviderSpec(name, kind, executor, default_strategy, enabled, config, connections: list[ConnectionSpec])`; `ConnectionSpec(id, label, auth_ref, scope, priority, strategy, concurrency, rate_per_min, status, plan, meta, units: dict[str, UnitSpec])`; `UnitSpec(limit, period, anchor, charged_on, unit_cost_usd, estimate_per_call)`; `CapabilitySpec(kind, description, routes: list[str], strategy, cache_ttl_seconds)`. `farm/registry/loader.py`: `load_registry(path) -> Registry` (YAML), `export_json_schema() -> dict`. `farm/registry/sync.py`: `async sync_registry(pool, registry)` upserts into DB (DB is the runtime source of truth; YAML seeds it; Console edits go through `farm_commands`).
- `farm/executors/base.py`:
  ```python
  class ErrorKind(StrEnum): RATE_LIMITED="rate_limited"; AUTH="auth"; EMPTY="empty"; TIMEOUT="timeout"; SERVER="server"; BAD_REQUEST="bad_request"; LIMIT_REACHED="limit_reached"; NEEDS_LOGIN="needs_login"; UNKNOWN="unknown"
  class ConnectionView(BaseModel): id, provider_id, auth_ref, meta, concurrency, rate_per_min   # what an executor may see
  class ExecRequest(BaseModel): request_id: UUID; capability: str; params: dict[str, Any]; connection: ConnectionView; timeout_s: float = 30
  class ExecResult(BaseModel): ok: bool; data: dict[str, Any] | None = None; found: bool | None = None; units_used: dict[str, float] = {}; cost_usd: Decimal = 0; error_kind: ErrorKind | None = None; error: str | None = None; retry_after_s: float | None = None; reset_at: datetime | None = None; latency_ms: int = 0
  class Executor(Protocol):
      async def execute(self, req: ExecRequest) -> ExecResult: ...
  ```
  Executors never raise for provider errors: they classify into `ErrorKind`. Only programming errors raise.
- `farm/adapters/<provider>.py`: one class per provider, subclass of `farm/adapters/_template.py:ApiAdapter` (httpx client, base_url, auth header from `resolve_auth`, timeout, status→ErrorKind map: 429→RATE_LIMITED (+Retry-After), 401/403→AUTH, 402→LIMIT_REACHED, 404/empty→EMPTY, 5xx→SERVER, timeout→TIMEOUT). Normalised outputs per capability live in `farm/capabilities/schemas.py` (e.g. `VerifyEmailOut(email, status: Literal['valid','invalid','risky','catch_all','unknown'], sub_status, provider, checked_at)`).
- `farm/resources/ledger.py`: `reserve(pool, conn_id, unit, amount, request_id, ttl_s=300) -> UUID | None`, `commit(pool, res_id, actual)`, `release(pool, res_id)` — thin wrappers over the SQL functions.
- `farm/resources/health.py` (M2), `strategies.py` (M2/M3), `router.py`: `async route(ctx, capability, params, *, strategy=None, pin=None, caller) -> RouteOutcome` — cache → candidates (enabled, status active, circuit not open, cooldown passed, capacity ≥ estimate, scope) → rank/strategy → reserve → execute → commit/release → classify → fallback; writes `runs` + `run_events` for every step.
- `farm/gateway/server.py`: `FastMCP("harness-farm")`; one tool per capability + infra tools (`get_capacity`, `list_resources`, `get_run`, `get_usage`, later `list_ais`). Middleware order: auth → policy (budget hard stop, scope) → cache → single-flight → trajectory. `uv run farm serve` = stdio; `--http` later.
- Every capability tool returns `{"ok": bool, "result": {...} | null, "error": {...} | null, "run_id": str, "source": {"provider": str, "connection_id": str, "cached": bool}, "cost": {"usd": float, "units": {...}}}`.
- Request identity: `request_hash = sha256(capability + "|" + json.dumps(params, sort_keys=True, separators=(",",":")))`.

## 5. Tests
- `tests/conftest.py` (M1a) gives fixtures: `db_url` (session: embedded pgserver in `FARM_DATA_DIR/pgtest`, fresh database per test session, migrated to head), `pool` (async pool, each test truncates all tables except `alembic_version`), `registry` (loads `tests/fixtures/registry.yaml`).
- Fault cassettes and fixtures in `tests/fixtures/<provider>/*.json`; recorded cassettes in `tests/cassettes/`.
- Acceptance tests are named `test_accept_<milestone>_*.py`.

## 6. Builder rules (every brief)
Touch only the files your brief owns. Never `git add/commit/push`, deploy, delete outside the repo, or send data off the machine. Never read or print `.env`. Do not change `pyproject.toml` unless your brief owns it.
Finish by running `powershell -NoProfile -ExecutionPolicy Bypass -File scripts/check.ps1` and include its last lines in your reply.

Run every command in the foreground and wait for it; never start long commands in the background and poll them (each poll re-sends your whole context and burns quota). Report results once, at the end.

## 7. AI CLI facts (discovered by M3e-A, 2026-10-04; fake-CLI tests in `tests/test_cli_agent_*.py`)
- **Claude Code:** `claude -p <task> --output-format json [--model m] [--resume id] [--allowedTools ""] [--permission-mode acceptEdits]`; JSON `result, session_id, total_cost_usd, usage, is_error`; limit text `usage limit`/`rate limit` + reset time; auth `not logged in`/`please log in`. **One account per `CLAUDE_CONFIG_DIR`.**
- **Codex 0.160.0:** `codex exec [--sandbox read-only|workspace-write] [-m m] --json --skip-git-repo-check <task>`, resume `codex exec resume <id>`; JSONL events (`message`, `turn.finished` usage, `thread_id`); limit `insufficient_quota`/`rate limit exceeded`; auth `Not logged in`. **One account per `CODEX_HOME`.**
- **Antigravity (agy):** `agy -p <task> --output-format json [--model m] [--conversation id]`; JSON `conversation_id, status, response, usage`; limit `RESOURCE_EXHAUSTED`/`Individual quota reached`; **single account only** (global config; no per-account dir) — must be the owner's Gemini Pro account.
- **Hermes:** `hermes -p <profile> -z <task> --usage-file f [-m m] [--resume id]`, cwd via `TERMINAL_CWD`; usage JSON `estimated_cost_usd, session_id, input/output_tokens, failed`. **One account per profile.**
- Claude/Codex limit + reset formats are from docs and fakes, not yet observed live — verify on the first real limit and adjust the parsers.


## Reuse first (owner 2026-10-05)
Before writing any non-trivial component, check `library/` (pinned repos, `research/library.tsv`) and the pinned dependencies (FastMCP 4, DBOS, pybreaker, RJSF, shadcn starter, TanStack, Recharts, …) for an existing implementation and build on it. Hand-rolled code is for the Farm's own logic (registry, router/ledger wiring, policies, UI composition). Never trade quality for reuse: a reused block must meet §0 (typed, tested, secure, accessible) or it is wrapped until it does. New repos worth pinning are proposed to the lead (name, URL, licence, why), not vendored silently.
