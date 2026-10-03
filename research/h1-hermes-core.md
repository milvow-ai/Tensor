# Hermes Agent core runtime: integration and control map

Commit c8301ea6 (2026-10-03). Paths are relative to `library/hermes-agent/`; `config_defaults.py` is `hermes_cli/config_defaults.py`; docs are under `website/docs/`. Verified: doc = docs only, source = read in code, both = confirmed.

## Capability inventory

| Capability | How it works | Interface | Source | Verified |
|---|---|---|---|---|
| Agent loop | `AIAgent.run_conversation()` builds the prompt, makes an interruptible model call, runs tool calls (thread pool; `clarify` serial), repeats until a text answer. Planning is model-driven (`todo` tool). | `chat()`, `run_conversation()` | developer-guide/agent-loop.md; agent/conversation_loop.py:1636 | both |
| Iteration and time caps | One `IterationBudget` unit per model call (`execute_code` turns refunded). Stop reason `max_iterations_reached(N/M)`. Source default is UNLIMITED; docs say 500. Wall-clock cap with 80% wrap-up notice. | `AIAgent(max_iterations=, run_budget_seconds=)`, `agent.max_turns`, `agent.run_budget_seconds`, `--max-turns N`, `--run-budget SECONDS` | run_agent.py:268,297; config_defaults.py:78,85; turn_iteration_prep.py:393; turn_finalizer.py:130-138 | source |
| Retry and fallback | API retries, then fallback chain, then timed auto-recovery cycles (jittered 15-60 s). | `agent.api_max_retries` (3), `agent.auto_recovery_cycles` (5), `fallback_providers` | config_defaults.py:135,141; agent_init.py:1463; conversation_loop.py:1506 | both |
| Interrupt and steer | Cooperative interrupt at next safe boundary; steer text lands after the next tool call. | `AIAgent.interrupt()`, `POST /v1/runs/{id}/stop`, `/steer` | interrupt_control.py:13-20; programmatic-integration.md | both |
| Loop guardrails | Warn, then hard-stop on repeated identical or failing calls; per-turn caps. | `tool_loop_guardrails.hard_stop_enabled` (False), `.non_interactive_hard_stop_enabled` (True), `.loop_caps.max_subagents` (50) | config_defaults.py:560-575; agent/tool_guardrails.py | source |
| Delegation | `delegate_task` runs child agents in threads with their own budget; parallel batch; leaf by default. Children inherit parent toolsets minus `delegate_task`, `clarify`, `memory`, `send_message`, `cronjob_manage`. Process-local: `/stop` or restart ends them. Plugin API: `ctx.subagent_lifecycle`. | `delegation.max_concurrent_children` (10), `.max_spawn_depth` (1), `.max_iterations` (250), `.child_timeout_seconds` (0), `.subagent_auto_approve` (False), `.oneshot_max_children` (2) | config_defaults.py:1364-1393; delegate_tool_toolsets.py:14-20; subagent-lifecycle-api.md | both |
| Sessions and memory | SQLite WAL `state.db` per `HERMES_HOME` (sessions, messages, FTS5). `MEMORY.md`/`USER.md` injected into the prompt; agent memory writes are ungated by default. | `--resume`, `session_search`, `memory.write_approval` (False), `memory.memory_enabled`, `AIAgent(skip_memory=True)` | session-storage.md; hermes_state_registry.py:1-20; config_defaults.py:1314-1325 | both |
| Compression | Lossy aux-model summary of middle turns; tail kept; child session lineage. | `compression.threshold` (0.50; config comment floors it at 0.75 for windows under 512K), `.target_ratio` (0.20) | config_defaults.py:579-596; context-compression-and-caching.md | both |
| Cron and pause | Gateway ticks every 60 s; `jobs.json`; fresh session per run; flock `.tick.lock`; script-only jobs. `hermes pause` writes `$HERMES_HOME/ESTOP`: blocks NEW cron/kanban/gateway turns, never kills in-flight. | `hermes cron create`, `cron.max_parallel_jobs`, `HERMES_CRON_TIMEOUT` (600 s idle) | cron/scheduler.py:2,4107-4145; agent/estop.py:1-8; cron.md:338-365 | both |
| Kanban, goals, loops | Kanban: durable SQLite board; dispatcher spawns `hermes -p <assignee> chat -q` workers; heartbeat, crash reclaim, idempotency keys. `/goal`: aux judge, fail-open. `/loop`: in-session cadence. | `kanban.dispatch_in_gateway`, `goals.max_turns` (20), `loops.max_ticks` (100) | kanban.md:126-139,375-398; kanban-worker-lanes.md:33; config_defaults.py:1405-1416 | doc (behaviour), source (keys) |
| Trajectories and audit | ShareGPT JSONL, library flag only. `agent.log` and `errors.log` (5 MB x 3). Session rows. Read-only observer hooks. HMAC-signed outbound webhooks. | `AIAgent(save_trajectories=True)`, `logging.max_size_mb`, `hooks.outbound` | trajectory-format.md; run_agent.py:271; config_defaults.py:2029-2033; observer-hooks.md | both |

