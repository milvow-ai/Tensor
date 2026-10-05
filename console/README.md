# Harness Farm Console

The control room for the Harness Farm: full owner control across tool pools (Clay, Reoon, ZeroBounce, Apollo, Hunter), AI pools (Claude, Codex, Gemini, Hermes), MCP servers (Notion, GitHub, Linear, stdio), Routing rules, Memory & Evidence, Policies & Budgets, and System Settings.

## Architecture

```
Console (Next.js on Vercel)  --reads-->   Supabase Postgres: v_* views, alerts, farm_commands
                             --inserts--> farm_commands (RLS: owner only)
Farm (Local PC Daemon)       --polls-->   farm_commands, validates, executes, writes result back
```

- **Zero inbound connections to the PC, zero provider secrets in the Console.** Connections store an `auth_ref` (`env:GITHUB_TOKEN`, `cli:claude-01`); the secrets live exclusively on the local PC or token store (`farm set-secret <NAME>`, `farm mcp login <ID>`).
- **Reads** execute through optimized SQL views (`views.sql` and `views_c3.sql`): `v_connection_status`, `v_pool_overview`, `v_spend_month`, `v_routes`, `v_facts`, `v_evidence`, `v_spend_daily`, `v_cost_per_result`, etc.
- **Writes** are asynchronous commands enqueued in `farm_commands`. The Console monitors status (`queued` → `running` → `done`/`failed`) and reconciles data on completion.

---

## Running Locally

### 1. Install dependencies
```bash
pnpm install
```

### 2. Environment configuration
Create `console/.env.local`:
```bash
cp .env.example .env.local
```

Configurable environment variables:
| Variable | Required | Default | Description |
|---|---|---|---|
| `FARM_DATA_SOURCE` | Optional | `supabase` | Set to `fixtures` for local offline development; defaults to `supabase`. |
| `NEXT_PUBLIC_SUPABASE_URL` | For `supabase` mode | — | Supabase project URL. |
| `NEXT_PUBLIC_SUPABASE_PUBLISHABLE_KEY` | For `supabase` mode | — | Supabase anon/publishable key. |
| `OWNER_EMAIL` | Optional | `owner@example.com` | Allowed owner email for authorization. |
| `FARM_TIMEZONE` | Optional | `UTC` | Timezone used for daily budget resets and reporting. |

### 3. Start development server
```bash
pnpm dev
```
Open `http://localhost:3000`. In `fixtures` mode, the yellow "Fixtures" badge is visible and authentication is bypassed.

---

## Security Model

1. **No Client Secrets:** Provider API keys and credentials are never stored in Supabase or delivered to the client. All integrations reference an `auth_ref`. The UI rejects raw secret pastes (`sk-...`, JWTs, passwords) with `SECRET_REFUSAL`.
2. **Build-Time Guard:** `next.config.mjs` enforces that `FARM_DATA_SOURCE=fixtures` is impossible in production builds (`NODE_ENV=production` without explicit `FARM_E2E=1`). A production build will abort immediately if fixtures are requested.
3. **HTTP Security Headers:**
   - **Content-Security-Policy (CSP):** Strict production policy without `unsafe-eval`, restricts script execution, connections, fonts, and images.
   - **Frame Protection:** `frame-ancestors 'none'` and `X-Frame-Options: DENY` prevent clickjacking.
   - **MIME Sniffing & Referrer:** `X-Content-Type-Options: nosniff` and `Referrer-Policy: strict-origin-when-cross-origin`.
4. **Row-Level Security (RLS):** Console user actions can only insert commands for their own verified owner session. Admin actions and tool execution run locally on the Farm daemon.

---

## Deploy Steps for the Lead

1. **Database Migrations:**
   Ensure C2 and C3 views are applied in Supabase Postgres:
   ```bash
   # Apply console/sql/views.sql and console/sql/views_c3.sql
   ```
2. **Verify Quality Gates locally:**
   ```bash
   pnpm check       # Biome lint + TypeScript type-check + Next.js build
   pnpm bundle      # Validate bundle sizes remain under limits
   pnpm test:e2e    # Playwright end-to-end tests
   ```
3. **Configure Vercel Project Settings:**
   - **Framework Preset:** Next.js
   - **Root Directory:** `console`
   - **Build Command:** `pnpm build`
   - **Install Command:** `pnpm install`
   - **Environment Variables:**
     - `NEXT_PUBLIC_SUPABASE_URL`: Production Supabase URL
     - `NEXT_PUBLIC_SUPABASE_PUBLISHABLE_KEY`: Production Supabase anon key
     - `OWNER_EMAIL`: Owner's verified email address
     - `FARM_TIMEZONE`: e.g. `America/New_York` or `UTC`
     - *(Ensure `FARM_DATA_SOURCE` is NOT set to `fixtures`)*
4. **Deploy:**
   Trigger deployment via Vercel Git integration or:
   ```bash
   vercel --prod
   ```
5. **Post-Deploy Sanity Check:**
   - Access production URL; verify redirect to `/login`.
   - Sign in via magic link using the configured `OWNER_EMAIL`.
   - Verify `/settings` displays `Source: supabase` and `Healthy` daemon status.
   - Test dispatching a test command (e.g. acknowledge an alert or toggle routing priority).

---

## Supported Commands

| Kind | Payload | Purpose |
|---|---|---|
| `set_route` | `{ capability, provider_id, position?, enabled? }` | Reorder pool priority or toggle pool participation per capability |
| `set_strategy` | `{ strategy, provider_id?, capability? }` | Set routing strategy (`failover`, `most_remaining`, `round_robin`, `parallel_split`, `sticky`, `fit_check`) |
| `set_budget` | `{ scope, monthly_usd, hard_stop?, ref? }` | Configure global or per-provider spending caps and enforcement |
| `add_connection` | `{ provider_id, id, auth_ref, label?, ... }` | Register MCP server, OpenAPI service, or AI account |
| `update_connection` | `{ connection_id, label?, ... }` | Modify account or connection parameters |
| `remove_connection` | `{ connection_id }` | Unlink connection from registry |
| `test_connection` | `{ connection_id, capability? }` | Dispatch test ping through local Farm daemon |
| `pause` / `resume` | `{ connection_id, reason? }` | Temporarily suspend or restore an account |
| `ack_alert` | `{ alert_id }` | Acknowledge active system warning |
| `cancel_ai_job` | `{ job_id }` | Cancel an in-flight AI run / job |
| `set_max_parallel` | `{ connection_id, max_parallel }` | Update account concurrency limit |
| `set_mcp_tool_access` | `{ server_name, tool_name, allowed }` | Allow or deny specific MCP server tool |
| `sync_mcp_tools` | `{ server_name? }` | Refresh tool schemas from MCP servers |


