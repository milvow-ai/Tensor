# Harness Farm on a Linux server: runbook (Milvow team)

The Farm moves from the owner's laptop to a small Linux server, so Tensor and the team (owner + 2 members) can use
it around the clock. The database stays on Supabase. The Farm is reachable only on the team's private Tailscale
network; port 8787 is never open to the internet.

**Status (2026-10-24):** the kit is built and tested in WSL Ubuntu 24.04 on the PC (§10). No server has been bought.
Each step marked **owner** is yours: Claude never handles your keys, logins or money.

## 0. How it fits together

```
 owner laptop ─┐                    harness-farm (Ubuntu 24.04, user farm)
 member A ─────┼── Tailscale ──► tailscale serve :443 ──► farm run 127.0.0.1:8787 ──► Supabase (Mumbai)
 member B ─────┘   (WireGuard)    https://harness-farm.<tailnet>.ts.net   │   ├─► MCP servers (Clay, Sentry, ...)
 Tensor ───────────────────────────────────────────────────────────────────┘   └─► AI CLIs: claude, codex, agy, hermes
```

| Path | What | Owner |
|---|---|---|
| `/opt/harness-farm` | code (git checkout of the deploy branch) + `.venv` | root, read-only for the Farm |
| `/etc/harness-farm/farm.env` | settings, no secrets (template: `deploy/farm.env.example`) | root 0644 |
| `/etc/harness-farm/backup-recipients.txt` | age public key(s) for backups | root |
| `/var/lib/harness-farm` | state = home of user `farm` (`FARM_DATA_DIR`) | farm 0700 |
| `  secrets.env` | your `.env` (`FARM_ENV_FILE`) | farm 0600 |
| `  registry.yaml` | the server's registry (`FARM_REGISTRY_PATH`) | farm |
| `  ai/<connection>` | one CLI login home per AI account | farm |
| `  tokens/<connection>` | MCP OAuth token stores | farm |
| `  workspaces/` | where edit-mode jobs may work (`edit_roots`) | farm |
| `  .hermes/`, `bifrost/`, `logs/` | Hermes profile, Bifrost config, logs | farm |
| `/var/backups/harness-farm` | encrypted state backups, 14 days | root 0700 |

Units (`deploy/systemd/`): `harness-farm.service` (the Farm, user `farm`, `Restart=always`),
`harness-farm-bifrost.service` (Bifrost for Hermes, optional), `harness-farm-watchdog.timer` (every minute: restarts
a hung Farm or Bifrost and alerts), `harness-farm-backup.timer` (daily state backup). `/usr/local/bin/farm` runs the
Farm's CLI as user `farm` with the service's settings, from any admin shell.

**One Farm per database.** Never run the laptop's Farm and the server's Farm against Supabase at the same time:
both would run the scheduler and the command consumer (no leader lock until F2), and each syncs its own paths into
the shared account rows. The cutover (§6) switches from one to the other.

## 1. Choose and buy the server (owner)

**Size:** 4 vCPU, 8 GB RAM, 80–160 GB disk, plus 4 GB swap (the installer adds it). The Farm itself needs little;
the AI CLIs are the load: up to about 12 at once with today's `max_parallel` values, 300–500 MB each, and
edit-mode jobs that build or test code on top. Take 16 GB if many edit-mode jobs will run in parallel.

**Where:** the database is in Supabase `ap-south-1` (Mumbai), and every Farm call makes several database round
trips one after another, so distance to Mumbai adds to every call. Prices checked 2026-10-24 on the providers' own
pages, without VAT/GST:

| Option | Spec | Per month | Distance to Supabase |
|---|---|---|---|
| **DigitalOcean, Bangalore (BLR1), Basic** (recommended) | 4 vCPU, 8 GB, 160 GB, 5 TB traffic | $48 | same country |
| Hetzner Singapore, CPX32 | 4 vCPU, 8 GB, 160 GB, 2 TB | €49.49 | about as far as the laptop is today |
| Hetzner Germany/Finland, CPX32 | 4 vCPU, 8 GB, 160 GB, 20 TB | €35.99 | Europe ↔ Mumbai on every query |
| Hetzner CX43 (cost-optimized) | 8 vCPU, 16 GB, 160 GB | €16.49 | Europe; shown as unavailable on 2026-10-24 |

