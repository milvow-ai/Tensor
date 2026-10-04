# AGENTS.md

## Project overview

The Harness Farm Console is the owner's control room for the Farm: every tool pool and AI pool, every account in it, its usage, limits, health and spend. It is a Next.js 16 / React 19 / TypeScript / Tailwind v4 / shadcn/ui app (started from the arhamkhnz admin dashboard; its MIT license is in `LICENSE`).

The Console never holds a provider secret and never talks to the Farm directly. It reads Postgres (Supabase) through SQL views and writes commands into `farm_commands`; the Farm, running on the owner's PC, validates and executes them. See `README.md` for the data flow.

<!-- BEGIN:nextjs-agent-rules -->

# This is NOT the Next.js you know

This version has breaking changes — APIs, conventions, and file structure may all differ from your training data. Read the relevant guide in `node_modules/next/dist/docs/` (resolved from this file's directory; in monorepos the `next` package may not be visible from the repo root) before writing any code. Heed deprecation notices.

This block is written and re-added by `next dev` — verify at `node_modules/next/dist/server/lib/generate-agent-files.js`. Removing it from a diff only re-creates the uncommitted change; committing it with your work keeps the tree clean.

<!-- END:nextjs-agent-rules -->

## shadcn skill

Use the shadcn skill for all work involving shadcn/ui components, styling, composition, registries, presets, or `components.json`. Always inspect the local component source before using it. Do not modify files inside `src/components/ui/`; apply styling and behaviour where the components are used.

## Setup and checks

This project uses pnpm (store `D:/dev-cache/pnpm-store` on the Farm PC).

```bash
pnpm install
pnpm dev                 # FARM_DATA_SOURCE=fixtures in .env.local gives demo data; no login
pnpm check               # biome lint + tsc --noEmit + next build
pnpm bundle              # first-load JS per route must stay under 250 kB gzip (run after a build)
pnpm test:e2e            # Playwright against `next start` in fixtures mode (needs a build first)
pnpm check:fix           # biome check --write
```

Browsers for Playwright: `PLAYWRIGHT_BROWSERS_PATH=D:\dev-cache\ms-playwright`. Screenshots land in `D:\dev-cache\shots\C1\` (override with `FARM_SHOTS_DIR`).

## Structure

- `src/app/(main)/` the signed-in shell (sidebar, header) and every page: `overview/`, `pools/` (tools, `[id]`, `ai`, `ai/[id]`), placeholder pages for later milestones.
- `src/app/(public)/` login and unauthorized. `src/app/auth/` magic-link callback and sign-out. `src/proxy.ts` refreshes the Supabase session and redirects signed-out visitors.
- `src/lib/farm/` the data layer: `types.ts` (row types mirroring `sql/views.sql`), `data.ts` (`getFarmData()` picks the source), `supabase-source.ts`, `fixtures/` (a generated world plus the same views in TypeScript), `commands.ts` (zod payloads, server side) and `command-meta.ts` (labels, no zod, safe for client bundles), `actions.ts` (server actions that validate and queue commands).
- `src/components/farm/` shared domain components (status badges, usage meters, command tracking, states). `src/components/ui/` is the shadcn kit.
- `sql/views.sql` the five `v_*` views; the Farm repo wraps it into Alembic migration 0003.
- `e2e/` Playwright specs.

## Rules that keep it correct

- Aggregate in SQL (the `v_*` views), never in the client. Fixtures derive their rows with the same rules (`src/lib/farm/fixtures/views.ts`); change both together.
- Every mutation is a command: validate with the zod schema in `commands.ts` on the server, enqueue through `FarmData.enqueueCommand`, and show queued, running, done or rejected (`useCommands`). Never apply a change in the browser and call it done.
- No secret ever enters the Console: an account's `auth_ref` is an env-var name or `cli:<id>`; `looksLikeSecret` refuses anything else.
- Fixtures are for local development and the e2e run only. `resolveDataSource()` refuses `FARM_DATA_SOURCE=fixtures` when `NODE_ENV=production` unless `FARM_E2E=1`, which only `playwright.config.ts` sets.
- Pages are Server Components that call `attempt(...)` and render `ErrorState` on failure; interactive parts are small Client Components. Keep first-load JS under the budget: lazy-load heavy widgets (charts, dialogs, menus) after hydration.
- Every state is designed: loading skeleton, empty, error, needs login, exhausted. Numbers right-aligned with units, relative times with the absolute time on hover, WCAG AA contrast (the e2e suite runs axe in both themes).

## Code conventions

- TypeScript strict mode. Use precise types; no `any`.
- Use the `@/` import aliases. Follow the Biome configuration: double quotes, semicolons, two-space indentation, sorted imports and classes, 120-character lines. File names are kebab-case.
- Use semantic theme tokens (and the status tones in `src/lib/farm/state.ts`); do not add arbitrary color values.
- Avoid unnecessary dependencies.
