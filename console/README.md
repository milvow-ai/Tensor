# Harness Farm Console

The control room for the Harness Farm: see every tool pool (Clay, Reoon, ZeroBounce, Apollo, Hunter) and AI pool (Claude, Codex, Gemini, Hermes), every account in it, its usage, limits, billing and health, and pause, resume, reprioritise, change strategy or add accounts.

## How it works

```
Console (this app, Vercel)  --reads-->   Supabase Postgres: v_* views, alerts, farm_commands
                            --inserts--> farm_commands (RLS: the owner may insert only there)
Farm (the PC)               --polls-->   farm_commands, validates, executes, writes the result back
```

- **No inbound connection to the PC, no provider keys in the Console.** An account stores an `auth_ref` (`env:CLAY_KEY_08`, `cli:claude-02`); the secret itself lives in the PC's `.env` (`farm set-secret <NAME>`).
- **Reads** go through five SQL views in `sql/views.sql` (`v_connection_status`, `v_pool_overview`, `v_spend_month`, `v_capability_capacity`, `v_recent_runs`). Aggregation happens in SQL, never in the browser.
- **Writes** are commands. The UI shows each one as queued, running, then done or rejected (with the Farm's reason), and refreshes from the database when it settles.

### Command payloads

| kind | payload |
|---|---|
| `pause`, `resume`, `test_connection`, `remove_connection` | `{ connection_id }` |
| `set_priority` | `{ connection_id, priority }` |
| `set_strategy` | `{ provider_id, strategy }` (failover, most_remaining, round_robin, parallel_split, sticky, fit_check) |
| `add_connection` | `{ provider_id, connection: { id, label, auth_ref, scope, priority, concurrency, plan: { name, price_usd, billing_day }, meta, units: { <unit>: { limit, period, anchor, charged_on } } } }` |
| `ack_alert` | `{ alert_id }` |

The zod schemas are in `src/lib/farm/commands.ts`; the server action validates every payload again before it is queued.

## Running it

```bash
pnpm install
cp .env.example .env.local   # or create it: see below
pnpm dev
```

`.env.local` (public values only):

```
NEXT_PUBLIC_SUPABASE_URL=...
NEXT_PUBLIC_SUPABASE_PUBLISHABLE_KEY=...
FARM_DATA_SOURCE=fixtures    # local development only; omit (or "supabase") for the real Farm
```

- **`supabase` (default):** magic-link sign-in at `/login`; after sign-in the server checks that the email equals `farm_settings.owner_email`, otherwise `/unauthorized`. Every server action checks it again.
- **`fixtures`:** demo data built in memory (the real pools, a few unhealthy accounts, 40 runs) and a command queue that completes after 1.5 s. No login. A yellow "Fixtures" badge is always visible. The server refuses to start with fixtures when `NODE_ENV=production`.

## Pages

Overview, Tools & Pools (`/pools/tools`, `/pools/[id]`), AI Pools (`/pools/ai`, `/pools/ai/[id]`) are complete. Runs, Routing, Policies & Budgets, Billing, Memory & Evidence, Integrations and Settings are designed placeholders that say what they will do and when (C2 or C3). Pool pages refresh by polling every 5 seconds while the tab is visible.

## Quality gates

```bash
pnpm check       # biome lint + tsc --noEmit + next build
pnpm bundle      # fails if any route's first-load JS is over 250 kB gzip
pnpm test:e2e    # Playwright, production build, fixtures mode (run pnpm check first)
```

The e2e run covers: every page without console errors at 1440 and 390 px (screenshots in `D:\dev-cache\shots\C1\`), no horizontal scroll, pause and resume through the queue, a Farm rejection, inline priority, strategy, pagination and sorting, the add-account forms (including a pasted-secret refusal and a duplicate id), alert acknowledgement, WCAG AA contrast in both themes (axe), and the production fixtures guard.