## Programmatic integration options

| Interface | How Tensor would call it | Sync / async / streaming | Auth | Structured output? | Concurrency | Stability notes |
|---|---|---|---|---|---|---|
| `hermes -z PROMPT` | Subprocess. Flags `-m`, `--provider` (requires `-m` or `HERMES_INFERENCE_MODEL`, else exit 2), `-t`, `--reasoning`, `--resume`, `--usage-file PATH`. `--in DIR` only changes directory. Final text on stdout. | Sync; no stream | Provider creds in `$HERMES_HOME` | No | One process per call; own `HERMES_HOME` | Forces `HERMES_YOLO_MODE=1`, `HERMES_ACCEPT_HOOKS=1` (oneshot.py:278-282). Exit 0 ok, 2 failed/partial/budget, 1 empty, 130 interrupted |
| `hermes chat -q` or `--query-file PATH` with `--oneshot -Q`, optional `--format stream-json` | Subprocess. Also `--max-turns`, `--run-budget`, `--source tool`, `--ignore-user-config`, `--ignore-rules`, `--checkpoints`. | Sync; JSONL stream | same | No | same | Not yolo: `approvals.single_query_mode` (deny). Exit 0/1/130, 75 for kanban rate-limit. _parser.py:235-292 |
| Python `AIAgent` | `run_conversation(user_message, conversation_history=, task_id=)` returns a dict: `final_response`, `messages`, `api_calls`, `completed`, `partial`, `interrupted`, `turn_exit_reason`, token and cost fields. | Sync; callbacks (`stream_delta_callback`, `tool_start_callback`, `step_callback`) | Args or env | No | One instance per thread | No supported wheel; run from a checkout; ~90 kwargs; `max_iterations` unlimited by default |
| API server | `/v1/chat/completions`, `/v1/responses`, `/v1/runs` (+`/events` SSE, `/approval`, `/steer`, `/stop`), `/api/sessions/*`, `/api/jobs` | Async runs; SSE with 10 s keepalive | Bearer `API_SERVER_KEY`, mandatory and strength-checked, loopback default (api_server.py:1586-1605, 4449-4505) | No `response_format` handling | `gateway.api_server.max_concurrent_runs` (10, then 429); `Idempotency-Key` on `/v1/runs` (24 h) | Platform `api_server` is unattended: `approvals.unattended_mode` (deny) |
| TUI gateway JSON-RPC | stdio or WebSocket; `prompt.submit`, `session.interrupt`, `session.usage`; gateway sends `approval` requests to the host. | Async events | Local IPC; WS clients must call `client.capabilities` | No | Several clients per session | Only 3 methods located in source (methods_prompt.py:657,1002) |
| ACP | `hermes acp` on stdio; the client answers permission requests. | Streaming | Local user | No | Per session | `hermes-acp` toolset includes `terminal` and `execute_code`; a headless auto-approver is unattended execution (acp.md:286-298) |
| `hermes mcp serve` | NOT an agent runner. A 10-tool messaging bridge: `conversations_list`, `conversation_get`, `messages_read`, `attachments_fetch`, `events_poll`, `events_wait`, `messages_send`, `channels_list`, `permissions_list_open`, `permissions_respond`. | Sync | stdio | n/a | n/a | `permissions_respond` only records a decision, "best-effort without gateway IPC" (mcp_serve.py:1-8,333-341,692-704) |
| `batch_runner.py` | Fire CLI: `--dataset_file --batch_size --run_name`, `--max_turns` (10), `--num_workers` (4), `--resume`; JSONL in and out. | Batch | Provider key | No | multiprocessing Pool | Training-oriented: `skip_memory=True`, drops no-reasoning samples (batch_runner.py:24,248-257,865-889) |
| Inbound webhook | `POST :8644/webhooks/<route>`; route keys `secret`, `prompt`, `toolsets`, `deliver_only`, `cron_job`. | Async; result to a sink | HMAC `secret` required; `INSECURE_NO_AUTH` is loopback only | No | Idempotency cache 1 h; body caps | webhook.py:4-9,102-105,213 |
| Hermes as MCP client | Tensor exposes its own MCP server; Hermes loads it from `mcp_servers.<name>`. | Sync tool calls | Per-server `headers`, `auth: oauth` | JSON Schema on tool `inputSchema` | `supports_parallel_tool_calls` | `tools.include`, `tools.exclude`, `trust: untrusted`, `timeout` (mcp-config-reference.md:44-71) |

## Control and policy surfaces

