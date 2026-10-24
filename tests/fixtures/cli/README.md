# CLI output fixtures (Q0B)

Each case is `<case>.stdout`, `<case>.stderr`, `<case>.exit` (exit code); Hermes cases add `<case>.usage.json` (the `--usage-file`).
Cases marked *live* were recorded 2026-10-24 from the real CLI; the others are verbatim failure texts given in `briefs/Q0B-classify.md`.
Never put anything from a `.env` file here.

- `claude/success` *live* — `claude -p "Reply with exactly: PONG" --model haiku --output-format json`, exit 0, `is_error:false`.
- `claude/success_trigger_words` *live* — answer contains every old trigger word (rate limit, 429, capacity, resets in, /login, 401, 403, unauthorized, quota exceeded, overloaded); still a success.
- `claude/not_logged_in` *live* — `CLAUDE_CONFIG_DIR` an empty temp dir: exit 1, JSON on stdout with `is_error:true`, `result:"Not logged in · Please run /login"`, `terminal_reason:"api_error"`.
- `agy/success` *live* — `agy -p ... --model gemini-3.8-flash-high --output-format json` → `{"status":"SUCCESS","response":"PONG\n",...}`.
- `agy/success_trigger_words` *live* — same with the trigger-word sentence as the answer.
- `claude/oauth_expired` *live* — claude-01 (its stored OAuth login can no longer be refreshed), run through the Farm's own
  runner (clean child environment): exit 1, `is_error:true`, `api_error_status:null`,
  `result:"Failed to authenticate: OAuth session expired and could not be refreshed"`.
- `agy/limit` — quota exhausted: stdout empty, stderr `error: ...` + `AGY_ERROR: {...}`, exit 1.
- `agy/model_rejected` *live* — `--model gemini-3.1-pro-high`, a model this agy no longer knows: exit 1, stdout
  `{"status":"ERROR","error":"invalid model selection ... is not recognized ..."}`, the same text on stderr. No tokens used.
- `agy/models_list` *live* — `agy models`: one `<id>\t<display name>` line per model on stdout, a progress line on stderr.
- `agy/not_eligible` — account not eligible for Antigravity (stderr, exit 1).
- `hermes/auth` — expired provider key: error text on stdout, exit 0, usage file `failed: true`.
- `hermes/model_unavailable_free` *live* — hermes-01 with `openrouter/qwen/qwen3.8-27b:free` (OpenRouter dropped
  the free variant): "Provider said: HTTP 404: This model is unavailable for free ...". The text is the run's
  recorded error (job `ef30a8ce…`, 2026-10-24); exit and usage file as in `hermes/auth`.
- `codex/rollout_error` — Codex session bookkeeping error on stderr; NOT an auth failure.
