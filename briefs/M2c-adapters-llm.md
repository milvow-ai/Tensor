# Brief M2c — adapters: Apollo, Hunter, PageSpeed, Adzuna, public ATS boards + LLM executor via Bifrost

Rules: follow `briefs/CONTEXT.md` §6. Touch only the files under "Owns". Never git add/commit/push. Never read `.env`. Never call a live provider API (docs pages are fine to read).

## Goal
Grow the Farm from one capability to the full M2 tool surface (HANDOFF §4.2), each adapter built from `farm/adapters/_template.py` exactly like
`reoon.py`/`zerobounce.py`, with fault classification, key redaction and offline tests. Add the LLM executor so `extract`/`classify` run through the
local Bifrost gateway (OpenAI-compatible) with a virtual key — the Farm never holds an LLM provider key.

## Read first
`briefs/CONTEXT.md`, `HANDOFF.md` §4.2 + §4.8, `farm/adapters/_template.py`, `farm/adapters/reoon.py` (the pattern to copy), `farm/capabilities/schemas.py`
(add your capability models next to the existing ones and register them in the same capability→(input, output) mapping the router uses),
`farm/executors/base.py`, `config/registry.yaml`, `research/r1-data-providers.md` (limits/prices per provider), `library/bifrost/docs/quickstart/gateway/*`.
Official API docs (fetch; record URL + date as `_source` in each fixture):
Apollo (`mixed_people/search` — 0 credits; `organizations/enrich`; `people/match` — 1 credit when found; header `x-api-key`), Hunter v2 (`domain-search`,
`email-finder`, `email-verifier`, `account`), Google PageSpeed Insights v5 `runPagespeed` (+ CrUX `loadingExperience`), Adzuna jobs search,
Greenhouse/Lever/Ashby public job-board JSON (no auth).

## Owns
- `farm/adapters/apollo.py`, `hunter.py`, `pagespeed.py`, `adzuna.py`, `ats_public.py`; registration in `farm/adapters/__init__.py`.
- `farm/executors/llm.py` — `LlmExecutor`: POST `{BIFROST_URL or http://127.0.0.1:8080}/v1/chat/completions`, header `x-bf-vk` from the connection's `auth_ref` (e.g. `env:BIFROST_FARM_VK`), model from connection `meta.model`; `extract(text, json_schema)` uses response_format json_schema when supported else strict JSON instruction + Pydantic validation + 1 repair retry; `classify(text, labels)` returns one label + confidence; units `requests` 1 and `tokens` from usage; `cost_usd` from Bifrost's `usage.cost.total_cost` when present; 429/402/403-from-budget → LIMIT_REACHED with reset; model-blocked 403 → BAD_REQUEST.
- `farm/capabilities/schemas.py` (extend): `FindPersonIn/Out` (company domain or name, titles, seniority → people list with name, title, linkedin, source), `FindEmailIn/Out` (first, last, domain → email, confidence, source, verification hint), `EnrichCompanyIn/Out` (domain → name, industry, size range, location, socials, tech hints, source), `PageSpeedIn/Out` (url, strategy → CrUX p75 LCP/INP/CLS with date + lab metrics marked as lab), `JobsLookupIn/Out` (company/board or what+where+country → postings list), `ExtractIn/Out`, `ClassifyIn/Out`. Output models must carry `source` (provider + connection) and `observed_at`.
- `config/registry.yaml` (extend): providers `apollo` (capabilities find_person, enrich_company, find_email), `hunter` (find_email, verify_email as a third verify pool), `pagespeed` (`env:GOOGLE_PAGESPEED_API_KEY`, 25 000/day), `adzuna`, `ats_public` (no auth, unlimited), `llm` pool (kind ai? no — kind `tool`, executor `llm`) with connections `llm-or-free` (model `openrouter/qwen/qwen3.8-27b:free`), `llm-or-flash` (`openrouter/deepseek/deepseek-v4-flash`), `llm-groq` (status needs_login until a Groq key exists), `llm-bedrock` (status paused; the owner enables it — AWS cap $20 via Bifrost VK); capabilities `find_person` [apollo, clay], `find_email` [hunter, apollo], `enrich_company` [apollo, clay], `pagespeed` [pagespeed], `jobs_lookup` [ats_public, adzuna], `extract`/`classify` [llm] with strategy failover. Keep every existing entry.
- Fixtures `tests/fixtures/<provider>/*.json` and tests `tests/test_adapter_<provider>.py`, `tests/test_executor_llm.py`.

## Tests (offline, respx)
Per adapter: happy path(s) mapped into the output model; 429 + Retry-After; 401; credit exhaustion → LIMIT_REACHED; empty result → `found=False` with EMPTY; 5xx; timeout; malformed body; the sentinel key never appears in any error/log/result; `units_used` reflects the provider's real charging rule (e.g. Apollo search 0, match 1 only when found).
LLM: valid JSON path; invalid JSON then repaired; schema-invalid twice → BAD_REQUEST; Bifrost budget exhausted → LIMIT_REACHED; cost parsed from usage.

## Done when
`scripts/check.ps1` → `RESULT: all passed`, and `uv run python -c "from farm.registry.loader import load_registry; load_registry('config/registry.yaml')"` succeeds.

## Reply (≤ 15 lines)
Files changed; test count; check tail; doc URLs used + anything UNVERIFIED; deviations.