**Can a pre-tool hook deny a call before it executes? YES.** A plugin registers `ctx.register_hook("pre_tool_call", fn)`. Return `{"action": "block", "message": "..."}` to veto (message becomes the tool result), `{"action": "modify", "args": {...}}` to shallow-merge new arguments, or `{"action": "approve", ...}` to push ANY tool into the human-approval gate. Precedence: block over approve. Source: hermes_cli/plugins.py:2038-2090, invoked from model_tools.py:779-790 and agent/tool_executor.py:668-680 before dispatch.
- Python callbacks fail CLOSED: a raise or a timeout (`plugins.hook_callback_timeout`, 30 s) becomes a block (plugins_dispatch.py:49,226-242).
- Shell hooks (`hooks.pre_tool_call[]` with `command`, `matcher`, `timeout`, `fail_closed`) block via stdout JSON or exit code 2 but fail OPEN unless `fail_closed: true` (agent/shell_hooks.py:5,392-431). Consent keys on the command string; script edits are trusted.
- Residual fail-open: if the dispatcher itself raises, args pass through (tool_executor.py:668-680). `tool_request` middleware rewrites args before hooks and approvals and also fails open.
- In-process alternative: `set_thread_tool_whitelist(allowed)` (plugins.py:2026-2035, enforced at 2049-2052).
- `--safe-mode`/`HERMES_SAFE_MODE` disables plugins and shell hooks. Hermes' own SECURITY.md calls hooks and approvals heuristics, not a boundary.

**Approvals.** `approvals.mode` (`smart` default = aux-LLM guardian; `manual`; `off`), `approvals.timeout` (300), `approvals.cron_mode`, `.single_query_mode`, `.unattended_mode` (all `deny`), `approvals.deny` globs, `command_allowlist`, `security.approval.transport` (`builtin`; `transport_fallback` `deny`) (config_defaults.py:1682-1696,1776). Hardline patterns (approval_detection.py:96) and `approvals.deny` fire BEFORE yolo (approval_floors.py:1-55). `--yolo`/`HERMES_YOLO_MODE=1` skips the rest (approval.py:46,323). Docker and cloud backends skip the dangerous-command check (security.md:587-597), but `approvals.deny` still applies. Approvals cover terminal and `execute_code` patterns, ACP edits, MCP `trust: untrusted` writes and hook `approve`; not arbitrary tools.

**Budgets.** Iterations, time, subagent count, `goals.max_turns`, `loops.max_ticks`. `tools/budget_config.py` is a tool-RESULT size budget (100,000 chars per result, 200,000 per turn), not tokens or dollars. No token or cost cap exists (grep of agent/, tools/, gateway/, cron/); cost is reported only. `agent.empty_response_guard.cost_threshold_usd` (0.25) bounds empty-retry spend only.

**Egress and isolation.** `TERMINAL_ENV` (`local`, `docker`, `ssh`, `singularity`, `modal`, `daytona`, `vercel_sandbox`). `hermes egress` (iron-proxy) injects credentials for SANDBOXES only, not host LLM calls (iron-proxy.md:18-23). Compose network isolation leaves DNS open (network-isolation.md:179-195). `security.allow_private_urls` (False), `security.website_blocklist`.

**Managed scope.** `/etc/hermes/config.yaml` and `.env` (`HERMES_MANAGED_DIR`) pin keys per leaf over user config and shell env. Enforcement is file permissions only; the agent's shell can still override env; POSIX-first (managed-scope.md).

**Secrets.** `security.redact_secrets` (True), scrubbed subprocess env, `terminal.env_passthrough`, secret-source plugins, `hermes vault`, credential pools (rotation on 429/402). Plugins and hooks can read in-process credentials (SECURITY.md 2.3).

## Structured output

- The main agent cannot be forced into a JSON Schema. `response_format`, `json_schema` and `output_schema` appear only in auxiliary, TTS and provider code and in delegation; none in `run_agent.py` or `api_server.py`. `--format stream-json` is an event format. Deliverable mode only uploads files whose paths appear in the reply.
- `delegate_task(tasks=[{"output_schema": {...}}])`: the schema is pasted as an OUTPUT CONTRACT; fences and prose are stripped; the parent validates with `jsonschema` and sends exactly ONE retry carrying up to 10 errors (delegation_output_schema.py; delegate_tool_child_run.py:478-518). After a second miss the result stays `status: completed` with `schema_valid: false`, `schema_errors`, `schema_note` and unvalidated text in `summary` (child_run.py:620-640). If `jsonschema` is missing, validation is skipped. A bad schema fails the whole call before any child spawns (delegate_tool_tasks.py:108-120).
- Malformed tool arguments are coerced; corrupted ones are dropped with a marker (run_agent.py:247).
- Inference, not verified: make the schema the `inputSchema` of a Tensor MCP "submit result" tool and validate in Tensor; revalidate any final text.

