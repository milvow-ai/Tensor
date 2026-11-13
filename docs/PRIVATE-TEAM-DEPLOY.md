# Private team deployment

This is the procedure for the three-person release. The older single-user installer/update scripts do not manage these profiles. Local checks prepare a candidate; server enrollment, authenticated client checks, off-host restore and the 48-hour soak in PRIVATE-TEAM-RELEASE.md still gate cutover. No server has been provisioned by the local checks.

## Boundaries

Provision `owner`, `member-a`, `member-b` as personal instances and `team` as a shared **external MCP/API resource gateway**. Each has a private database and state directory, its own `hf-NAME` Linux user, and a console under a separate `hfui-NAME` user. One code installation is root-owned and read-only to all workers. Never run two backends against one database.

Native CLI accounts and project memory belong in personal instances. The shared gateway refuses native CLI pools and does not expose personal memory tools. An AI CLI running under one Linux identity can read that identity's files; registering everybody's native logins in a shared CLI pool is therefore not supported. Shared MCP calls have member-private runs/results, but the external service itself determines what a shared account can access. Project-memory sharing is not inferred from an account label.

The console currently requires Supabase Auth and PostgREST. Use four distinct Supabase-compatible projects with distinct authenticated TLS PostgreSQL databases; four embedded local databases alone do not supply dashboard authentication. Configure each personal registry's `settings.owner_email` as that person's verified login email. The team registry uses the administrator's email. The owner-only console is the control room for its instance; other people use their own console and membership-bound MCP tokens.

## Prepare the target

Use an Ubuntu 24.04 host with Python 3.12, systemd, Node 20.9 or newer at `/usr/bin/node`, pnpm, uv, age, PostgreSQL client tools matching the database server major version, and rclone if off-host delivery uses it. Install the AI CLI binaries from their vendors during onboarding. Each profile starts with a limit of two active executions. The systemd ceilings are 2 GiB per backend and 768 MiB per console. Measure the representative workload to size the host.

Place a clean checkout at `/opt/harness-farm`, install frozen Python and console dependencies, and keep code/dependencies root-owned. Do not copy a PC `.env`, personal CLI homes, OAuth stores, screenshots, imports or runtime database into the code checkout. The provisioner refuses a repository `.env`. Do not enable the legacy `harness-farm.service` or legacy watchdog on this profile deployment.

```bash
cd /opt/harness-farm
uv sync --frozen --no-dev --python /usr/bin/python3.12
pnpm --dir console install --frozen-lockfile
```

Stage a manifest based on `deploy/team.example.json` and private inputs under `/root/farm-input` (directory 0700, files 0600). Start each registry from `deploy/registry.private.example.yaml` and set its owner's email. Every profile's secrets file needs these variables.

```dotenv
SUPABASE_DB_URL=PASTE_THE_PRIVATE_TLS_DATABASE_URL_HERE
SUPABASE_URL=https://PROJECT.supabase.co
SUPABASE_SERVICE_ROLE_KEY=SERVER_ONLY_KEY
SUPABASE_ANON_KEY=PUBLIC_ANON_OR_PUBLISHABLE_KEY
```

Use a direct database connection or a session-mode pooler. The backend sets role and tenant context for the lifetime of a database session, so transaction-mode pooling is incompatible. Set `sslmode=verify-full`, use correctly URL-encoded credentials and install the database CA when required. The service key must differ from the publishable/anon key. Store provider secrets in the private secrets file and refer to them by variable name in the registry. Convert PC registries with `farm registry for-server --help` and review paths and commands before staging. Keep account homes and allowed edit roots under that profile's state directory. Bind infrastructure to loopback and configure MCP links only from personal profiles to the shared gateway. [Supabase connection and restore guide](https://supabase.com/docs/guides/self-hosting/restore-from-platform)

Each profile needs a distinct HTTPS hostname. Use private DNS and a TLS reverse proxy reachable only by the team. Route `/mcp`, `/ready` and `/reports/usage` to its backend and other paths to its console. Preserve authorization headers and allow streaming on `/mcp`. Distinct hostnames keep browser cookies separate. Configure each Supabase project's Site URL and permitted `/auth/callback` redirects for its exact hostname. Keep raw backend and console ports and the database off the public network.

## Plan, provision and enroll

These commands run on the target as root. Planning is the default; applying creates identities/configuration and reloads unit definitions, but starts/enables nothing.

```bash
python3 /opt/harness-farm/scripts/linux/provision-team.py /root/farm-input/team.json
python3 /opt/harness-farm/scripts/linux/provision-team.py /root/farm-input/team.json --apply
farm-instance owner db migrate
farm-instance owner registry sync /var/lib/harness-farm-instances/owner/registry.yaml
python3 /opt/harness-farm/scripts/linux/build-console.py owner
python3 /opt/harness-farm/scripts/linux/build-console.py owner --apply
```

