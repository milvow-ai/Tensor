# Brief L1A — Connect core: login sessions, sealed input, login service (no real CLI drivers yet)

Rules for every step: touch only the files listed under "Owns". Never read any .env file. Never run git add, commit, push,
reset, checkout, stash or worktree commands. Never call a real provider, never start a real login, never send data off this
machine. Run every command in the foreground and wait for it.

## Goal

The Farm side of "sign in from the dashboard" (design: `docs/design/CONNECT.md` §5). A dashboard command starts a login
session; a driver runs the login in a **staging home**; the session row tells the dashboard what to show (link, code, "paste
the code"); the dashboard sends **sealed** input; on success the staging home replaces the account's home atomically; on any
failure nothing about the account changes. This round builds all of that except the real CLI drivers (they come in L1B after
a spike): tests use a scripted `FakeDriver`.

## Read first

`CLAUDE.md`; `docs/design/CONNECT.md` (all of it); `docs/ARCHITECTURE.md` §4 and §7;
`farm/db/migrations/versions/0019_tenancy_enforced.py` (tenant tables, RLS, grants, `security_invoker` views — copy its
patterns exactly); `farm/control/commands.py` (`PAYLOAD_VALIDATORS`, `execute_command`, `execute_provider_command` for
`add_provider`, `_assert_no_raw_secrets`, `_emit_audit`, `_run_claimed`, `CommandConsumer`); `farm/control/run.py` (`run_farm`,
where `CommandConsumer` starts); `farm/secrets.py` (`set_secret`, `redact`, `register_secret`); `farm/tenancy/context.py`
(`use_tenant`, `require_tenant`) and how `farm/ai/jobs.py` runs background work under a tenant; `farm/control/cli.py`
`ai_login` (how an account's home is chosen today); `tests/test_isolation_data.py` and the command tests (find them with grep)
for fixtures and style.

## Owns (create or change only these)

- `farm/db/migrations/versions/0020_login_sessions.py` (new)
- `farm/connect/__init__.py`, `sealed.py`, `homes.py`, `sessions.py`, `service.py`, `commands.py`,
  `drivers/__init__.py`, `drivers/base.py`, `drivers/fake.py` (all new)
- `farm/control/commands.py`: only (a) register the new kinds in `PAYLOAD_VALIDATORS` and route them to
  `farm/connect/commands.py`, (b) let a valid sealed envelope through `_assert_no_raw_secrets`, (c) blank consumed sealed
  values in the stored command payload (step 7). Smallest possible diff.
- `farm/control/run.py`: start and stop the `LoginService` next to `CommandConsumer`. Smallest possible diff.
- `pyproject.toml` (add `cryptography` to the dependencies) and `uv.lock` (only as `uv` rewrites it).
- `tests/connect/` (new): `__init__.py`, `test_sealed.py`, `test_homes.py`, `test_sessions.py`, `test_service.py`,
  `test_commands.py`, `fixtures/sealed_vectors.json`
- Must not touch anything else (no `console/`, `farm/executors/`, `farm/gateway/`, `config/`, `docs/`, existing tests,
  `tests/quarantine.txt`).

## Steps

1. **Migration 0020** (expand only, revises 0019). `public.login_sessions`:
   `id uuid` primary key (default like 0019's tables), `tenant_id` exactly like 0019's tenant tables (NOT NULL, FK, RLS),
   `connection_id text not null` (FK to `connections` the way other tenant tables reference it; cascade on delete),
   `provider_id text not null`, `method text not null` (check: `device_code, paste_code, loopback, oauth_redirect, api_key,
   token`), `state text not null default 'queued'` (check: `queued, starting, waiting_for_user, verifying, done, failed,
   expired, cancelled, duplicate, needs_verification`), `auth_url text`, `user_code text`, `verification_uri text`,
   `needs_input boolean not null default false`, `input_hint text`, `expires_at timestamptz`,
   `identity jsonb not null default '{}'`, `evidence jsonb not null default '{}'`, `error_kind text`, `error_excerpt text`,
   `created_by text not null`, `created_at`, `updated_at` (both `timestamptz not null default now()`), `finished_at timestamptz`.
   A partial unique index: one active session per connection, `(tenant_id, connection_id) where state in ('queued',
   'starting', 'waiting_for_user', 'verifying')`. RLS policies and grants for the runtime role exactly like 0019's tenant
   tables. View `public.v_login_sessions` with `security_invoker = true`, all columns. Add to the single-row
   `public.farm_settings` a nullable column `connect_public_key jsonb`. Downgrade removes all of it.
2. **`farm/connect/sealed.py`** (values typed in the dashboard, opened only here):
   - `load_or_create_keys(dir: Path)`: P-256 private key in `dir/connect.pem` and 32 random bytes in `dir/connect-salt`,
     both created once (POSIX: mode 0600; Windows: rely on the profile ACL, note it in a comment). `dir` defaults to
     `<data_dir>/keys`. Returns the private key, the public JWK (`kty, crv, x, y`) and the salt.
   - Envelope v1 (JSON): `{"v": 1, "alg": "ECDH-ES+HKDF-SHA256+A256GCM", "epk": <JWK>, "iv": <b64url, 12 bytes>,
     "ct": <b64url ciphertext+tag>}`. Key: ECDH(farm private, epk) → HKDF-SHA256(salt empty, info
     `b"harness-farm/connect/v1"`, 32 bytes) → AES-256-GCM with AAD.
   - AAD is always `f"{tenant_id}|{subject}|{purpose}"`, purpose one of `login_input`, `secret`, `api_keys`; subject is the
     session id (login input) or the secret's name.
   - `open_sealed(envelope, *, private_key, aad) -> str`; every failure raises `SealedError` whose message never contains
     plaintext, key material or the ciphertext. `seal(plaintext, public_jwk, aad) -> dict` (same algorithm; tests and fixtures).
   - `publish_public_key(pool)` writes the JWK to `farm_settings.connect_public_key`.
   - Pydantic model `SealedEnvelope` (`extra="forbid"`, `v: Literal[1]`, `alg` literal, `epk` with `kty="EC"`,
     `crv="P-256"`, `x`, `y`, `iv`, `ct` as base64url strings with sane length limits).
   - `tests/connect/fixtures/sealed_vectors.json`: a fixed TEST-ONLY key pair (PEM, marked as such) and 3 envelopes with
     their AAD and plaintext, so the browser side (L3) can be checked against it.
3. **Drivers** — `drivers/base.py`: events `Url(url)`, `UserCode(code, verification_uri)`, `NeedsInput(hint)`,
   `NeedsVerification(url, message)`, `Success()`, `Failure(kind, excerpt)`; `Identity(provider_account_id, email, plan,
   org)` with `masked_email()` → first character + `•••••` + `@domain`; a `LoginDriver` protocol with
   `method`, `async start(home, hint)`, `events() -> AsyncIterator[event]`, `async submit(text)`, `async cancel()`,
   `async identity(home) -> Identity | None`, `async models(home) -> list[str]`. `drivers/fake.py`: `FakeDriver` scripted
   with a list of events (and optional delays), an identity, a model list; records what was submitted; writes a marker file
   into the staging home on `Success` so tests can see the promotion.
4. **`farm/connect/homes.py`**: `account_home(conn_meta, connection_id)` — the same rule `farm ai login` uses (`meta.home`,
   else `meta.config_dir`, else `<data_dir>/ai/<id>`); `staging_home(home, session_id)` → sibling
   `<home>.login-<first 8 of id>`, created empty; `promote(staging, home)` — atomic swap: existing home →
   `<home>.prev-<UTC timestamp>`, staging → home, keep only the newest `.prev-*`; on Windows retry each rename up to 5 times
   with 200 ms between tries; if the second rename fails, rename the prev back and raise; `discard(staging)` — remove it,
   missing is fine.
5. **`farm/connect/sessions.py`**: create, get, update state (sets `updated_at`; sets `finished_at` for `done, failed,
   expired, cancelled, duplicate`), active session for a connection, expire overdue (`expires_at < now` while
   `waiting_for_user` → `expired`), mark every active session `failed` with `error_kind='farm_restart'`. All through the
   tenant-scoped connection the rest of the code uses.
6. **`farm/connect/service.py` — `LoginService`**:
   - Constructor: `pool`, `driver_factory(provider_id, connection_id, meta) -> LoginDriver`, `test_job(connection_id,
     home) -> awaitable result with ok: bool and run_id: str`, `keys_dir`, `clock`, `max_concurrent=3`, `user_timeout_s=600`,
     `verify_timeout_s=120`. Module-level `set_login_service()` / `get_login_service()` like `register_active_gateway`.
   - `start_login(tenant_id, connection_id, actor, method=None) -> session_id`: if an active session exists for that
     connection, return its id; otherwise create it (`queued`) and run it as an asyncio task (semaphore of `max_concurrent`;
     waiting ones stay `queued`). Background tasks set the tenant context explicitly, the way `farm/ai/jobs.py` does.
   - The task: `starting` → `driver.start(staging, hint)` (hint = `meta.expected_email`) → events: `Url`, `UserCode`,
     `NeedsInput` → `waiting_for_user` with the matching columns and `expires_at = now + user_timeout_s`;
     `NeedsVerification` → `needs_verification` (`auth_url` = its url, `error_excerpt` = its message); `Success` →
     `verifying` → `identity` → **duplicate check**: another connection of the same provider in this tenant whose
     `meta.identity.account_hash` equals this one → `duplicate`, `error_excerpt` names that connection, staging discarded →
     else `models`, then `test_job` (must return ok) → `promote(staging, home)` → update the connection: `status='active'`
     when it was `needs_login`, `meta.identity = {account_hash, email_masked, plan, org}`, `meta.login = {method,
     last_login_at}`, `meta.models` = models when non-empty → `done` with `evidence = {test_run_id, models}`.
     `Failure` → `failed` with its kind and a `redact`ed excerpt (≤ 500 chars). No event before `expires_at` → `expired`.
     Any exception → `failed` (`crash`). Every outcome except `done` discards the staging home. The account's existing
     home changes only through a successful `promote`.
   - `account_hash` = hex HMAC-SHA256(key = `connect-salt` file bytes, message = `f"{tenant_id}|{provider_id}|
     {provider_account_id}"`).
   - `submit_input(tenant_id, session_id, envelope, actor)`: session must be `waiting_for_user` with `needs_input`; open with
     AAD `tenant|session|login_input`; `register_secret(text)` before anything else; `driver.submit(text)`.
   - `cancel(tenant_id, session_id, actor)`: `driver.cancel()`, `cancelled`, discard staging.
   - Every state change writes an audit event with `_emit_audit` (action `login_session.<state>`, target the connection id,
     after = `{session_id, state}`). CLI output is never logged unless passed through `redact`.
   - `start()`: load keys, publish the public key, mark leftover active sessions `farm_restart`, start a sweeper that expires
     overdue sessions every 15 s. `stop()`: cancel running tasks, mark their sessions `failed` (`farm_restart`), discard staging.
7. **Commands** — `farm/connect/commands.py` with pydantic payloads (`extra="forbid"`) and handlers; registered in
   `PAYLOAD_VALIDATORS` and dispatched from `execute_command`:
   - `start_login {connection_id, method?}` → `{session_id}`.
   - `submit_login_input {session_id, sealed: SealedEnvelope}` → `{session_id, state}`.
   - `cancel_login {session_id}` → `{session_id, state}`.
   - `add_accounts {provider_id, count (1–10), cli?, plan?, emails? (≤ count), max_parallel (1–4, default 1)}`: ids follow the
     provider's existing ones (`claude-05` after `claude-04`; for provider `gemini` the prefix comes from `cli`: `agy-NN` or
     `gemini-cli-NN`, `cli` defaulting to the most common one in that pool; numbers zero-padded to 2, next free). For each:
     the same insert path `farm ai add` uses (`execute_provider_command(pool, "add_provider", …)`), then `meta.expected_email`
     and `meta.plan` when given, no `home`/`config_dir` (so homes default to `<data_dir>/ai/<id>`), status `needs_login`, then
     `start_login`. → `{connection_ids, session_ids}`.
   - `save_secret {name, sealed: SealedEnvelope, connection_id?}`: `name` validated like an env var name in
     `validate_auth_ref`; open with AAD `tenant|<name>|secret`; `register_secret`; `farm.secrets.set_secret(name, value)`;
     when `connection_id` is given set its `auth_ref` to `env:<name>`. → `{saved: name}` only.
   - `_assert_no_raw_secrets` skips a value that validates as `SealedEnvelope` (ciphertext looks random by design) and still
     rejects a raw key anywhere else.
   - After `submit_login_input` or `save_secret` finishes, replace the `sealed` value in the stored `farm_commands` payload
     with the string `"consumed"`, in the same place the command's result is written.
8. **Wiring in `farm run`**: construct `LoginService` with the real pool; `driver_factory` raises
   `NotImplementedError("CLI drivers arrive in L1B")` for every provider, and the session then ends `failed` with
   `error_kind='not_implemented'`; `test_job` likewise. Start it after `CommandConsumer`, stop it before. This is intended.
9. **Tests** (`tests/connect/`, no network, no real CLI, real test database like the other DB tests):
   - sealed: round trip; wrong AAD, tampered `ct`, wrong key each raise `SealedError`; messages hold no plaintext; the vectors
     file opens; `SealedEnvelope` rejects extra fields.
   - homes: promote swaps and keeps one prev; rollback when the second rename fails (monkeypatch `os.replace`/`rename`);
     discard twice is fine.
   - service with `FakeDriver`: (a) device code → `waiting_for_user` with `user_code` → `Success` → `done`, home promoted
     (marker file), connection `active`, `meta.identity` set, audit rows present; (b) paste code → `NeedsInput` → sealed
     submit → driver got the plaintext → `done`; (c) duplicate identity → `duplicate`, staging gone, old home untouched, the
     message names the other connection; (d) `Failure` → `failed`, old home untouched; (e) no user action → `expired`;
     (f) driver raises → `failed` `crash`; (g) second `start_login` while active returns the same id; (h) `stop()` →
     `farm_restart`; (i) `test_job` not ok → `failed`, old home untouched; (j) `max_concurrent` honoured.
   - commands: `add_accounts` ids for claude (after 04) and gemini/agy; `save_secret` writes through `set_secret`
     (monkeypatched to a temp `.env`) and sets `auth_ref`; a raw key in another field is rejected; the stored payload says
     `"consumed"` afterwards.
   - isolation: a session made under tenant A is invisible under tenant B (pattern of `tests/test_isolation_data.py`).
10. Run `uv run pytest tests/connect -q` after each step. At the end run the full gate:
    `powershell -NoProfile -ExecutionPolicy Bypass -File scripts/check.ps1` — it must end with `RESULT: all passed`
    (ruff, mypy, pytest). Fix your code until it does; never weaken or skip existing tests.

## Done when

`scripts/check.ps1` prints `RESULT: all passed` with the new tests included, and every step above is implemented.

## Reply (≤ 15 lines)

Files changed (count + list) · tests added (count) · the gate's last lines · choices you made where this brief allowed one ·
anything left open.
