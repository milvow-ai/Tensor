# PPLX — Perplexity as an AI pool (API-key accounts)

Owner request 2026-10-25: "add a Perplexity option" in AI Pools. Perplexity has no agent CLI; its programmable surface is
the **Sonar API** (OpenAI-compatible `POST https://api.perplexity.ai/chat/completions`, `Authorization: Bearer <key>`,
models `sonar`, `sonar-pro`, `sonar-reasoning-pro`, `sonar-deep-research`; the response has `choices[0].message.content`,
`citations` / `search_results`, and `usage` incl. `cost` when present). Accounts are **API keys**, pasted in the dashboard
and stored sealed (existing `save_secret` command → `.env` + `connections.auth_ref = "env:<NAME>"`; resolve with
`farm.secrets.resolve_auth`). Answer mode only (no file edits). Worktree `D:\Harness Farm\wt-pplx`, branch `perplexity`.
No keys in code, tests, logs or errors (use `farm.secrets.redact`). No commits.

## Part 1 — Farm backend · builder: codex-01
1. `farm/executors/cli_agent/perplexity.py`: `PerplexityApiExecutor` implementing the same `Executor` protocol as the
   other drivers (`farm/executors/base.py`: `ExecRequest` → `ExecResult`; mirror how `hermes.py` maps prompt, model,
   timeout, usage, cost and failures). httpx async; model from the request or `connection.meta["model"]`, default `sonar`.
   The answer text gets a "Sources:" list appended from `citations`/`search_results`. Failure mapping into the existing
   failure kinds (`farm/ai/failures.py`): 401/403 → auth, 429 → limit (honour `retry-after`), 5xx/timeout → crash/timeout,
   400 → bad_request. Usage tokens and `usage.cost.total_cost` (or computed 0 + `cost_estimated`) like other drivers.
2. Register it: `"perplexity": PerplexityApiExecutor()` in `farm/executors/cli_agent/__init__.py`; add `"perplexity"`
   to `AiProvider` (`farm/capabilities/schemas.py:472`) and to the `cli` Literals in `farm/connect/commands.py:56` and
   `farm/control/commands.py:311`; wherever the AI list / effort handling / login methods switch on the AI name
   (`grep -rn '"hermes"' farm`), add perplexity: effort not applied; login method = API key (no CLI login: `start_login`
   for a perplexity account returns a clear "paste an API key" rejection; `save_secret` with `connection_id` is how it
   signs in); account test = one tiny `sonar` call ("Respond with only the word PONG").
3. `add_accounts` for provider `perplexity` creates the pool + accounts (`perplexity-01`, …) with `meta.cli = "perplexity"`,
   `auth_ref` empty until a key is saved, status `needs_login` until then.
4. Tests (respx, no network): success with citations appended and usage/cost recorded; 401 → auth; 429 with retry-after
   → limit + retry_at; the key never appears in the result, logs or error text; registry/add_accounts creates a perplexity
   pool; `ai_start(ai="perplexity")` routes to the executor. Run `uv run pytest -q tests/<your new tests>` and
   `uv run ruff check` + `uv run mypy` on changed files.
Files: `farm/executors/cli_agent/perplexity.py` (new), the registrations above, `farm/connect/**` only where the AI list
lives, new `tests/test_perplexity*.py`. Do not touch `console/`.

## Part 2 — Dashboard · builder: agy-01 (Gemini)
1. `console/src/lib/farm/command-meta.ts`: add `perplexity` to `AI_CLIS` / `AI_CLI_INFO` (label "Perplexity", models
   above), and mark it as an **API-key** AI (e.g. `signIn: "api_key"`), keep the existing CLIs as they are.
2. Add-accounts dialog and the account sign-in action: for an API-key AI, the sign-in card shows a password field
   "Perplexity API key" + a link to https://www.perplexity.ai/account/api/keys, and sends the existing sealed
   `save_secret` command with `connection_id` (reuse the sealed-envelope code path of L3; the plaintext never leaves the
   browser unsealed). After saving, the account test runs like for other AIs.
3. Zod schemas in `console/src/lib/farm/commands.ts` accept `"perplexity"` wherever the CLI enum is.
4. Fixtures (`console/src/lib/farm/fixtures/world.ts`): a Perplexity pool with one active and one needs-login account.
5. e2e: a spec `console/e2e/perplexity.spec.ts` (add it to the "behaviour" project's testMatch in `playwright.config.ts`):
   add a Perplexity account → sign-in card asks for the API key → submitting sends `save_secret` whose payload holds an
   envelope and NOT the plaintext (copy the pattern of `signin.spec.ts`).
Files: `console/src/lib/farm/command-meta.ts`, `console/src/lib/farm/commands.ts`, `console/src/lib/farm/fixtures/**`,
`console/src/app/(main)/pools/_components/**`, `console/e2e/perplexity.spec.ts`, `console/playwright.config.ts`
(testMatch only). Do not touch `farm/`. Run `pnpm check`; run only `pnpm exec playwright test --project=behaviour perplexity`.

## Report (≤ 12 lines each)
Files changed, how a key flows (dashboard → sealed → env → executor), test results.
