# Brief L3 — Dashboard sign-in: add and sign in AI accounts and API keys from the Console (no terminal)

Rules for every step: touch only the files listed under "Owns". Never read any .env file. Never run git add, commit,
push, reset, checkout, stash or worktree commands. Never call a real provider. Run every command in the foreground and
wait for it. C: is nearly full: in PowerShell put temp files on D: first (`$env:TEMP='D:\dev-cache\tmp'; $env:TMP=$env:TEMP`).

## Goal

Today the Console adds an AI account and then tells the owner to copy `farm ai login <id>` / `farm set-secret <NAME>` into
PowerShell. Replace that with sign-in in the dashboard, for single accounts and whole pools, for any Harness Farm user:
the owner clicks **Sign in** (or **Add accounts**), a card shows exactly what to do (a device code, a link plus a box to
paste a code), updates live while the Farm signs the account in, and ends **Ready** (with evidence) or with a clear reason.
API keys are pasted in the Console, encrypted in the browser for the Farm, and never stored in plain text.
The Farm side is already built and tested (branch `l1a`): `farm/connect/*`, migration 0020. Do not change the Farm.

## Read first

`docs/design/CONNECT.md` (§3–§6), `console/AGENTS.md`, `docs/design/DASHBOARD-COMPONENTS.md`, the pools page
(`console/src/app/(main)/pools/**`), the command helpers (`console/src/lib/farm/commands.ts`, `actions.ts`,
`components/farm/use-command.ts`, `command-meta.ts`, `command-schemas.json`), the data layer (`lib/farm/data.ts`,
`supabase-source.ts`, `fixtures/source.ts`, `fixtures/world.ts`, `mode.ts`, `types.ts`), the kit
(`console/src/components/kit/*`), `farm/connect/commands.py`, `farm/connect/sealed.py`,
`farm/db/migrations/versions/0020_login_sessions.py`, `tests/connect/fixtures/sealed_vectors.json`.

## Owns

- `console/src/lib/farm/seal.ts` (new), `console/src/lib/farm/login.ts` (new: types + data access for sign-in),
  `console/src/components/farm/use-login-session.ts` (new)
- `console/src/app/(main)/pools/_components/*` (change and add files: sign-in drawer, cards, add-accounts dialog,
  API-key form), `console/src/app/(main)/pools/page.tsx`
- `console/src/app/(main)/integrations/_components/*` only to replace `farm set-secret` / `farm ai login` instructions
  with the new dashboard flows (MCP OAuth `farm mcp login` stays for now: that is L2)
- `console/src/lib/farm/commands.ts`, `command-meta.ts`, `command-schemas.json`, `types.ts`, `data.ts`,
  `supabase-source.ts`, `fixtures/source.ts`, `fixtures/world.ts` (additions only)
- tests: `console/src/lib/farm/__tests__/seal.test.ts`, `console/src/lib/farm/__tests__/login.test.ts`,
  `console/e2e/signin.spec.ts` (new)
- Must not touch `farm/`, `tests/` (Python), `docs/`, other Console pages.

## Contracts (exact; the Farm already implements them)

1. **Commands** go into `farm_commands` through the existing command helper (same as pause/resume), results read the
   same way:
   - `add_accounts` `{provider_id, count (1–10), cli?: "claude"|"codex"|"gemini"|"agy"|"hermes", plan?, emails?: string[],
     max_parallel?: 1–4}` → result `{connection_ids: string[], session_ids: string[]}`
   - `start_login` `{connection_id, method?}` → `{session_id, connection_id}`
   - `submit_login_input` `{session_id, sealed: Envelope}` → `{session_id, state}`
   - `cancel_login` `{session_id}` → `{session_id, state}`
   - `save_secret` `{name: ENV_NAME, sealed: Envelope, connection_id?}` → `{saved: name}`
   Regenerate `command-schemas.json` with the generator the repo already uses (find it: grep for `command-schemas.json`
   in `scripts/`, `farm/` and `console/`), so it includes these five kinds; add them to `command-meta.ts`.
2. **Session state**: view `public.v_login_sessions` (RLS-scoped; no tenant_id column): `id, connection_id, provider_id,
   method (device_code|paste_code|loopback|oauth_redirect|api_key|token), state (queued|starting|waiting_for_user|
   verifying|done|failed|expired|cancelled|duplicate|needs_verification), auth_url, user_code, verification_uri,
   needs_input, input_hint, expires_at, identity {account_hash, email_masked, plan, org}, evidence {test_run_id, models},
   error_kind, error_excerpt, created_by, created_at, updated_at, finished_at`. Poll every 1.5 s while a card is open and
   stop when `finished_at` is set (a Realtime subscription instead is fine if the data layer already has one).
3. **Public key**: `farm_settings.connect_public_key` (JWK `{kty:"EC", crv:"P-256", x, y}`). When it is null, sign-in
   buttons are disabled with "Start the Farm to sign accounts in" (the Farm publishes the key when it starts).
