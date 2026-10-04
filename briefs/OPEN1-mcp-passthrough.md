# Brief OPEN1 — any MCP server through the Farm, as-is (pass-through, import, discovery)

Rules: follow `briefs/CONTEXT.md` §6. Touch only the files under "Owns". Never git add/commit/push. Never read `.env`. Run commands in the foreground and report once.

## Goal (owner, 2026-10-05: the Farm is a general product, not a GTM tool)
Harness Farm is **one MCP connection that fronts every MCP server and AI CLI the owner uses**. Any MCP server that Claude or Codex can use (Notion, GitHub, Clay, Linear, filesystem, custom servers, …) must be addable **with config only, no code**, and its tools must reach the calling AI **exactly as the original server returns them**, while still getting the Farm's value: several accounts per server, pools and strategies, quotas/cooldowns/circuit, budgets, run history, secrets kept in the local `.env`.
Typed capabilities (`verify_email`, …) stay as an optional layer for cross-provider failover; pass-through does not need them.

## Read first
`briefs/CONTEXT.md`, `farm/registry/models.py`, `config/registry.yaml` (Clay block), `farm/executors/mcp/` (M3c-A client + token store + fake MCP server in tests), `farm/gateway/server.py`, `farm/resources/router.py` (public API only), `farm/secrets.py` (`set-secret` path), `farm/control/cli.py`.

## Reuse first (mandatory — owner 2026-10-05: do not hand-roll what FastMCP 4 already ships)
Build on the pinned FastMCP (`library/fastmcp`, docs under `library/fastmcp/docs/servers/`), wiring its primitives to the Farm's router/ledger/run history instead of writing parallel versions:
- **Proxying any server:** `providers/proxy.mdx` (MCP Proxy Provider, `create_proxy`) — one proxy client per Farm connection (account); a small `WrappedProvider` (`fastmcp.server.providers.wrapped_provider`) or gateway middleware picks the connection per call via the router.
- **Server definitions + import parsing:** `fastmcp.mcp_config` (`MCPConfig`, the standard `mcpServers` format Claude uses) and `fastmcp.client.transports.config`; only the Codex TOML reader and secret extraction are ours.
- **Namespacing / allow-deny:** `transforms/namespace.mdx` (`Namespace`), `servers/visibility.mdx` + `transforms/visibility`.
- **Discovery:** `transforms/tool-search.mdx` (BM25 search transform → synthetic `search_tools` + `call_tool`). Use it instead of writing `find_tools`/`describe_tool` (rename item 3 accordingly).
- **Extra `_farm` argument / arg stripping:** `transforms/tool-transformation.mdx` (`ToolTransform`).
- **OAuth MCPs (Notion, Linear, …):** `fastmcp.client.auth.oauth` + `oauth_callback`, storing tokens in the M3c-A per-connection token store.
- **HTTP APIs with an OpenAPI spec:** `fastmcp.server.providers.openapi` — add `executor: openapi` providers (spec URL/path + `env:` auth ref) the same way, same router path.
- Patterns only (read, do not vendor): `library/mcp-context-forge` (IBM MCP gateway: federation, per-tool enable/disable, health).
Our own code is limited to: registry/config models, secret extraction to `.env`, Codex TOML import, the per-call account selection, router/ledger/run-row wiring, CLI and tests. If a FastMCP API does not do what the brief needs, say so in the reply instead of re-implementing it silently.

## Owns
`farm/registry/models.py` (extend), `config/registry.yaml` (add an example `fake-mcp` provider only if tests need it — keep the real file loading), `farm/mcp/` (new package: `importer.py`, `sync.py`, `passthrough.py`), `farm/gateway/mcp_tools.py` (new; `server.py` gets one registration call), `farm/gateway/server.py` (only: that call + remove test-name hooks, see 6), `farm/executors/mcp/*` (extend for raw results), `farm/control/cli.py` (new `farm mcp` sub-commands), one new Alembic migration (next free number in `farm/db/migrations/versions/`), tests `tests/test_mcp_import.py`, `tests/test_mcp_sync.py`, `tests/test_mcp_passthrough.py`, `tests/test_accept_open1.py`, test fixtures under `tests/fixtures/mcp/`.

