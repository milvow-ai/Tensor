# Harness Farm: private team release

Owner direction: 2026-11-11. This supersedes Tensor-first and public-SaaS assumptions for this release.

## Product

Harness Farm is the team's central resource manager and MCP/AI-worker orchestrator. Claude Code, Codex, Cursor, Antigravity and other MCP clients are operators. They retain the reasoning and can use the same connected tool accounts without reconnecting those tools in each IDE. Tensor is an optional consumer, not the product or a release dependency. Fosa is another consumer.

The first deployment serves exactly three people on a private VPS. Purchase and deployment follow a separately reviewed readiness decision. No public registration, billing platform or autonomous planning engine is required for this release.

## Required behavior

- Each person has their own authenticated identity, revocable client tokens, private AI logins, private context and memory database. A shared tenant's row-level security alone does not provide per-person privacy. Do not provision everybody into the existing default tenant and call that isolation.
- Explicit team pools expose selected MCP or AI accounts to selected people. A single MCP connection and a pool use the same discovery/call protocol. Exact account pinning remains available. The caller's IDE login and the selected downstream Jira/Clay/etc. login are separate identities.
- Owner/admin can manage membership, pool access, capacity and usage. Members cannot perform administrative changes through direct database/API calls even if they bypass the dashboard. Removing access invalidates the person's associated tokens.
- Every run records the authenticated operator and selected worker/account. An operator email is trusted only when bound to server-side membership. Caller-supplied labels and IDE names are not verified account identity.
- Display actual account email/name when available, with an honest unknown state when unavailable. Show selected model/effort and observation time; a configured default does not prove what a CLI actually used.
- Usage distinguishes Farm-observed consumption, local budgets and provider-reported remaining capacity. Unknown is never zero. Usage, failures, retries and freshness remain inspectable.
- Recovery preserves the original goal, bounded context, lineage and artifacts. A different worker continues only when compatible and authorized. Uncertain file edits or external writes require reconciliation, not blind replay. Original results remain available even during a temporary database outage.
- MCP tool schemas and structured results survive pooling. Tool annotations guide safe retry behavior. Timeout, cancellation, disconnection and restart release capacity and reach an inspectable terminal or reconciliation state.
- Dashboard supports private member onboarding, explicit personal/shared resource selection, CLI/MCP connect/disconnect, login status, live jobs and role-appropriate administration. Owner-only login screens and decorative placeholders do not satisfy this requirement.

## Execution location

Server workers operate on server workspaces. A remote `cwd=D:/project` cannot access a laptop drive. Work on a laptop folder requires an authenticated local runner or an explicit checkout/patch transfer. Moving only the gateway while retaining all CLI workers on the laptop does not remove their memory load. Never silently copy CLI home directories, credentials or unrelated project files.

## First-release deployment architecture

Use three personal instances and one shared team instance on the same VPS. They reuse one read-only code installation, but each has a separate Linux service identity, PostgreSQL database and database role, state/home directory, secrets file, registry, CLI profiles, workspaces and loopback port. This is the chosen first-release boundary because today's process-wide settings, provider IDs and credential storage cannot safely represent private per-person resources inside one shared process.

Each IDE connects to its person's Farm. Each personal Farm may register the team Farm as an upstream HTTP MCP, using a distinct membership-bound token and namespace. The shared instance supports explicitly shared external MCP/API resources. Native CLI pools and project memory remain in personal instances: a shared CLI's Linux identity could read other sessions/files. The team Farm never connects back to the personal Farms.

Central administrative usage reporting must use a bounded, read-only reporting interface that returns counters and freshness, not personal prompts, results, credentials or inference tokens. Provisioning, reporting and project-memory APIs must be implemented and tested before this architecture is called ready.

## Release evidence

These are gates, not statements that implementation has passed:

| Gate | Required evidence |
|---|---|
| Personal data boundary | Two people with identical resource IDs cannot read/write each other's private data, memory, logins, results or files; shared access works only by explicit grant |
| Membership and administration | Direct command enqueue, token reuse after removal, forged operator, wrong-tenant IDs and member privilege escalation are refused |
| Jobs and MCP effects | Interrupted worker/server, provider timeout, DB outage at result save, cancellation and ambiguous writes have bounded, truthful behavior; no duplicate external effect |
| Operations | Linux install/update checks; bounded restart and resource limits; authenticated readiness; dependency outage does not cause restart storms |
| Recovery | Encrypted database + state backup, off-host copy, isolated restore and smoke test; stale/failing backups alert |
| Client compatibility | Authenticated discovery/call/long job/result/reply from installed Claude and Codex clients; Cursor/Antigravity verified separately before advertising support |
| Dashboard | Member/owner sessions, real identity/usage, onboarding failures, keyboard/mobile flows and no hidden admin bypass |
| Cutover | One Farm per database, conservative initial concurrency, explicit server workspace mapping, rollback procedure and no secrets in logs/artifacts |
| Sustained operation | Three-person representative workload and failure injection, then a 48-hour server soak with measured queue age, memory, failures and recovery |

No system can promise zero errors. Release readiness means failures are bounded, observable and recoverable, and the gates above have evidence.

## Reference checks

- MCP authorization and token audience boundaries: https://github.com/modelcontextprotocol/modelcontextprotocol/blob/main/docs/docs/2026-07-28/tutorials/security/authorization.mdx (checked 2026-11-11).
- systemd start limiting: https://www.freedesktop.org/software/systemd/man/latest/systemd.unit (checked 2026-11-11).
- systemd process/resource limits: https://freedesktop.org/software/systemd/man/latest/systemd.exec.html (checked 2026-11-11).