4. **Tenant id** for sealing: `NEXT_PUBLIC_FARM_TENANT_ID`, default `00000000-0000-0000-0000-000000000001` (Milvow, tenant #1)
   — the Console is single-tenant until the A3 workspaces; put this in one function so A3 can replace it.
5. **Sealing** (`seal.ts`, WebCrypto only, no new dependency), byte-for-byte compatible with `farm/connect/sealed.py`:
   ephemeral P-256 key per value; ECDH with the Farm's public key → 32-byte shared secret; HKDF-SHA256 with an **empty
   salt** and info = UTF-8 `harness-farm/connect/v1`, 32 bytes; AES-256-GCM with a random 12-byte IV and
   AAD = UTF-8 of `` `${tenantId}|${subject}|${purpose}` ``; envelope
   `{v: 1, alg: "ECDH-ES+HKDF-SHA256+A256GCM", epk: {kty:"EC", crv:"P-256", x, y}, iv: b64url, ct: b64url}`
   (base64url without padding; `ct` = ciphertext with the GCM tag appended). Purposes and subjects:
   pasted login code → subject = session id, purpose `login_input`; API key → subject = the env name, purpose `secret`.
   Never log, store, or put in React state longer than needed a plaintext code or key; clear inputs after sending.

## What to build

1. **Pools page (AI)**: per pool card — "N of M ready" from the existing data, **Sign in again (k)** when k accounts are
   `needs_login`, **+ Add accounts**; per account row — **Sign in** for `needs_login` (and in the row menu for any AI
   account: "Sign in again"). Remove the `farm ai login` copy-command from `add-ai-account-form.tsx` and
   `account-model.ts`: adding an AI account now leads straight into its sign-in card.
2. **Add accounts dialog**: provider (existing AI pools), count stepper 1–10, plan (Free/Pro/Max/Team), optional expected
   emails per slot, jobs at a time → `add_accounts` → opens a stack of sign-in cards, one per new session.
3. **Sign-in card** (one component, reused in a side drawer and in the stack) driven by the session row:
   - `queued`/`starting`: "Getting the sign-in ready…"
   - `waiting_for_user` + `user_code`: big code with Copy, **Open sign-in page** (link `verification_uri` or `auth_url`,
     `target=_blank rel=noopener`), "Use a private window if this browser is signed in to another account", expiry
     countdown from `expires_at`
   - `waiting_for_user` + `needs_input`: **Sign in** link (`auth_url`) + labelled input "Paste the code the page shows you"
     (`input_hint` as help) + **Connect** → seal → `submit_login_input`; then "Checking…"
   - `verifying`: checklist "Signed in · Checking who it is · Test reply"
   - `done`: **Ready** with `identity.email_masked`, `identity.plan`, `evidence.models`, link to the test run if the Console
     has a run page
   - `duplicate`: warning with `error_excerpt` + **Try again in a private window** (new `start_login`) + **Remove this slot**
     (only for accounts created in this dialog: existing `remove_connection` command)
   - `needs_verification`: "The provider asks you to verify this account" + **Verify** link (`auth_url`) + **Check again**
     (new `start_login`)
   - `failed`/`expired`/`cancelled`: the reason (`error_excerpt`) + **Start again**
   - every non-final state has **Cancel** → `cancel_login`
4. **API keys**: on the pools page for providers whose accounts use `env:` auth (and in Integrations where it said
   `farm set-secret`): "Paste API key" (one) and "Paste keys, one per line" (bulk): each key → seal → `save_secret` with a
   generated env name (`<PROVIDER>_KEY_<NN>`, upper-case, next free number) and the target `connection_id` when there is one;
   show per-key result rows (saved / rejected with reason); show the lock note "Encrypted in this browser for your Farm".
5. Follow the approved look: the kit components, light theme first, cards with hairline borders, one blue accent, status
   chips green/amber/red with text (never colour alone), real `<button>`/`<a>`/`<label>`, keyboard reachable.
6. **Fixture mode**: extend the fixtures (world/source) with a public key (use the TEST key from
   `tests/connect/fixtures/sealed_vectors.json`), and scripted sessions for every state above, so the pages and e2e work
   without Supabase.

## Tests (run only these; never the full e2e suite)

- `seal.test.ts`: (a) import the TEST private key (PKCS8 PEM in `sealed_vectors.json`) with WebCrypto and **open every
  Python-made vector** — proves the derivation matches the Farm; (b) seal → open round trip; (c) wrong AAD and tampered
  `ct` fail; (d) envelope shape matches the contract exactly.
- `login.test.ts`: state → card view model mapping for every state; env-name generation (`REOON_KEY_01` after
  `REOON_KEY_00`… next free).
- `console/e2e/signin.spec.ts` (fixture mode): device-code card shows the code and link; paste-code flow sends a
  `submit_login_input` whose payload contains an envelope and **not** the plaintext; duplicate card shows the reason;
  add-accounts dialog sends `add_accounts`; bulk API keys send one `save_secret` per key with no plaintext in any payload.
- Commands to run (from `console/`): `pnpm exec biome lint`, `pnpm exec tsc --noEmit`,
  `pnpm exec vitest run src/lib/farm/__tests__/seal.test.ts src/lib/farm/__tests__/login.test.ts`,
  `pnpm exec playwright test e2e/signin.spec.ts`. Fix until all pass. Do not run `next build` or the whole e2e suite
  (the PC is short of memory); the lead runs them.

## Reply (≤ 15 lines)

Files changed · tests added and their result lines · anything you could not do and why.