Repeat migration/sync/build for all four profiles. `farm-instance NAME ...` drops to that profile's Linux identity and uses its private environment; it does not carry the administrator's environment or credentials into the worker. Build one console at a time. Each build receives only its public Supabase URL/key, uses bounded workers and webpack for the shared read-only dependency directory, and switches the console symlink only after a successful build. Old build directories remain available for rollback. The Node service has no backend credentials or access to CLI state.

Before starting, verify database grants: a personal database credential must not read another profile's database. Supabase users must have membership rows linked to their Auth subject. The Farm can ensure its configured owner membership during startup; enroll other shared-gateway members with the existing `farm member` commands. Inspect `farm-instance team member --help` and `token create --help`; mint a distinct token per client/person with `--user EMAIL --scope mcp`. Add `ai:recovery:approve` only to an operator allowed to approve provider changes. Do not distribute unbound wildcard service tokens to people. Membership removal revokes their tokens; shared HTTP MCP rejects unbound tokens.

Configure private TLS, Supabase redirects and backup delivery before explicitly starting units:

```bash
systemctl start harness-farm@owner harness-farm-console@owner
farm-instance owner doctor
```

Repeat per profile, inspect each unit's journal and authenticate `/ready`. `/health` is liveness; `/ready` checks database/schema and background-task liveness. Native systemd notifications detect a blocked HTTP event loop; dependency outages return unavailable and do not justify endless restarts. Restart budgets stop after five starts in an hour. Background progress and external-provider availability require separate observation. Review every doctor warning; exit zero only means there were no FAIL checks.

Sign into each personal console, add/test its native CLI accounts and MCP connections there, and verify real provider-returned identity. An unverified account shows unknown identity. The active model/effort and worker account are separate from the verified Farm-token operator email. `farm_operator` identifies the authenticated Farm member/client; it does not attest which subscription is signed into the caller's IDE.

### Continue a task from another IDE

Connect both IDEs to the same personal Farm, using a member-bound token for each IDE. In the first IDE, call `task_open` with the project name, absolute workspace path, and goal. Save the task ID and operator epoch.

1. Use `task_cli` for a worker job, `task_call` for a Farm API or upstream MCP tool, and `task_local_edit` before editing the IDE workspace. Use a distinct step key for each action and the same key and input if retrying the submission. Choose a model and effort with `ai_options` when assigning a worker. Parallel edits need separate Git worktrees on the worker host.
2. Save progress with `task_checkpoint`, including changed files, completed work, and what remains. A note-free checkpoint renews the three-minute operator lease. Pause explicitly when leaving if the IDE is still responsive. Farm jobs and calls already submitted continue independently of the IDE.
3. In the new IDE, call `task_list` with the same workspace path, then `task_status`. Choose **finish** for local edits or **delegate** for worker assignments. `task_takeover` records the choice and gives the new operator a fresh epoch. Replacing an active lease requires explicit confirmation. For an interrupted local edit, check that the old IDE has stopped writing and inspect the files before acknowledging the workspace check.
4. Read a saved MCP or API response with `task_status(task_id, step_key)`. Read CLI output and any partial result with `ai_result` using the job ID. Responses above the receipt limit retain a preview and run reference. For failed or interrupted steps, record verified outcomes with `task_resolve_step`. If work remains, give the replacement step a fresh key and include the saved checkpoint and output in the instructions. Verify external writes and file changes before superseding an uncertain step. Close the task with `task_complete`.

The personal console's Tasks page refreshes worker activity and task status while visible. `worker_profiles` also shows configured execution limits, active and queued work, model selections, and Farm-metered limits. Provider allowance windows and resets appear where observed. The number of future jobs an allowance can cover is unknown. The Farm can resume saved workspace files and registered steps. An IDE's unsaved buffers and native chat history remain with that IDE.

Register the team gateway as an upstream HTTP MCP in each personal Farm, using the corresponding person's team token held in their private secrets file. Start with a discovery namespace and explicit tool policy. Always sync/test the upstream before use. New synced tools become discoverable on the running gateway; an IDE that caches its direct tool list may need its tool list refreshed. Search/describe/call helpers remain present even before the first server is added. Never link the team gateway back to a personal Farm.

## Administrator reporting

Create separate service tokens with **only** `reports:usage` on each instance. They cannot invoke MCP tools. Store them as environment variables for the owner's console in root-private `/etc/harness-farm/instances/owner/reporting.env`. These limited reporting credentials are the only cross-instance credentials permitted in the admin Node process.

Set `FARM_TEAM_REPORTS_FILE` to a private JSON file readable by `hfui-owner`, containing entries such as:

```json
[{"name":"member-a","endpoint":"https://member-a.farm.example.com/reports/usage","token_env":"TEAM_MEMBER_A_REPORT_TOKEN"}]
```

