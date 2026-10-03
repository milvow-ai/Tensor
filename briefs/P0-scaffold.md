# Brief P0-scaffold — repo scaffold for Harness Farm (Phase 0)

Rules for every step: touch only the files listed under "Owns". Never run git add/commit/push, never deploy, never delete anything outside this repo, never send data off this machine. Do not read or print the contents of any `.env` file.

## Goal
Create the empty, checkable skeleton of the Harness Farm Python package so later milestones only add code. No business logic.

## Read first
`HANDOFF.md` §6 (package pins), §11 (repo structure), §12 (registry.yaml shape). Nothing else.

## Owns (create these; nothing else)
- `pyproject.toml`
- `farm/__init__.py` (`__version__ = "0.0.1"`)
- `farm/{gateway,registry,resources,executors,adapters,knowledge,recipes,policy,control}/__init__.py` (empty)
- `farm/executors/{api,mcp,llm,agent,browser,local,human}/__init__.py` (empty)
- `farm/db/__init__.py`, `farm/db/migrations/.gitkeep`
- `farm/control/cli.py`
- `farm/settings.py`
- `tests/__init__.py`, `tests/test_smoke.py`, `tests/cassettes/.gitkeep`
- `config/registry.yaml`
- `.env.example`

## Steps
1. `pyproject.toml`: project `harness-farm`, `requires-python = ">=3.12,<3.13"`, build backend hatchling, package `farm`. Script entry `farm = "farm.control.cli:app"`.
   Dependencies pinned exactly (`==`): fastmcp 4.0.10, mcp 2.3.0, dbos 3.2.0, crawl4ai 0.9.4, pydantic 2.13.5, httpx 0.28.1, tenacity 9.1.4, aiolimiter 1.3.0, pybreaker 1.4.1, psycopg[binary] 3.3.6, structlog 26.1.0, tldextract 5.3.2, rapidfuzz 3.14.6, typer 0.27.2, pyyaml (>=6), alembic (>=1.14), playwright (>=1.50, unpinned upper).
   Dev group (`[dependency-groups] dev`): pytest, pytest-asyncio, respx 0.23.1, vcrpy 8.3.0, ruff, mypy, types-PyYAML.
   Tool config: `[tool.ruff]` line-length 110, target py312, lint select E,F,I,B,UP; `[tool.mypy]` python_version 3.12, strict = true, packages = ["farm"]; `[tool.pytest.ini_options]` testpaths ["tests"], asyncio_mode "auto".
   If uv reports an unsatisfiable pin, do NOT change the pin silently: keep going with the rest and list the conflict in your reply.
2. `farm/settings.py`: function `load_env(path: Path = repo-root/.env) -> None` — a tiny stdlib parser (KEY=VALUE lines, `#` comments, optional quotes) that sets `os.environ` only for keys not already set. Function `require(name: str) -> str` raising `RuntimeError(f"missing env var {name}")`. Never log values.
3. `farm/control/cli.py`: a typer app with `farm version` (prints `__version__`) and a `db` sub-app with `farm db check`: calls `load_env()`, reads `SUPABASE_DB_URL`, connects with psycopg (connect_timeout=10), runs `select version()`, prints `db ok: <first 40 chars of version>` and exits 0; on any error prints `db FAIL: <exception class name>` and exits 1. Never print the URL or password.
4. `tests/test_smoke.py`: one test that imports `farm`, checks `__version__`, and runs `farm version` via `typer.testing.CliRunner`.
5. `config/registry.yaml`: the HANDOFF §12 shape verbatim, with a top comment `# Shape only — real accounts are filled in at M1/M3.`
6. `.env.example` — names only, every value empty, grouped with comments:
   SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY, SUPABASE_DB_URL, OPENROUTER_API_KEY, GROQ_API_KEY, BIFROST_URL, APOLLO_API_KEY, HUNTER_API_KEY, REOON_API_KEY, ZEROBOUNCE_API_KEY, GOOGLE_PAGESPEED_API_KEY, ADZUNA_APP_ID, ADZUNA_APP_KEY, TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID.
7. Run, from the repo root: `uv sync` then `uv run pytest -q` then `uv run ruff check farm tests` then `uv run mypy`.

## Done when
`uv run pytest -q` passes (1 test), `uv run ruff check farm tests` is clean, `uv run mypy` is clean.

## Reply (≤ 12 lines)
Files created; the last 3 lines of each of the three check commands; any pin conflicts; open issues.
