# Connect & Pools — sign in from the dashboard, never from a terminal (design v1, 2026-10-24)

Status: **proposal, waiting for the owner's yes.** Mockups: Design canvas "Harness Farm — Connect & Pools"
(https://claude.ai/artifact/5AGEghjoE5cxKYgXpyK3RQ, private to the owner). Visual language: `DASHBOARD-COMPONENTS.md`.
Product meaning: `docs/PRODUCT.md` §5.4 (Farm) and §6 (first ten minutes). Binding architecture: `docs/ARCHITECTURE.md`.

---

## 1. The problem (verified in code and on the live Farm, 2026-10-24)

- The Console can add an account, then hands the owner a command to copy into PowerShell:
  `farm ai login <id>` (`console/src/app/(main)/pools/_components/add-ai-account-form.tsx:210`, `pools/_lib/account-model.ts:53`),
  `farm mcp login <id>` and `farm set-secret <NAME>` (`integrations/_components/integrations-view.tsx:289, 377, 437`;
  `pools/_components/add-account-form.tsx:193`).
- `farm ai login` (`farm/control/cli.py:766`) starts each CLI interactively: Claude Code with `/login` typed by hand, `codex login`
  (`--device-auth` when headless), `agy` with a per-account `HOME`/`USERPROFILE`, `hermes -p <profile>`.
- `farm mcp login` opens a browser with a loopback callback on the machine running the command; on a server it needs
  `ssh -L` and `FARM_OAUTH_CALLBACK_PORT` (`farm/executors/mcp/connect.py:587–603`).
- Live state: Codex 0 of 3 usable (`needs_login`), claude-01 "OAuth session expired and could not be refreshed", agy-05..07
  "not eligible … verify your account", agy-01 lists a model agy rejects (hand-typed model list).
- On a server every login gets harder (SSH, port forwarding). For SaaS tenants a terminal is not an option at all.

## 2. Goal and rules

1. **Zero terminal** for connecting, re-connecting, adding keys and removing accounts, for one account or a whole pool.
2. **One flow everywhere:** the PC Farm today, the server, and SaaS tenants (only the place the CLI runs changes).
3. **A failed (re)login never breaks a working account:** the old login stays until the new one passes every check.
4. **Green only with evidence** (PRODUCT §2.2): who the account is, which models it has, a test run.
5. **Secrets:** typed in the browser, encrypted there for the Farm; the Console, Supabase and every agent see ciphertext or a
   name only. CLAUDE.md rule 4 still holds: values live only on the Farm machine (`.env` now, the vault after F2).
6. The terminal commands stay as an owner fallback, as thin wrappers over the same code.

## 3. How each kind of account connects

| Method | Used by (installed version) | How it completes | What the person does |
|---|---|---|---|
| **Device code** | Codex 0.160 `codex login --device-auth` | The CLI polls until the person approves | Open link, enter the code (phone works: QR) |
| **Paste-back code** | Claude Code 2.1.299 `claude auth login [--email <hint>]`; Gemini CLI 0.63 with `NO_BROWSER=true` | The person pastes the code the provider page shows into the card; the Farm writes it to the CLI | Sign in, paste once |
| **Long-lived token** (candidate) | `claude setup-token` → `CLAUDE_CODE_OAUTH_TOKEN`; `codex login --with-access-token` / `--with-api-key` (stdin) | Same as paste-back; the Farm captures the token and stores it as a secret | Same; re-login about yearly |
| **Loopback redirect** | agy 1.4.7 (no login subcommand: sign-in happens in its interactive screen); MCP OAuth (FastMCP callback server) | Local Farm: the Farm opens an **isolated private browser window** on the same PC and the redirect reaches the CLI. Remote Farm: MCP uses the dashboard callback (below); CLIs use a one-paste relay of the address-bar URL, or a runner on the PC (ARCHITECTURE §6, runner mode) | Sign in (remote CLI: + one paste) |
| **Dashboard callback** | MCP OAuth on a server or SaaS | Provider redirects to `<console>/connect/callback`; the Console seals the code and hands it to the Farm, which holds the PKCE verifier | Click, sign in, approve |
| **API key** | Reoon, Apollo, Hunter, ZeroBounce, OpenRouter (Hermes), model APIs | Sealed in the browser → stored by the Farm → provider test call | Paste one or many (one per line) |
| **Provider verification** (not a login) | agy "Eligibility check failed … verify your account" | The card shows the provider's URL and **Check again** | Finish the provider's steps |

L0 decides per CLI which method is the default (§8). Recommendation to test first: `setup-token` for Claude on the server and for
SaaS, because a token needs no home folder to sync and has no refresh-token race between parallel jobs.

## 4. Pools, not single accounts

- **Add 1–10 accounts at once:** provider, count, plan, optional expected emails, jobs at a time → N connections
  (`claude-05..08`, status `needs_login`, own homes) and one sign-in card each.
- **Sign in again:** per account, or "Sign in again (N)" for every `needs_login` account of a pool; also from the Overview
  "Needs you" banner, alerts and Telegram (deep link straight to the card; works on a phone).
- **Identity check and duplicate guard:** after sign-in the Farm reads who the account is; the same person or workspace as
  another connection of that provider is refused with its name ("same account as claude-02"), nothing saved.
- **Expected-email hint:** pre-filled where the CLI allows (`claude auth login --email`), compared after sign-in.
- **Models discovered, not typed:** the model list comes from the account after sign-in (fixes the agy-01 stale model).
- **Test with evidence:** a tiny test job (the `farm ai test` path); its run id is the account's evidence.
- **Reminders:** where an expiry is known (OAuth `expires_at`, token lifetime) alerts go out 7 days and 1 day before.
- **Bulk keys:** paste many API keys; each is tested; duplicates (same account or same key) skipped; each good key becomes a
  connection.
- **Remove:** sign out where the CLI supports it (`claude auth logout`, `codex logout`), then delete the home or secret, after a
  confirm.

## 5. Architecture

### 5.1 One login, end to end

```
Console card ──(farm_commands: start_login)──► CommandConsumer ──► LoginService
                                                                   │ driver runs the CLI in a STAGING home
                                                                   ▼
Console polls v_login_sessions ◄── login_sessions row: waiting_for_user (auth_url, user_code, needs_input)
person acts ──► device: CLI completes │ paste: sealed submit_login_input → stdin │ OAuth: callback → sealed code
                                                                   ▼
                     verify: identity → duplicate check → models → test job (evidence)
                                                                   ▼
          success: staging home swapped into place (or token stored) → connection active → audit, alert closed
          failure: staging discarded, old login untouched → card shows the reason and "Start again"
```

### 5.2 Data (migration 0020; tenant-scoped with RLS like 0019)

- `login_sessions`: `id`, `tenant_id`, `connection_id`, `provider_id`, `method`, `state`
  (`queued → starting → waiting_for_user → verifying → done`, or `failed | expired | cancelled | duplicate | needs_verification`),
  `auth_url`, `user_code`, `verification_uri`, `needs_input`, `input_hint`, `expires_at`, `identity` (account id hash, masked
  email, plan, org), `evidence` (test run id, models), `error_kind`, `error_excerpt` (redacted), `created_by`, timestamps.
  **No secret is ever written to this table.**
- `connections.meta.identity` (stable provider account id, hashed with a tenant salt) and `connections.meta.login`
  (method, last login, known expiry).
- `v_login_sessions` (`security_invoker`) for the Console.

### 5.3 New command kinds (`farm/control/commands.py`; owner/admin only, never exposed to agents)

| Kind | Payload | Effect |
|---|---|---|
| `add_accounts` | provider, count ≤ 10, plan, emails?, max_parallel | N connections + N queued sessions |
| `start_login` | connection_id, method? | one session (one per connection at a time) |
| `submit_login_input` | session_id, sealed | pasted code, pasted redirect URL or OAuth code |
| `cancel_login` | session_id | stops the CLI, discards staging |
| `save_secret` | connection or provider, name, sealed | before F2: `farm.secrets.set_secret` → `.env`, `auth_ref env:NAME`; after F2: `vault:NAME` |
| `add_api_keys` | provider, sealed keys[] | tests each, one connection per good key |
| `remove_account` | connection_id, sign_out | sign out, delete home or secret, audit |

`test_connection` (existing) is reused for the verify step.

### 5.4 Sealed input

- The Farm creates a P-256 key pair on first start (`cryptography`; declare it in `pyproject.toml`, it is already in `uv.lock`).
  Private key in `<data_dir>/keys/connect.pem` (owner-only ACL); public key (JWK) in `farm_settings.connect_public_key`.
- Browser: WebCrypto ECDH with an ephemeral key → HKDF-SHA256 → AES-256-GCM; payload `{v, epk, iv, ct}`;
  AAD = `tenant_id|session_id|kind`, so a ciphertext cannot be replayed into another session. No new JS dependency.
- The consumer decrypts, uses the value once, then blanks it in `farm_commands` (`"sealed": "consumed"`).
- One shared test-vector file: sealed in the browser test, opened in the Python test.

### 5.5 Login service and drivers (`farm/connect/`)

- `service.py`: sessions as asyncio tasks inside the consumer role; at most 3 at once; one per connection (advisory lock now,
  `leases` after F2); 10-minute wait for the person, 2-minute verify; every transition written; all CLI output redacted
  (`farm.secrets.redact`, captured tokens registered as secrets) and kept in memory only.
- `drivers/{claude,codex,agy,gemini_cli,hermes,api_key,mcp_oauth}.py`, one interface:
  `start(home, hint)`, `events(output) → Url | UserCode | NeedsInput | NeedsVerification | Success | Failure`
  (signature tables + golden transcripts, the same pattern as `farm/executors/cli_agent/classify.py`), `submit(text)`,
  `identity(home)`, `models(home)`.
- Process runner: plain pipes where the CLI works without a terminal; a PTY (`pywinpty` on Windows, `ptyprocess` on Linux)
  only for CLIs that need one (L0 decides; add the dependency only then).
- **Browser control on a local Farm:** the CLI must never open the person's main browser (it would sign in the wrong account).
  The driver overrides the CLI's opener (`BROWSER` or equivalent, per L0) and the Farm opens the URL in an isolated profile
  (`--user-data-dir=<temp on D:> --inprivate/--incognito`), closed after success. On a server the dashboard opens it.
- **Staging home:** the login runs in `ai/<id>.login-<session>`; on success it replaces `ai/<id>` atomically (the old one kept
  as `ai/<id>.prev` for a day); running jobs finish on the old home. After F3 the staging home becomes a new encrypted bundle
  version instead.
- `farm ai login` and `farm mcp login` call the same service (one code path).

### 5.6 MCP OAuth through the dashboard (L2)

- `FarmOAuth.redirect_handler` publishes the URL to the session (no print, no browser); its callback handler waits for the
  session's code.
- Redirect URI per deployment: local → the existing loopback (`FARM_OAUTH_CALLBACK_PORT`); server / SaaS →
  `<CONSOLE_URL>/connect/callback`. Dynamic client registration uses the URI in force; a change re-registers that
  connection's client only.
- Console route `/connect/callback`: reads `code` and `state`, seals the code, posts `submit_login_input`, shows progress,
  says "You can close this tab".
- After login: tool sync runs by itself (no `farm mcp sync`); identity for the duplicate check from a per-server probe
  (`clay__get-current-workspace` → workspace; Sentry → organisation; otherwise the token's `sub`).

### 5.7 Identity per provider (confirm in L0)

| Provider | Identity source |
|---|---|
| Claude | `claude auth status --json` (email, organisation, subscription) |
| Codex | `codex login status` + the claims of the id token in `auth.json`, read locally |
| agy | `agy models` proves the login and gives the model list; identity source to find in L0 |
| Gemini CLI | its stored Google account (L0) |
| MCP | per-server probe (§5.6) |
| API keys | the provider's account or balance endpoint, per adapter; duplicate = same account id or same key hash |

### 5.8 Security

- Only owner/admin members can connect, re-login or remove (F1 roles); every step goes to `audit_events`.
- No secret in the database, logs, run events, Sentry or Langfuse: tests plant canary values and grep for them.
- Sessions expire; inputs are single use; AAD binds a ciphertext to its session.
- Agents never get these command kinds or the decrypted values.

## 6. Dashboard (A3A kit; mockups on the canvas)

1. **Farm → AI / Tools / APIs:** one card per pool: "2 of 4 ready", a segment per account, state with reason, "Sign in again (N)",
   "+ Add accounts", "Open pool". Overview gets the "Needs you" banner.
2. **Pool page:** accounts table (signs in with, state, why with evidence, action); the sign-in card opens as a side drawer.
3. **Add to farm:** catalog (AI accounts, MCP tools, APIs, each with how it connects) + pool setup (count, plan, expected
   emails, jobs at a time).
4. **Sign-in cards:** waiting, checking, ready, same account twice, private window on this PC, provider verification,
   expired / failed.
5. **Tools and keys:** OAuth "Connect" with the return page; any MCP server by URL; bulk API keys with a result per key.
6. **Phone:** the alert link opens the card; device code and sign-in work on the phone.

Live updates: poll `v_login_sessions` every 1.5 s while a card is open (Realtime later). Remove every copy-command listed in §1.

## 7. Build plan (estimates, not promises)

| ID | Scope | Owner | Done when | Days |
|---|---|---|---|---|
| **L0** | Login spike on this PC and in WSL: each CLI's login without a person at the keyboard (output, code entry, success signal, where the login lands, identity, opener override); `setup-token` + `CLAUDE_CODE_OAUTH_TOKEN` job; Codex device auth; agy; Gemini CLI; Clay DCR with an https redirect | Sonnet worker, lead reviews | `docs/spikes/L0-logins.md` + recorded transcripts as fixtures; every method proven or given a named fallback | 0.5–1 |
| **L1** | Migration 0020, command kinds, sealed input, LoginService, drivers, staging-home swap, identity + duplicate guard + model discovery + test job, audit, `farm ai login` on the same path | Builder per state file; worker-check on crypto, RLS and secrets | Replay tests with fake CLIs for every state (success, wrong code, expired, duplicate, verification needed, crash, timeout); isolation suite green; canary test finds no secret; Windows + Linux | 1.5–2 |
| **L2** | MCP OAuth via sessions: loopback and dashboard-callback modes, per-mode DCR, auto tool sync, Clay identity probe | Builder | Recorded OAuth test (local fake authorisation server) passes in both modes; a duplicate workspace is refused | 0.5–1 |
| **L3** | Dashboard screens from §6 in the A3A kit, `/connect/callback`, browser sealing, removal of every copy-command | Builder (agy or Farm Claude pool) | Playwright on fixtures for every card state; light + dark screenshots; vitest for sealing; WCAG 2.2 AA checks | 2–3 |
| **L4** | Live run with the owner | Lead + owner | codex-01..03, claude-01 and agy-01 signed in from the dashboard, one Clay workspace and the Reoon key added, `get_capacity` shows them usable, **zero PowerShell** | 0.5 |

Total ≈ 5–7.5 working days.

## 8. Open questions L0 must answer

1. Does `claude auth login` run without a terminal, take the code on stdin, honour `--email`? Can the Farm read the identity of a
   `setup-token` login? Which features does a token-only login lack?
2. Does Codex device auth need a ChatGPT setting switched on first? What exactly does it print?
3. How does agy finish a Google sign-in (loopback port or a code)? Can its sign-in run headless? Does the `HOME` override keep
   accounts apart on Windows and Linux?
4. Gemini CLI `NO_BROWSER` flow and the variable that gives each account its own home.
5. Do Clay and Sentry accept an https dashboard redirect URI during dynamic client registration?

## 9. Place in the plan

Pulled forward from A3 (Farm pages) and F3 (accounts as assets). It depends on nothing unbuilt: it uses today's homes and `.env`.
F2 swaps `.env` for the vault and F3 swaps homes for encrypted bundles behind the same driver interface; the screens do not
change. It also gives the rest of A3 its first real pages in the new look.
