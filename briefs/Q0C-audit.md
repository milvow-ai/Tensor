# Q0C — Read-only defect audit by area

Rules: this is a **read-only** audit. The only file you may create or change is `docs/audits/Q0-audit.md`. Reproductions go
under `D:/dev-cache/runs/q0c/repro/` (outside the repo). Never run `git add / commit / push / reset / checkout / stash`, never
read or print any `.env` file, never run anything against a live database or the live Farm (`127.0.0.1:8787`). Run every
command in the foreground.

**Goal.** Find defects before we build on top of them. The owner's rule: no new building on a known defect. Two classes are
already known and being fixed elsewhere (`docs/QUALITY.md` D1–D3): do not re-report them. Look for what nobody has seen yet.

**Read first:** `briefs/CONTEXT.md` · `docs/QUALITY.md` · `docs/ARCHITECTURE.md` §5–6 · then the code named per area.

**Areas and checklist (in this order):**
1. **Router and ledger** (`farm/resources/router.py`, `ledger.py`, `flight.py`, `health.py`): every reservation is committed or
   released on every path (success, failure, timeout, cancellation, process death → TTL); `used + reserved` never exceeds a
   limit; only `found = true` answers are cached; single-flight joins return the same outcome; `pin` skips cache and
   single-flight but keeps eligibility; fallback order matches the route.
2. **AI jobs** (`farm/ai/jobs.py`, `accounts.py`, `failures.py`): guarded state transitions; heartbeat-lease recovery;
   `ai_cancel` kills the process tree and settles quota; `max_parallel` per account and pool; `retry_other_account`; how a
   failed result changes an account's status (interplay with D1).
3. **MCP relay and OAuth** (`farm/executors/mcp/*`): path failure vs a tool's own `isError`; concurrent token refresh; secrets
   never in error strings.
4. **Command consumer** (`farm/control/commands.py`): can two processes execute the same command; validation; audit rows.
5. **Scheduler and alerts** (`farm/manager/*`, `farm/control/alerts.py`): duplicate runs across processes; reactivation logic.
6. **Console numbers** (`console/sql/*`, `farm/db/migrations/versions/0003_*, 0006_*, 0009_*`): pick three dashboard numbers and
   check their SQL against the source tables for double counting, time-zone and period-boundary errors.

**Method:** read the code, run the existing tests that touch the area, and where a finding is real write the smallest
reproduction you can (a pytest file or a command) under `D:/dev-cache/runs/q0c/repro/`. A finding without evidence is marked
"suspected".

**Output — `docs/audits/Q0-audit.md`:** a ranked table (most severe first, at most 25 rows): id · area · severity (critical /
major / minor) · finding (one sentence) · evidence (`file:line`) · reproduction (path or command) · suggested fix (one line) ·
confidence (confirmed / suspected). Then a short "areas checked with no findings" list.

**Owns:** `docs/audits/Q0-audit.md` (new). Nothing else.

**Done when:** the report exists, every "confirmed" finding has a reproduction, and `git status` shows no other change.

**Reply (15 lines or fewer):** counts by severity · the top five findings in one line each · report path.
