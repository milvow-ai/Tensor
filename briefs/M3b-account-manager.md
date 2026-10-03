# Brief M3b — Account Manager: balance sync, pacing, budgets + hard stop, renewals, alerts, Telegram, `farm` admin CLI, command consumer

Rules: follow `briefs/CONTEXT.md` §6. Touch only the files under "Owns". Never git add/commit/push. Never read `.env`. No live provider calls; Telegram is mocked.

## Goal
The "manager" the owner described: it watches every account's usage, balance, spend and renewal, adjusts the machine automatically (pause near the
limit, resume at reset, pace daily spend, hard-stop at budget caps) and tells the owner what needs a human (logins, renewals, upgrades) — HANDOFF §4.4.
It also executes the Console's commands from `farm_commands`, so every Console control works end to end.

## Read first
`briefs/CONTEXT.md`, `HANDOFF.md` §4.4, §4.6, §5.3, `farm/resources/*`, `farm/adapters/*` (`balance()` methods), `farm/registry/sync.py`, `farm/control/cli.py`.

## Owns
`farm/manager/__init__.py`, `farm/manager/balance.py` (sync per connection: adapter `balance()` where it exists → `balance_snapshots` source api; manual entries via CLI; estimated vs actual reconciliation: drift between ledger and provider balance raises an alert above 10 %),
`farm/manager/pacing.py` (daily pacing budget = remaining ÷ days to reset; router consults it: over-pace connections rank last, not blocked),
`farm/manager/budgets.py` (month-to-date spend per provider/connection/global from usage_events + billing_events; forecast = spend ÷ elapsed days × days in month; alerts at thresholds 50/80/100 % once per month per scope; **hard stop**: router policy refuses paid calls (unit_cost > 0) that would cross a hard-stop budget → `policy_block` event and envelope error `budget_exhausted`),
`farm/manager/renewals.py` (from `plan.billing_day`/`renews_on`: reminder 3 days before with usage % and keep/cancel suggestion; upgrades/cancellations become alerts of kind `human_task` with the provider's billing URL; never pays),
`farm/manager/alerts.py` + `farm/manager/telegram.py` (send via Bot API with `env:TELEGRAM_BOT_TOKEN`/`TELEGRAM_CHAT_ID` when set, else log only; dedupe; rate-limit),
`farm/manager/scheduler.py` (asyncio loop or DBOS scheduled workflows — use DBOS `@DBOS.scheduled` if `farm/durable` exists: hourly balance sync, every 5 min reactivate-due/expire reservations, daily budgets + renewals),
`farm/control/commands.py` (consumer: poll `farm_commands` where status queued every 2 s, or LISTEN/NOTIFY; validate payload with Pydantic per kind; execute pause/resume/set_priority/set_strategy/set_budget/add_connection/update_connection/remove_connection/set_route/test_connection/ack_alert against the DB; write result + audit_events; unknown or invalid → `rejected` with reason; `add_connection` refuses an `auth_ref` that is not `env:`/`token-store:`/`cli:` form or looks like a raw secret),
`farm/control/cli.py` (add `farm usage`, `farm pause <conn>`, `farm resume <conn>`, `farm add-connection` (interactive prompts, no secrets), `farm import-csv <file>` (accounts inventory: provider, id, label, plan, price, billing_day, units…), `farm set-secret <ENV_NAME>` (hidden prompt, writes/replaces the line in the repo `.env`, never echoes, prints only "saved"), `farm balance set <conn> <unit> <remaining>`, `farm run` (gateway + manager scheduler + command consumer in one process; this is what runs 24/7)),
`templates/accounts-inventory.csv` (header + one example row per pool type), tests `tests/test_manager_*.py`, `tests/test_commands.py`, `tests/test_accept_m3_manager.py`.

## Acceptance tests
- Budget hard stop: global budget 0, a paid connection (unit_cost 0.01) and a free one → paid never called, `policy_block` logged; raise budget via a `set_budget` command → paid becomes eligible.
- Thresholds: spend crossing 50/80/100 % creates exactly one alert each per month; Telegram mock called once per alert.
- Renewal reminder fires in a time-travel test 3 days before `billing_day`, with usage %; not twice.
- Exhausted connection auto-resumes at its reset time (injected clock).
- Balance: API snapshot + manual snapshot recorded; drift > 10 % → alert.
- Commands: one executed + acknowledged row per command kind; one invalid row rejected with reason; audit row per change.
- `set-secret` writes `.env` (temp path in tests) without echo and never logs the value.

## Done when
`scripts/check.ps1` → `RESULT: all passed`.

## Reply (≤ 15 lines)
Files changed; test count; check tail; deviations.