## Build
1. **Registry: MCP providers by config.** A provider with `executor: mcp` gets an `mcp:` block: `transport: stdio|http|sse`, `command`, `args`, `env` (values are `env:NAME` refs only), `url`, `headers` (refs only), `auth: none|env|oauth` (oauth = the M3c-A per-connection token store), `namespace` (default = provider id, slug), `tools: {allow: [glob], deny: [glob]}`, `expose: direct|discovery|auto` (default auto). Connections = accounts; each connection may override `env`/`headers`/token store. Validate: no literal secret values anywhere in the registry (reject strings that look like keys/tokens; only `env:` refs).
2. **Sync.** `farm mcp sync [provider]` (and once at `farm serve` start, non-blocking, cached): connect through the first healthy connection, `tools/list` (with pagination), store per provider in a new table `mcp_tools(workspace_id default, provider, name, description, input_schema jsonb, output_schema jsonb null, annotations jsonb, schema_hash, synced_at)`; apply allow/deny. Changed schema → new hash, logged. A server that fails to start → provider marked unhealthy, others unaffected.
3. **Expose.** `direct`: every allowed tool is registered on the Farm gateway as `<namespace>__<tool>` (names `^[a-zA-Z0-9_-]{1,64}$`; truncate+hash if longer) with the **original description, input schema and annotations unchanged**, plus one optional extra argument `_farm: {account?, strategy?, timeout_s?}` that is stripped before forwarding. `discovery`: three meta-tools instead — `find_tools(query, provider?, limit≤20)` (keyword match on name+description, returns name, description, input schema), `describe_tool(name)`, `call_tool(name, arguments, account?)`. `auto`: direct when the total exposed tools ≤ `settings.mcp_direct_limit` (default 40), otherwise discovery **plus** direct for tools listed in `settings.mcp_pinned`. Discovery meta-tools always exist so any AI can reach everything.
4. **Call path.** Every pass-through call goes through the router as capability `mcp:<provider>` (pool, strategy, health/circuit, cooldown, quota unit `calls` unless the provider defines units, budget, run row with provider/tool/account/duration/status — never the arguments' secret values; redact). Forward to the chosen connection and **return the remote `CallToolResult` unchanged**: all content blocks (text, image, audio, resource, resource_link), `structuredContent`, `isError`. Remote `isError` is a tool result, not a Farm failure (no failover); transport/auth/limit errors are Farm failures → next account per strategy, and the error names the account and kind.
5. **Import from Claude and Codex.** `farm mcp import --from claude-desktop|claude-code|codex|file:<path> [--dry-run] [--only name,…]`: read `%APPDATA%\Claude\claude_desktop_config.json`, `~/.claude.json` (top-level and per-project `mcpServers`), `~/.codex/config.toml` (`[mcp_servers.*]`). For each server write a provider block into the registry (one connection `<ns>-01`). **Literal secret values found in `env`/`headers` go to the local `.env` as `FARM_MCP_<NS>_<VAR>` through the same no-echo code path as `farm set-secret`; the registry stores only the `env:` ref.** Dry-run prints server names, transport and the env-var *names* only, never values. Skip duplicates; report what was skipped.
6. **Remove test hooks from production.** `farm/gateway/server.py` has test-name checks (`legacy_patterns`, `is_legacy_m1`, reading the current test name). Delete them and fix the affected tests instead (same rule SEC1 applied to the CLI drivers).

## Acceptance tests (no network; use the existing fake MCP server, extend it with an image block, `structuredContent` and an `isError` tool)
- Import from fixture configs of all three formats: providers created, a literal token ends in a temp `.env` as a ref, **the token string appears nowhere in the registry, logs or run rows**; dry-run output has no values.
- Sync stores tools; deny-list hides a tool; changed schema bumps the hash.
- Direct mode: `fake__echo` is listed with byte-identical description and input schema; calling it returns content blocks identical to calling the fake server directly (compare JSON), including the image block and `structuredContent`.
- Discovery mode: `find_tools("echo")` → schema; `call_tool` → identical result.
- Two connections, strategy `round_robin`: calls alternate; one connection fails at transport → call succeeds on the other, run row shows both attempts; remote `isError` result is returned as-is with no failover.
- A provider whose server cannot start does not break `farm serve` or other providers.
- No test-name checks remain in `farm/` (grep).

## Done when
`powershell -File scripts/check.ps1` → `RESULT: all passed`. Reply ≤ 15 lines: files, test count, check tail, the exact registry YAML for one imported server, deviations.
