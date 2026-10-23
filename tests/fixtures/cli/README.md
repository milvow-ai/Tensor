# CLI output fixtures (Q0B)

Each case is `<case>.stdout`, `<case>.stderr`, `<case>.exit` (exit code); Hermes cases add `<case>.usage.json` (the `--usage-file`).
Cases marked *live* were recorded 2026-10-24 from the real CLI; the others are verbatim failure texts given in `briefs/Q0B-classify.md`.
Never put anything from a `.env` file here.

- `claude/success` *live* — `claude -p "Reply with exactly: PONG" --model haiku --output-format json`, exit 0, `is_error:false`.
- `claude/success_trigger_words` *live* — answer contains every old trigger word (rate limit, 429, capacity, resets in, /login, 401, 403, unauthorized, quota exceeded, overloaded); still a success.
- `claude/not_logged_in` *live* — `CLAUDE_CONFIG_DIR` an empty temp dir: exit 1, JSON on stdout with `is_error:true`, `result:"Not logged in · Please run /login"`, `terminal_reason:"api_error"`.
- `agy/success` *live* — `agy -p ... --model gemini-3.8-flash-high --output-format json` → `{"status":"SUCCESS","response":"PONG\n",...}`.
- `agy/success_trigger_words` *live* — same with the trigger-word sentence as the answer.
- `agy/limit` — quota exhausted: stdout empty, stderr `error: ...` + `AGY_ERROR: {...}`, exit 1.
- `agy/not_eligible` — account not eligible for Antigravity (stderr, exit 1).
- `hermes/auth` — expired provider key: error text on stdout, exit 0, usage file `failed: true`.
- `codex/rollout_error` — Codex session bookkeeping error on stderr; NOT an auth failure.
