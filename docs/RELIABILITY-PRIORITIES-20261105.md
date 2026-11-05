# Reliability priorities and distributed worker direction — 2026-11-05

User decision: PC remains first server. Implement in this order:
1. Fix false quota/exhaustion flags, then automatic usage monitoring.
2. Durable failed-job recovery, same provider first; authenticated operator approval for a different provider.
3. Continuous operation: supervision, readiness, alerts, restart recovery, backup/restore, fault tests and soak.
4. Media integration, private onboarding (only Asad requested), distributed PC workers and portable server deployment.

## Usage acceptance

Separate three facts: Farm-observed daily/monthly consumption; configured local budgets; provider-reported remaining allowance. Never infer a subscription quota from tokens or silently turn unavailable data into zero. Keep source, observation time, provider window duration/reset and freshness. Report failures/retries and incomplete metering. Calendar aggregation uses an explicit timezone; rolling provider windows retain their true semantics. Poll supported read-only interfaces serially with strict timeouts/backoff; never send inference prompts just to measure remaining capacity. Monitoring failures must not mark an account exhausted or undo manual pauses. Codex app-server officially documents account/rateLimits/read and account/usage/read; verify installed-version support. Claude documents status-line rate_limits but that is not proof of a headless polling endpoint. Gemini/Antigravity remain unknown unless verified separately.

Sources checked 2026-11-05: https://learn.chatgpt.com/docs/app-server ; https://code.claude.com/docs/en/statusline ; https://geminicli.com/docs/reference/commands/

## Server + small worker client (design, not implemented)

The full Farm, database, scheduling/routing, approvals, policy, usage accounting, user administration and proprietary coordination logic stay on the owner's PC/server. A separately packaged client contains only execution adapters, authenticated outbound transport, local process supervision and a bounded encrypted/checksummed transfer spool. Do not distribute the full Farm repository, central database, server secrets or other users' sessions. Client code on another person's machine can be inspected/copied; the defensible boundary is retaining server functionality server-side, not promising uncopyable binaries.

Each enrolled device has its own revocable identity bound server-side to a person. It runs with a standard OS identity, explicit workspace roots and per-account CLI/MCP homes. Logins remain on that device. Install only pinned dependencies needed for the selected tools, in a private application directory; project files live in a separate owner workspace. No unrelated users' private tasks are scheduled on a teammate's PC by default. Shared compute is an explicit pool opt-in with resource limits and a separate execution identity; a machine's administrator can inspect work executed on that machine.

Clients pull authenticated assignments over TLS (no inbound PC port required), advertise available CPU/RAM/concurrency and send heartbeats/checkpoints/results asynchronously. Server persists assignments before issuing them; leases and generation/fencing tokens reject stale completions. Same durable job and recovery model applies locally and remotely. Never blindly replay file edits or external effects after a disconnected client: quarantine/reconcile ambiguous runs first. Transfer only task-required files/artifacts with size limits and per-user authorization; never sync entire CLI homes or credentials.

A sleeping/offline PC is unavailable capacity. Queue work or use another explicitly eligible online worker; a small always-on fallback will still be needed for work that must execute while every teammate PC is off. This design reduces central compute load but does not guarantee zero latency or zero downtime. Measure latency, queue age and recovery under the actual five-person workload before making performance claims.

## Verification gates

Quota: positive/negative error fixtures, terminal ordering, transient health transitions, no assistant-prose false flags. Usage: day/month boundaries, unknown vs zero, duplicate/late events, source freshness, tenant isolation, provider timeout and restart persistence. Recovery: restored input/context, one recovery child, depth bounds, operator authorization, edit reconciliation. Operations: killed worker/server, lost network, exhausted disk, sleeping worker, backup restore and sustained workload. Remote client: cross-user access denial, revoked device, forged/replayed assignment, stale completion, paths/symlinks, disconnect after external effect, no credentials in artifacts.

## Current checkpoint

Media commit f08f8ce is local only; 12 tests and targeted type/lint checks passed. Recovery is uncommitted and targeted tests had 12 passed/4 failed. Private-instance kit exists but is not provisioned or fully checked; the per-person full-instance approach is now a temporary isolation option, not the intended teammate distribution model. Existing live Farm has not received these changes. R1 worker 715a958d-ba7a-4c3b-ba5a-79ea142098b9 is finishing quota fixes in the repair worktree; remaining work must be independently reviewed and tested before release.
