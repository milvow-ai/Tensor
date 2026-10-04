# Brief FIX1 — Windows event-loop compatibility for AI CLI runner; fsync policy for local Postgres

Rules: follow `briefs/CONTEXT.md` §6 and §0. Touch only the files under "Owns". Never git add/commit/push. Never read `.env`.

## Why
1. The Farm process must run asyncio on a **SelectorEventLoop** on Windows because psycopg async does not support the Proactor loop. `asyncio.create_subprocess_exec` does not work on a SelectorEventLoop on Windows. `farm/executors/cli_agent/base.py` currently spawns the AI CLIs with asyncio subprocesses, so it would fail inside the real Farm process.
2. `farm/db/local.py` writes `fsync = off` for the embedded Postgres. That is fine for the throwaway **test** data dir (`<FARM_DATA_DIR>/pgtest`), but the owner's **runtime** local DB (`<FARM_DATA_DIR>/pg`) must survive power loss — the PC already lost power once today.

## Owns
`farm/executors/cli_agent/base.py`, `tests/test_cli_agent_base.py` (extend), `farm/db/local.py` (fsync policy only — do not change its crash-recovery logic), `tests/test_db_local_fsync.py` (new).

## Steps
1. Rewrite the runner in `cli_agent/base.py` to run each CLI via a worker thread (`asyncio.to_thread`) around `subprocess.Popen` with: argv list (no shell), cwd, env overlay, stdin closed, stdout/stderr captured with size caps, wall-clock timeout → kill the **whole process tree** (Windows: `taskkill /T /F /PID`, else process group kill) and return TIMEOUT, cancellation of the awaiting task also kills the tree. Keep the public API and every existing behaviour/test of the cli_agent drivers unchanged.
2. Add a test that runs the runner under an explicit `asyncio.SelectorEventLoop` on Windows (and the default loop elsewhere): a fake CLI succeeds; a fake CLI that sleeps beyond the timeout is killed together with a child process it spawned (assert the child PID is gone); cancellation kills the tree.
3. `local.py`: apply `fsync = off` (and any other durability-reducing setting) **only** when the data dir is the test dir (explicit parameter, e.g. `durable: bool`), never for `<FARM_DATA_DIR>/pg`. Test: the runtime data dir's managed config has fsync on; the test dir's has it off.

## Done when
`powershell -NoProfile -ExecutionPolicy Bypass -File scripts/check.ps1` → `RESULT: all passed`.

## Reply (≤ 10 lines)
Files changed; tests added; check tail; deviations.
