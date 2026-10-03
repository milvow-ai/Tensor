# Brief M1b — registry, executor contract, secrets, Reoon + ZeroBounce adapters

Rules: follow `briefs/CONTEXT.md` §6. Touch only the files under "Owns". Never git add/commit/push. Never read `.env`.
You work in a separate git worktree; another builder is building the database layer (M1a) in parallel — do not create DB code, `tests/conftest.py`, or touch `pyproject.toml`/`uv.lock`.

## Goal
The typed heart of the Farm that has no DB dependency: the registry (YAML → Pydantic → JSON Schema, which the Console will use to generate forms),
the executor contract every resource implements, secret resolution with redaction, the API-adapter template, and the first two real adapters
(`verify_email` via Reoon and ZeroBounce) with fault-classified, fully mocked tests. Quality bar: an adapter must never leak a key (keys travel in
query strings for both providers), must never raise on provider errors, and must map every provider status into the normalised schema.

## Read first
`briefs/CONTEXT.md` (all — §4 is your API contract), `HANDOFF.md` §4.2–§4.6 and §12 (registry shape), `config/registry.yaml` (current placeholder).
Provider docs (fetch them; record the URL + date you used at the top of each fixture file as a `_source` field):
- Reoon Email Verifier API: verify endpoint (quick + power mode) and account-balance endpoint — start at https://www.reoon.com/articles/api-documentation-of-reoon-email-verifier/
- ZeroBounce API v2: `/v2/validate` and `/v2/getcredits` — start at https://www.zerobounce.net/docs/email-validation-api-quickstart/
If a doc page is unreachable, build from the best available official source, mark the fixture `"_source": "UNVERIFIED: <why>"`, and list it under open issues.

## Owns
- `farm/secrets.py` — `resolve_auth`, `AuthRefError`, `redact` (CONTEXT §4). `redact` must mask resolved values seen in this process and `key|api_key|apikey|token|access_token=` query values.
- `farm/registry/__init__.py`, `farm/registry/models.py`, `farm/registry/loader.py` (`load_registry`, `export_json_schema`, validation: unique connection ids, routes reference existing providers, capability kinds match provider kinds, strategy names in the allowed set `failover|most_remaining|round_robin|parallel_split|sticky|fit_check|pin`).
- `farm/executors/__init__.py`, `farm/executors/base.py` (exactly CONTEXT §4).
- `farm/capabilities/__init__.py`, `farm/capabilities/schemas.py` (`VerifyEmailIn(email: EmailStr-like validated str)`, `VerifyEmailOut` per CONTEXT §4, plus `status` mapping helpers).
- `farm/adapters/__init__.py` (a registry `ADAPTERS: dict[str, type[ApiAdapter]]`), `farm/adapters/_template.py` (`ApiAdapter` base: injectable `httpx.AsyncClient`, per-call timeout, status→ErrorKind map, Retry-After parsing, latency, `redact` on every error string, a `capabilities()` mapping), `farm/adapters/reoon.py`, `farm/adapters/zerobounce.py` (each: `verify_email` + `balance()` returning remaining credits; map provider statuses → `valid|invalid|risky|catch_all|unknown`; `found`/charging semantics per the docs; `units_used={"credits": 1}` only when the provider charges).
- `config/registry.yaml` — the real registry for this user (see below).
- `tests/fixtures/registry.yaml`, `tests/fixtures/reoon/*.json`, `tests/fixtures/zerobounce/*.json`, `tests/test_registry.py`, `tests/test_secrets.py`, `tests/test_adapter_reoon.py`, `tests/test_adapter_zerobounce.py`.

## `config/registry.yaml` content (shape per HANDOFF §12 + CONTEXT §4 models)
- settings: owner_email `milvow.ai@gmail.com`, global monthly budget 0 (hard stop on paid calls until the owner raises it), alert thresholds 50/80/100.
- Tool pools: `reoon` (1 connection `reoon-01`, `env:REOON_API_KEY`, units credits limit 20 period day charged_on success), `zerobounce` (`zerobounce-01`, `env:ZEROBOUNCE_API_KEY`, credits 100 month anchor 1), `apollo` (`apollo-01`, `env:APOLLO_API_KEY`, status `needs_login`), `hunter` (`hunter-01`), `clay` (executor `mcp`, default_strategy `sticky`, seven connections `clay-01`…`clay-07`, `auth_ref: token-store:clay-0N`, status `needs_login`, units credits limit null until the owner fills the plan).
- AI pools (kind `ai`, executor `cli_agent`): `claude` (connections `claude-02`,`claude-03`,`claude-04`, `auth_ref: cli:claude-0N`, meta `{cli: claude, config_dir: D:/farm-data/ai/claude-0N, models: [sonnet, opus, haiku]}`, status `needs_login`, units `requests` limit null period `rolling_5h`), `gemini` (`agy-01`, meta `{cli: agy, models: [gemini-3.8-flash-high, gemini-3.1-pro-high]}`, status active), `codex` (`codex-01`, status needs_login), `hermes` (`hermes-01`, meta `{cli: hermes, profile: farm-agent, models: [openrouter/deepseek/deepseek-v4-flash]}`, status active).
- Capabilities: `verify_email` (kind tool, routes [reoon, zerobounce], strategy failover, cache_ttl_seconds 5184000), `ask_ai` (kind ai, routes [claude, gemini, codex, hermes], strategy failover, cache 0).
- Budgets: global 0; per provider 0 except where noted; comment that the owner sets real numbers in the Console.

## Tests (all offline, respx)
- Registry: the real `config/registry.yaml` loads and validates; bad ids/routes/strategies raise clear errors; `export_json_schema()` is valid JSON Schema containing ProviderSpec + ConnectionSpec.
- Secrets: env resolution; missing env → `AuthRefError` whose message names the env var but contains no value; `redact` masks a resolved key and `?key=…&email=…` URLs.
- Each adapter: happy paths for every provider status (mapped correctly), 429 with Retry-After → RATE_LIMITED + retry_after_s, 401 → AUTH, 402/credit-exhausted body → LIMIT_REACHED, 500 → SERVER, timeout → TIMEOUT, malformed JSON → UNKNOWN; **no test output, exception or ExecResult.error contains the fake key** (assert on a sentinel key string); balance() parses remaining credits.

## Done when
`powershell -NoProfile -ExecutionPolicy Bypass -File scripts/check.ps1` → `RESULT: all passed` in your worktree.

## Reply (≤ 15 lines)
Files changed; test count; last 6 lines of check.ps1; the doc URLs used and anything UNVERIFIED; deviations from CONTEXT §4 and why.
