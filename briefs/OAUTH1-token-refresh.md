# Brief OAUTH1 — OAuth MCP accounts must stay logged in (token refresh after restart)

Rules: follow `briefs/CONTEXT.md` §6. Touch only the files under "Owns". Never git add/commit/push. Never read `.env` or token stores' contents.
Run every command in the foreground and wait for it; never start one in the background and poll it. Report once. A real `farm run` serves on
127.0.0.1:8787 with data in D:/farm-data: tests never use that port or those files.

## Bug (verified by the lead, 2026-10-06)
Clay accounts logged in with `farm mcp login` worked, then after the access token expired the Farm logged `Token refresh failed: 404` and marked
both accounts `needs_login`. Cause: in a new process the MCP SDK's `OAuthClientProvider` has tokens from storage but no `oauth_metadata`, so
`_get_token_endpoint()` falls back to `urljoin(auth_base_url, "/token")` = `https://api.clay.com/token` (404). Clay's metadata
(`https://api.clay.com/.well-known/oauth-authorization-server`) says `token_endpoint: https://api.clay.com/oauth/token`. Every OAuth MCP whose
token endpoint is not `/token` breaks ~1 hour after login.

## Owns
`farm/executors/mcp/connect.py` (`HeadlessOAuth` / token storage wiring), `farm/executors/mcp/*` token-store helpers if needed,
tests `tests/test_mcp_oauth_refresh.py`.

## Build
1. Make refresh use the server's real token endpoint in every process: when tokens are loaded from the store and no `oauth_metadata` is set,
   discover it first (protected-resource metadata → authorization-server metadata, the same discovery the SDK runs on a fresh login) and/or persist
   the discovered metadata next to the tokens at login and load it with them. Prefer the SDK's own discovery helpers over re-implementing; do not
   depend on private SDK attributes if a public path exists — if only a private one exists, isolate it in one small function with a comment.
2. A refresh that fails with 400/401 `invalid_grant` → `needs_login` (as today). A refresh that fails with 404/5xx/network → `transient`
   (cooldown + retry), never `needs_login`, and the log names the token URL (no secrets).
3. Log the refresh outcome once per connection (`mcp.oauth_refreshed` / `mcp.oauth_refresh_failed` with status and token host).

## Acceptance tests (no network: fake authorization server with a non-default token path, e.g. `/oauth/token`)
- Login stores tokens; a new client instance (simulating a restart) with an expired access token refreshes via `/oauth/token` and the call succeeds.
- Fake server returning 404 on the token path → connection becomes `transient`, not `needs_login`; `invalid_grant` → `needs_login`.
- Existing MCP/OPEN1/OPEN2 tests still pass.

## Done when
`powershell -File scripts/check.ps1` → `RESULT: all passed`. Reply ≤ 8 lines: the fix, files, tests, gate tail.