The endpoint must use HTTPS (or loopback HTTP); redirects are rejected. Names must match `FARM_INSTANCE_ID`. The Team usage page reports bounded counters, recorded spend, missing/estimated metering, window/timezone and freshness. An unavailable source stays unknown. It does not return private prompts, results, login identities or inference tokens. Provider-reported/estimated costs are not a billing guarantee.

## Backups and restore

Configure root-private `backup.env` in **each** profile's settings directory:

```dotenv
FARM_BACKUP_DIR=/var/lib/harness-farm-instances/owner/backups/encrypted
FARM_BACKUP_RECIPIENTS=/var/lib/harness-farm-instances/owner/backup-recipients.txt
FARM_BACKUP_DATABASE=1
FARM_BACKUP_REMOTE=RESTRICTED_REMOTE:private-farm/owner
```

Change paths/remote prefix per person. Install only age **public recipients** on the server. Keep age private keys outside it. Configure a restricted rclone remote under that service user's home; do not share remote credentials between profiles. Require both archive and checksum delivery. The backup job dumps the database, snapshots private state, encrypts it and uploads both artifacts without remote deletion. An upload failure fails the unit and cannot create a success receipt. Doctor checks complete archive/delivery freshness separately from intermediate database-dump freshness.

```bash
systemctl start harness-farm-backup@owner.service
systemctl enable --now harness-farm-backup@owner.timer
```

Repeat for all profiles after manually proving delivery. Timers spread their daily starts over two hours. Monitor backup unit failures and doctor warnings; configuring a timer alone is not proof of backup delivery.

Download an archive from the off-host destination and decrypt it with `restore-state.sh ARCHIVE --identity KEY --to EMPTY_DIRECTORY` on an isolated recovery machine. Verify the checksum manifest. Restore `database/database.dump` into a **separate, empty authenticated PostgreSQL database** with matching PostgreSQL tools and the Farm roles present. The custom-format dump covers the Farm's `public` schema. Supabase's managed schemas are recovered through the provider's backup process. On a fresh Auth project, relink each existing Farm member with `farm member add EMAIL --role ROLE` and verify sign-in before exposing the restored console. Use the original role so the existing member ID, work and permission boundary are preserved.

Set `FARM_RESTORE_DB_URL` from a private environment file for the recovery target. Then run `/opt/harness-farm/.venv/bin/python /opt/harness-farm/scripts/linux/restore-database.py EMPTY_DIRECTORY/database/database.dump`. The helper refuses a target with existing Farm data and restores in one transaction. Prepare the `farm_app` and `farm_system` roles and their grants to the target's Farm connection role first. To restore policies from a Supabase source, use an initialized Supabase-compatible target with its Auth schema and roles present.

State and database backups are taken separately. Completion evidence can refer to a run missing from the restored database. The Farm retains the answer and evidence and requires manual reconciliation for such conflicts. Archive verification and database recovery are separate checks.

Restore logins, OAuth stores, memory and job artifacts only to the matching profile. Check private directory ownership and revalidate provider sessions. The restore helper rejects traversal, escaping links and special-device entries. `--into-live` is a deliberate later cutover operation; it refuses an active service and preserves the previous state, but does not restore PostgreSQL automatically. Never use it as a restore drill.

## Failures, upgrades and rollback

MCP writes with uncertain outcomes stop as `outcome_unknown`. Verify the external service before resubmitting. Failover after dispatch requires a trusted read-only hint. Before dispatch, a confirmed transport failure may move a call to another account. AI edits require reconciliation approval before continuation. Failed answers can continue through bounded recovery while preserving provenance and the chosen model and effort.

Terminal answers are fsynced locally before the final SQL update and replayed to SQL after a database outage. A completed worker is never reexecuted by spool replay. Damaged/conflicting spool entries and output files are retained for review under `ai-completions` and job-result storage. Preserve these during recovery; do not manually delete them to clear a warning.

Tracked MCP and API receipts are replayed from `task-completions`. If the Farm process stops before a receipt is saved, the expired call becomes `outcome_unknown` and can be reconciled by the next operator.

For upgrades, first prove an off-host backup and a usable rollback build. Stop all profile backends, check no CLI children remain, update the root-owned code/dependencies, apply migrations per profile, build consoles individually, then smoke-test one profile before the rest. Do not use the legacy single-instance `update.sh`. Restore the previous code/build for a code-only rollback; schema rollback or database restore needs a deliberate plan for that migration and post-backup work. Never point two workers at one database during a transition.

Remote CLI work needs a checkout under `/var/lib/harness-farm-instances/NAME/workspaces`. A PC folder such as `D:\project` is not a server path. This release does not include a PC execution runner. Fosa, Tensor and other projects remain consumers of this central service.

Before calling this release ready: test signed-in consoles, member removal, private/shared access, Claude and Codex client discovery/long job/result/reply, database/provider outages, cancellation, off-host **database plus state** recovery, and a measured three-person 48-hour server workload. Verify other IDE clients individually before promising compatibility.
