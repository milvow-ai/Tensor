# Brief M3c — MCP resource pools: Clay (×7 accounts) and any MCP server via the FastMCP proxy, per-account OAuth token stores

Rules: follow `briefs/CONTEXT.md` §6. Touch only the files under "Owns". Never git add/commit/push. Never read `.env`. Never read token-store
contents into logs/output. No live Clay calls (accounts are not logged in yet); use a local fake MCP server in tests.

## Goal
Many of the owner's tools (Clay first) are reachable only as remote MCP servers with OAuth per account. The Farm mounts each account as its own MCP
client connection (FastMCP client/proxy) with **its own token store**, maps the server's tools to Farm capabilities, and routes across the pool like
any other resource — so "7 Clay accounts" become one pool with failover, quotas and stickiness. Any other MCP server can be added the same way from config
(and later from the Console's Integrations screen).

## Read first
`briefs/CONTEXT.md`, `HANDOFF.md` §4.6 (integrations by config), §13 (Clay risk), `library/fastmcp/docs` (client, transports, OAuth client auth +
token storage, proxy/mounting), the Clay MCP tool names visible to the owner (search-companies, search-contacts, search-contacts-by-name,
add-company-data-points, add-contact-data-points, query-objects, list_subroutines, run_subroutine, get-current-workspace…), Clay's public MCP docs for
the server URL and auth (fetch; record the URL; if not public, make the URL a per-provider config value `meta.server_url` and mark UNVERIFIED).

## Owns
`farm/executors/mcp/__init__.py`, `farm/executors/mcp/client.py` (`McpExecutor`: per-connection FastMCP client with OAuth token storage under
`FARM_DATA_DIR/tokens/<connection_id>/` (directory ACL: current user only on Windows if feasible), connect/reconnect, call tool with timeout, map MCP
errors and tool error payloads → ErrorKind; auth expiry → NEEDS_LOGIN), `farm/executors/mcp/mapping.py` (config-driven tool→capability mapping with
param/result transforms: `meta.tool_map: {find_person: {tool: search-contacts, params: {...jinja-free simple field mapping...}, result: {...}}}`),
`farm/secrets.py` (extend `resolve_auth` for `token-store:<id>` → path to the store, never the token), CLI `farm mcp login <connection>` (runs the
OAuth browser flow for that one account and stores tokens in its own store), `farm mcp tools <connection>` (lists remote tools), `config/registry.yaml`
(clay provider: `meta.server_url`, `tool_map` for find_person/enrich_company/find_email using Clay's tools; strategy sticky), `tests/fake_mcp_server.py`
(a FastMCP server exposing Clay-like tools with configurable credits/errors), tests `tests/test_mcp_executor.py`, `tests/test_accept_m3_mcp_pool.py`.

## Acceptance tests
- A pool of 3 fake Clay accounts (each its own fake server instance/token store): `find_person` routes to account 1; account 1 returns an auth error → `needs_login` + alert with the exact `farm mcp login clay-01` command; request served by account 2.
- Credits exhausted on account 2 (tool error payload) → LIMIT_REACHED → account 3.
- Token stores are separate directories; no token value appears in logs, run_events or envelopes (sentinel assertion).
- A generic non-Clay fake MCP server added purely via registry config becomes callable as a capability with no code change.

## Done when
`scripts/check.ps1` → `RESULT: all passed`.

## Reply (≤ 15 lines)
Files changed; test count; check tail; Clay MCP facts (URL, auth, tool names/shapes, credit signals) + what is UNVERIFIED; deviations.

## Phase A (run now, before the router lands)
Do ONLY: `farm/executors/mcp/{__init__,client,mapping}.py`, `farm/secrets.py` token-store extension (append; do not change existing behaviour or tests), `tests/fake_mcp_server.py`, `tests/test_mcp_executor.py` (executor-level: per-account token-store dirs, tool call, error/credit/auth mapping, sentinel no-leak, generic non-Clay server via mapping config), and the Clay MCP facts (URL, auth, tools, credit signals).
Not in Phase A: registry.yaml, CLI commands, router/acceptance tests (Phase B).
Done when: `uv run pytest tests/test_mcp_executor.py tests/test_secrets.py -q` passes and ruff + mypy are clean.
