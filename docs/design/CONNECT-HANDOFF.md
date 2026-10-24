# Connect & Pools — handoff (2026-10-25 00:20, end of the Claude session that built it)

Read with `docs/design/CONNECT.md` (design) and `.orchestrate/state.md` ("Connect & Pools" block).

## Where the code is (all committed locally; nothing pushed, nothing live)

| Branch / worktree | Holds |
|---|---|
| `l1a` / `D:\Harness Farm\wt-l1a` | Sign-in core (migration 0020, `farm/connect/*`, commands, LoginService) + real drivers: Codex device code, Claude paste-back code, agy check. Commits 98da4ca, 7054dfe, 0779b3d, f3d9ea2 |
| `l3` / `D:\Harness Farm\wt-l3` | Dashboard screens (agy-01 built, lead reviewed): AI pools Sign in again / + Add accounts, live sign-in card, sealed API keys. Commit fc4047c |
| **`golive` / `D:\Harness Farm\wt-golive`** | **Ship branch** = `effort2/stage1-saas` + l1a + l3 + `claude/vigorous-mahavira-f03a6f` (false needs_login fixes D52–D57). This file lives here |

## Checks so far

- Python: connect + isolation + driver tests green; full gate on golive (before l3 merge) = 2,543 passed, 2 failed → cause found
  (sign-in tests registered the fake code `wrong` as a secret, masking Hunter's `wrong_params`) and fixed in f3d9ea2.
  Re-run of the full gate on golive was in progress: result in `D:\dev-cache\runs\connect\gate-golive-2.txt` (UTF-8, look for `RESULT:`).
- Console (l3): vitest seal + login 22 passed, biome lint + tsc clean. **Not yet run:** `pnpm check` (incl. `next build`) and
  `pnpm exec playwright test e2e/signin.spec.ts` (needs a build). Run them in `wt-golive\console` (one heavy job at a time: PC memory).

## Owner-approved go-live steps still to do (owner said yes on 2026-10-24)

1. Gate green on golive (Python) + console `pnpm check` + e2e signin spec.
2. Live DB backup — **done**: `D:\farm-data\backups\logical-20261024T182242Z` (35 tables, 2,526 rows, revision 0019).
   `farm db backup` needs pg_dump 17 (the bundled one is 16); the CSV backup script used is in the old session scratchpad — recreate if needed
   (COPY every public table to CSV via psycopg).
3. Fast-forward `effort2/stage1-saas` (main tree `D:\Harness Farm\Tensor`) to `golive`; `uv sync`; `uv run farm db migrate` (→ 0020, expand-only).
4. Restart the Farm (kill the `farm.exe run` process tree, let the HarnessFarm task/watchdog restart it; confirm port 8787 changed owner).
   On start the LoginService publishes `farm_settings.connect_public_key`.
5. Dashboard: locally `console-live` (repo `.claude/launch.json`, port 3100, `pnpm dev --port 3100` in `console/`) — it was NOT running,
   which is why the owner saw ERR_CONNECTION_REFUSED on localhost:3100.
6. Public: Vercel project **harness-farm-console** (team milvow, CLI user milvowai-5995) is linked to `D:\Harness Farm\Tensor\console`,
   env vars NEXT_PUBLIC_SUPABASE_URL / NEXT_PUBLIC_SUPABASE_PUBLISHABLE_KEY / OWNER_EMAIL set for production, domain
   harnessfarm.milvow.com added. Deploy after step 3: `vercel deploy --prod` from `console/`.
   **DNS:** owner added `A harnessfarm → 76.76.21.21` at Namecheap, but at 00:20 Namecheap's own servers (dns1.registrar-servers.com)
   still returned NXDOMAIN → owner should re-check the record is saved (green ✓). Supabase redirect URL
   `https://harnessfarm.milvow.com/auth/callback` added by the owner.

## Accounts (live Farm, fixed by hand in this session)

- codex-01 (WSL codex1): was a false needs_login, now active, test PONG. agy-06, agy-07: resumed, PONG.
- Still to sign in **from the dashboard after go-live**: codex-02, codex-03 (device code; homes `D:\farm-data\ai\codex-0N` exist with
  file-store config), claude-01 (config dir `C:\Users\Amaan\.claude` = the owner's main Claude Code home; paste-code flow; the driver
  replaces only `.credentials.json` + `oauthAccount`), agy-05 (Google verification link → agy check card).

## Known limits / next work

- agy on Windows: login is per Windows user, not per HOME → agy pools need Linux users (WSL now, server later). Dashboard offers a check only.
- WSL-hosted accounts (cli_prefix) can't be signed in from the dashboard yet (driver says so).
- Hermes / MCP OAuth (L2) through the dashboard not built yet. Claude long-lived `setup-token` not adopted (identity unverified).
- Key files under `<data_dir>/keys`: tighten Windows ACL; a missing `connect-salt` regenerates keys (weakens duplicate check).
- Owner preference: no PowerShell/terminal steps for logins, ever; design for pools and for every SaaS user (memory `no-terminal-logins`).
