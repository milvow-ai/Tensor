# S0-4 — Metering fields (runtimes that ran)

| Runtime | Where | Fields as reported | Cached input separate? | Reasoning separate? | Cost |
|---|---|---|---|---|---|
| Claude Code `-p --output-format json` (live) | top level | `total_cost_usd`; `usage.{input_tokens, cache_creation_input_tokens, cache_read_input_tokens, output_tokens, output_tokens_details.thinking_tokens}`; `usage.cache_creation.{ephemeral_5m,ephemeral_1h}_input_tokens`; `modelUsage.<model>.{inputTokens, outputTokens, cacheReadInputTokens, cacheCreationInputTokens, costUSD, costBasis:"list", contextWindow}`; `duration_ms`, `duration_api_ms`, `num_turns`, `permission_denials` | yes: read and creation, 5m vs 1h | `thinking_tokens` (0 for haiku here) | **notional**: `costBasis:"list"` is the API list price; subscription accounts are not billed per call |
| Claude via ACP (live) | `session/prompt` result `usage` | `{inputTokens, outputTokens, cachedReadTokens, cachedWriteTokens, totalTokens}` per turn; `usage_update{used, size, cost{amount, currency}}` notifications (cost is **cumulative** per session) | yes | not reported | notional USD, cumulative |
| agy `-p --output-format json` / `stream-json` (live) | `usage` (also per `step_update`) | `{input_tokens, output_tokens, thinking_tokens, cache_read_tokens, total_tokens}`; `duration_seconds`, `num_turns`, `denied_actions` | read only (0 in these runs; no write field) | `thinking_tokens`, counted inside `output_tokens` (7271 = 6838 + 433, thinking 393) | **none reported** |
| Hermes `--usage-file` (one failed live run) | JSON file | `estimated_cost_usd, cost_status, cost_source, input_tokens, output_tokens, cache_read_tokens, cache_write_tokens, reasoning_tokens, total_tokens, api_calls, model, provider, session_id, completed, partial, interrupted, turn_exit_reason, failed, service_tier, auxiliary{…}` | yes (read + write) | yes (`reasoning_tokens`) | estimated; every value was `null` in the only run (401, `failed:true`), so real values are **not verified** |
| Codex | n/a | not run (logged out). `turn.finished` usage per CONTEXT §7 comes from docs/fakes: **not verified** | | | |

Notes for the metering schema:
- Claude's `input_tokens` excludes cache tokens (4–7 versus 14k–100k cached), so total input = input + cache_read + cache_creation.
- agy reports no price: cost must come from the Farm price table.
- Only Claude (CLI and ACP) reports a dollar figure, and it is notional on subscriptions. Hermes has the field but it was empty.