Image: Ubuntu 24.04 LTS. Add your SSH key when you create it. In the provider's firewall allow only 22/tcp until
SSH over Tailscale works (§3). The provider's own disk backups (about +20 %) are the simplest disaster recovery;
optional.

## 2. Install (owner, as root on the server)

The repository is private: give the server a read-only deploy key.

```bash
apt-get update && apt-get install -y git
ssh-keygen -t ed25519 -N "" -f /root/.ssh/id_ed25519 -C harness-farm-deploy
cat /root/.ssh/id_ed25519.pub   # GitHub: repo Settings > Deploy keys > Add (read-only)
git clone --branch effort2/stage1-saas git@github.com:milvow-ai/Tensor.git /opt/harness-farm
bash /opt/harness-farm/scripts/linux/install-server.sh
```

The installer (`scripts/linux/install-server.sh`, safe to run again) installs the packages (git, Python 3.12,
age, Node.js, `postgresql-client-17` from the PostgreSQL project's apt repository, because `pg_dump` must be at least
the server's version and Supabase runs PostgreSQL 17), uv 0.12.17 (checked against its published sha256), the user
`farm`, the venv, `/etc/harness-farm/farm.env`, the units (enabled, not started), `/usr/local/bin/farm`, 4 GB swap,
Tailscale, and the firewall (deny incoming; allow SSH and the `tailscale0` interface). It starts nothing.

## 3. Private network: Tailscale (owner)

Tailscale's free Personal plan covers up to 6 users (checked 2026-10-24): owner + 2 members cost nothing.

```bash
tailscale up --ssh --hostname harness-farm     # prints a login URL: sign in with the owner's account
```

1. Admin console > DNS: turn on MagicDNS and HTTPS certificates.
2. `tailscale serve --bg 8787`: the tailnet (only) gets `https://harness-farm.<tailnet>.ts.net` with a valid
   certificate, proxied to the Farm on 127.0.0.1:8787. Never `tailscale funnel`: that would publish it.
3. Put the URL in `/etc/harness-farm/farm.env`: `FARM_PUBLIC_URL=https://harness-farm.<tailnet>.ts.net/mcp`.
4. Members: Admin console > Machines > harness-farm > **Share**, to each member's e-mail. They install Tailscale,
   accept, and see this one machine and nothing else of your tailnet.
5. Once `ssh root@harness-farm` works from the laptop over Tailscale: `ufw delete allow OpenSSH` and remove 22/tcp
   from the provider's firewall. The server then has no port open to the internet.

Check: on the server `ss -ltnp | grep 8787` shows `127.0.0.1:8787` only.

The alternative, HTTPS through Caddy plus Cloudflare, puts a public endpoint in front of the Farm and needs a
domain and Cloudflare Access; only worth it if someone cannot run Tailscale.

## 4. Secrets, registry and configuration (owner)

From the laptop (PowerShell; `scp` is built into Windows):

```powershell
scp "D:\Harness Farm\Tensor\.env" root@harness-farm:/root/farm.env
cd "D:\Harness Farm\Tensor"; uv run farm registry for-server config/registry.owner.yaml config/registry.server.yaml
scp config\registry.server.yaml root@harness-farm:/root/registry.yaml
```

On the server:

```bash
install -m 0600 -o farm -g farm /root/farm.env /var/lib/harness-farm/secrets.env && shred -u /root/farm.env
install -m 0600 -o farm -g farm /root/registry.yaml /var/lib/harness-farm/registry.yaml && rm /root/registry.yaml
```

`farm registry for-server` rewrites the PC's registry: every AI account gets its own login home
`/var/lib/harness-farm/ai/<id>` (the WSL users of agy-02..07 and codex-01 become ordinary accounts), `edit_roots`
become `/var/lib/harness-farm/workspaces`, and figma-bridge is disabled with its account paused (it needs the
Figma desktop app; §7). Statuses are kept. `config/registry.server.yaml` is git-ignored like the owner registry.

- **Hermes** (only if you use hermes-01): install Hermes for user `farm`, copy the `farm-agent` profile from
  `%LOCALAPPDATA%\hermes\profiles\farm-agent` to `/var/lib/harness-farm/.hermes/profiles/farm-agent`.
- **Bifrost** (Hermes goes through it): copy `D:\dev-cache\bifrost\config.json` and `config.db*` to
  `/var/lib/harness-farm/bifrost/` (owner farm), then `systemctl enable --now harness-farm-bifrost`. It reads
  `OPENROUTER_API_KEY` from `secrets.env` and runs the pinned Bifrost 1.6.3.
- **Backups key:** `age-keygen -o farm-backup.key` (on the server or any machine with age). Keep the whole file in
  Bitwarden, put only its `# public key: age1...` value into `/etc/harness-farm/backup-recipients.txt`, and delete
  the file from the server (`shred -u farm-backup.key`). The server can then write backups but never read them.
- **Alerts:** the watchdog and `farm alert send` use the database and Telegram (`TELEGRAM_BOT_TOKEN`,
  `TELEGRAM_CHAT_ID` in `secrets.env`, as on the PC).

## 5. Before the cutover: everything on a local database (owner)

The laptop's Farm keeps serving while you prepare the server against its own embedded Postgres (`--local`);
nothing touches Supabase yet.

```bash
farm db migrate --local
farm registry sync --local /var/lib/harness-farm/registry.yaml
```

Install the AI CLIs for user `farm` (they live in `~farm/.local/bin`, which is on the service's `PATH`):

```bash
sudo -iu farm bash -c 'curl -fsSL https://claude.ai/install.sh | bash'           # Claude Code
sudo -iu farm npm install -g --prefix /var/lib/harness-farm/.local @openai/codex   # Codex
# agy (Antigravity CLI) and Hermes: install as on the PC, for user farm, so `agy` / `hermes` are on PATH
```

Log in every account you want on the server, then test it. Each runs in that account's own home, as the Farm will:

```bash
farm ai login --local claude-02    # Claude Code starts: type /login, open the URL on your laptop, paste the code
farm ai login --local codex-01     # a device code: open the URL on any machine and enter the code
farm ai login --local agy-02       # Antigravity: open the printed URL
farm ai test  --local claude-02    # OK: claude-02 answered: PONG
```

Accounts you do not log in: set `status: needs_login` (or `paused`) in `/var/lib/harness-farm/registry.yaml`,
then sync it again.

**MCP accounts.** mem0, Stitch and Langfuse use keys from `secrets.env`: nothing to log in. Clay (2 accounts) and
Sentry use OAuth. The simplest way is to move their token stores from the PC, so no new login is needed (copy them
again right before the cutover; the PC's Farm must not use them after it):

```powershell
scp -r D:\farm-data\tokens\clay-karmabay D:\farm-data\tokens\clay-milvow D:\farm-data\tokens\sentry-milvowxhfk root@harness-farm:/var/lib/harness-farm/tokens/
```
```bash
chown -R farm:farm /var/lib/harness-farm/tokens && chmod -R go-rwx /var/lib/harness-farm/tokens
```

Or log in again from the server: on the laptop `ssh -L 8765:localhost:8765 root@harness-farm`, then in that shell
`farm mcp login --local clay-milvow` and open the printed URL in the laptop's browser (the redirect comes back
through the tunnel; the port is `FARM_OAUTH_CALLBACK_PORT`).

**Smoke test over the tailnet** (still local database):

```bash
sudo -iu farm bash -c 'set -a; . /etc/harness-farm/farm.env; set +a; cd /opt/harness-farm; .venv/bin/farm run --local'
farm token create smoke --local          # in a second shell
```

From the laptop: `curl https://harness-farm.<tailnet>.ts.net/health`, then attach a Claude Code session with that
token (§7) and call `get_capacity` and `ask_ai`. Stop with Ctrl-C and `farm token revoke smoke --local`.

## 6. Cutover (owner, about 15 minutes of downtime)

1. **Laptop:** stop the PC's Farm: end the `HarnessFarm` logon task and watchdog if they run, then kill the whole
   `farm run` tree (`taskkill /T /F /PID <pid of uv run farm run>`) and check nothing listens on 8787.
2. **Server:** copy the OAuth token stores once more if they changed (§5).
3. **Server:**
   ```bash
   farm db backup                                    # pg_dump of Supabase into /var/lib/harness-farm/backups
   farm db migrate                                   # a no-op when the PC was already current
   farm registry sync /var/lib/harness-farm/registry.yaml
   systemctl start harness-farm && sleep 5 && curl -s localhost:8787/health
   farm token create owner-laptop
   ```
4. **Laptop:** set the new token as your Windows user variable `FARM_TOKEN`, then
   `claude mcp remove harness-farm -s user` and the `claude mcp add` line from `farm connect claude-code` (§7).
5. **Check:** in Claude Code on the laptop, `ask_ai` answers, a Clay pass-through call works
   (`call_tool("clay__get-current-workspace", {})`), `get_capacity` shows the pools. On the server:
   `farm status`, `journalctl -u harness-farm -n 50`.

**Rollback:** `systemctl stop harness-farm`; on the laptop `uv run farm registry sync config/registry.owner.yaml`
and start the PC's Farm as before (`D:\farm-data\bin\start-farm.cmd`); point Claude Code back to
`http://127.0.0.1:8787/mcp` with the old token.

## 7. Team access (owner)

For each member (Alice here):

1. **Network:** share the machine with them in Tailscale (§3); they install Tailscale and accept.
2. **Token:** `farm token create alice`; send it privately (Bitwarden Send), never in a group chat. They store it
   as the environment variable `FARM_TOKEN`.
3. **Claude Code:** `farm connect claude-code` prints their command (it uses `FARM_PUBLIC_URL`):
   ```bash
   claude mcp add --transport http harness-farm https://harness-farm.<tailnet>.ts.net/mcp --header "Authorization: Bearer ${FARM_TOKEN}" -s user
   ```
   and the skill: copy `docs/skills/harness-farm/SKILL.md` to `~/.claude/skills/harness-farm/SKILL.md`.
4. **Dashboard sign-in:** `farm member add alice@example.com` creates their Supabase sign-in (confirmed, so no
   e-mail goes out) and their membership of Milvow, which the F1B row level security requires.
   `farm member list` shows everyone.
   **Not enough yet:** the Console still admits only `farm_settings.owner_email` (`checkOwner` in
   `console/src/lib/auth.ts`) and runs only on the owner's PC. Members need that check to accept members and the
   Console hosted where they can reach it (on the server behind `tailscale serve --https=8443`, or Vercel). Open.
5. **Leaving:** `farm member remove alice@example.com`, `farm token revoke alice`, and remove the share.

Every token can call every Farm tool for Milvow (per-person scopes and the per-tenant tool list come later, D51).

**Figma:** figma-bridge talks to the Figma desktop app, so it stays on each person's own machine: the owner keeps the
local figma-bridge MCP in their own Claude Code. Figma's official remote MCP (`mcp.figma.com`) only admits clients on
Figma's list; Claude Code is one, the Farm is not, so each person adds it to their own Claude Code.

## 8. Operations

| Task | Command |
|---|---|
| Logs | `journalctl -u harness-farm -f`; `/var/lib/harness-farm/logs/watchdog.log`, `logs/mcp/` |
| Health | `curl -s localhost:8787/health`; `farm status`; `sudo -iu farm bash -c 'set -a; . /etc/harness-farm/farm.env; set +a; /opt/harness-farm/.venv/bin/python -m farm.control.doctor'` |
| Restart | `systemctl restart harness-farm` (lets in-flight calls finish for up to 30 s) |
| Test alert | `farm alert send --kind farm_down --severity info -m "test from the server"` |
| State backup now | `systemctl start harness-farm-backup` (daily at 03:30 anyway) |
| Update | `/opt/harness-farm/scripts/linux/update.sh` (as root) |

**Watchdog.** systemd restarts a Farm that exits. Every minute the watchdog checks `/health` (and the command
consumer's heartbeat); after 180 s without an answer it alerts and restarts the unit. It checks Bifrost only when
its unit is enabled. If the whole server or Supabase is down, nothing can alert: an outside dead-man's switch
(healthchecks.io or similar, a new account) would close that gap; your decision.

**Backups.** The database is on Supabase (its own backups; `farm db backup` runs before every migration). The
server's own state is backed up daily by `scripts/linux/backup-state.sh`: CLI logins, OAuth token stores,
`secrets.env`, the registry, Hermes and Bifrost config, AI job results. Binaries, caches, logs and workspaces are
left out. Archives are age-encrypted to your public key, carry a checksum manifest and are kept 14 days in
`/var/backups/harness-farm`. Keep a copy off the server, e.g. weekly from the laptop over Tailscale:
`scp root@harness-farm:/var/backups/harness-farm/farm-state-*.age D:\farm-data\server-backups\`.

**Restore drill (monthly):**

```bash
/opt/harness-farm/scripts/linux/restore-state.sh --drill --identity /dev/stdin
# paste the private key from Bitwarden, then Ctrl-D   ->  "restore drill passed: ... (N files verified)"
```

**Real restore** (new server): install (§2), copy an archive to `/var/backups/harness-farm/`,
`systemctl stop harness-farm`, `restore-state.sh <archive> --identity /dev/stdin --into-live`, start. The previous
state directory is kept beside it. An old backup may hold logins that have since been refreshed elsewhere: re-run
`farm ai test` and log in again where it fails.

**Update procedure** (`update.sh`): state backup → `git` fast-forward of the deploy branch → `uv sync` →
`farm db backup` → `farm db migrate` → restart → `/health` within 90 s. If the new code does not come up, the old
commit is restored and restarted (migrations are expand-only, so the old code runs on the new schema) and an alert
goes out. Claude Code updates itself in `~farm/.local`; update Codex with
`sudo -iu farm npm update -g --prefix /var/lib/harness-farm/.local @openai/codex`.

**Security notes.** The AI CLIs run as user `farm`, so a job can read what `farm` can read (`secrets.env`, other
accounts' logins), exactly as on the PC today; per-job sandboxes are F3. The code checkout is read-only for them, and
edit-mode jobs may only start under `/var/lib/harness-farm/workspaces`. Nothing listens outside loopback except
Tailscale.

## 9. Done when

- [ ] Owner and both members call the Farm from their own machines over Tailscale (`get_capacity` from each).
- [ ] `ask_ai` and a Clay pass-through call succeed with the server serving (§6.5).
- [ ] A restore drill of the server state passes (§8).

## 10. What was tested, and where

- **WSL Ubuntu 24.04 on the PC (2026-10-24), as an unprivileged user:** the full suite (2,446 passed; the
  6 Linux-only failures it showed are fixed, each with a regression test), plus `tests/test_linux_server.py`: the
  watchdog restarts a Farm that stopped answering and alerts once, leaves a healthy one alone; a state backup
  restores byte for byte, leaves caches out, and a damaged archive is refused.
- **Windows (this PC):** the same tests where they apply (the Linux script tests skip).
- **Not tested yet:** a real server; Tailscale Serve end to end; Claude Code, Codex and agy logins as user `farm`
  under the hardened unit; Bifrost on Linux; Codex's sandbox under Ubuntu 24.04's AppArmor rules for unprivileged
  user namespaces (check an edit-mode Codex job early).