## Usage and cost reporting

- `hermes -z ... --usage-file PATH` writes JSON, even on failure: `estimated_cost_usd` (null if unpriced), `cost_status`, `cost_source`, `input_tokens`, `output_tokens`, `cache_read_tokens`, `cache_write_tokens`, `reasoning_tokens`, `total_tokens`, `api_calls`, `model`, `provider`, `session_id`, `completed`, `partial`, `interrupted`, `failed`, `turn_exit_reason`, `service_tier`, `failure`. Top level is the main loop only; `auxiliary` adds the same counters plus `by_task`; `total_including_auxiliary` has `estimated_cost_usd`, `total_tokens`, `api_calls` (oneshot.py:27-31,76-90,217-232).
- Persistent: `sessions` totals plus `session_model_usage`, keyed by session, model, billing provider, base URL, billing mode and `task` (empty = main loop), with call count, five token counters, `estimated_cost_usd`, `actual_cost_usd`, `cost_status`, `cost_source` (hermes_state_schema.py:76-95; hermes_state_usage.py:337-420). A background thread coalesces writes; call `flush_token_counts()` before reading.
- Per tool: counts only (`sessions.tool_call_count`; batch `tool_stats` with `count`, `success`, `failure`). No per-tool tokens or cost. Observer hooks give `post_api_request.usage` per call and `post_tool_call.duration_ms`.

## Reliability and recovery

- Messages persist after each turn. Resume reloads the transcript and reopens the ended row (oneshot.py `_load_resume_target`).
- Outcomes: interrupted gives `completed: false, interrupted: true`; budget or truncation gives `partial`; API run states are `completed`, `cancelled`, `failed`, `interrupted`.
- Stalls: delegation heartbeat abandons a child idle 450 s (1200 s inside a tool); cron idle limit 600 s.
- DB contention: 1 s SQLite timeout plus jittered retries (20 s routine, 60 s transcript writes), then `session_persistence_failed:locked` ends the turn.
- Restarts: running delegations become `unknown`, never resumed; kanban reclaims crashed workers; cron has `cron.catch_up_missed` and unreachable retry.
- Isolation: one `AIAgent` per thread; one live agent per `HERMES_HOME` (shared memory writes, profiles.md:14); never two gateway containers on one data dir (docker.md:240); many processes may share `state.db` through WAL; one `kanban.db` dispatcher only (kanban.md:375).

## Security model and declared limitations

- SECURITY.md 2.2: the OS is the only boundary. Approval gate, redaction and allowlists are heuristics. Plugins, skills and hooks run with agent privilege. Single-tenant; all authorised callers are equally trusted. Terminal-backend isolation does not confine `execute_code`, MCP subprocesses or plugins; whole-process wrapping (Docker image, NVIDIA OpenShell) is the supported posture for untrusted input. No bug bounty.
- Declared limits: managed scope v1 is advisory; API server has no file upload and keeps 100 stored responses; background delegation is not durable; external CLI worker lanes are "not yet a paved path".
- Platforms: Windows native 10/11 (shell via Git Bash; `HERMES_HOME` defaults to `%LOCALAPPDATA%\hermes`; cron lock uses msvcrt); WSL2 optional; Docker keeps state in `/opt/data`, and WAL needs a native volume (docker.md:216-225).
- Maturity: MIT (LICENSE; pyproject.toml:17); `version = "0.0.0"`; Python >=3.11,<3.15; no supported wheel or sdist; stable-release pipeline documented (stable-releases.md), cadence not stated; clone is shallow (1 commit, 0 tags). Tests: 5,421 `test_*.py` files, 44,291 `def test_` lines (counted only).

## Unverified

- Doc/source conflicts: default `max_iterations` 500 (agent-loop.md, python-library.md, env-vars `HERMES_MAX_ITERATIONS`) vs unlimited in source; `delegation.max_iterations` 50 (agent-loop.md) vs 250; python-library.md batch flags (`--input`, `--output`) vs `batch_runner.main`; compression 50% (doc) vs 0.75 floor (comment); observer-hooks.md says all hooks fail open, but `pre_tool_call` fails closed.
- Doc only: credential-pool rotation, kanban claim and reclaim, goal judge and gates, TUI gateway catalogue (3 of ~35 methods found), ACP fork and cancel, `stream-json` schema, API `model_options`, Windows feature matrix, release cadence, `tool_request` middleware (only a debug-log hit at model_tools.py:765).
- Nothing was executed. `-z` sets `HERMES_YOLO_MODE` at runtime (oneshot.py:278) while approval.py:46 freezes it at import; import order is unchecked.
- Inferences: cost cut-off via `step_callback` plus `interrupt()`; `request_overrides` carrying `response_format`; whether tool worker threads inherit the thread whitelist.
