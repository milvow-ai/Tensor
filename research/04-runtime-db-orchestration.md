# 04 — Where it runs, where it remembers, how it is scheduled

Checked 2026-10-02 by a research worker. WebFetch was blocked for docs.github.com and supabase.com, so those were read via Firecrawl and search snippets. UNVERIFIED marks anything not confirmed on a primary page.

## Compute and scheduling

| Option | Free allowance (verified) | Gotchas |
|---|---|---|
| **GitHub Actions** | Private repos on Free: **2,000 min/mo**, 500 MB artifacts, 10 GB cache. Public repos: standard runners free. | Every job is **rounded up to a whole minute**, so a job every 15 min ≈ 2,880 min/mo, which is over quota. Batch the work into fewer jobs. |
| GHA `schedule` | Minimum interval is 5 min | Runs can be delayed or dropped under load, especially at the top of the hour. Public repos auto-disable after 60 days without activity. 6 h max per job. |
| GHA port 25 | – | Not in GitHub's docs (UNVERIFIED). Assume it is blocked; send via 587/465 or an HTTPS API. |
| GHA 2026 pricing | Hosted runner prices cut up to 39% from 2026-01-01 | The planned $0.002/min fee for self-hosted runners was **postponed**, with no new date |
| **Oracle Always Free** | **A1 is now 2 OCPU / 12 GB** (halved from 4/24); 2× E2.1.Micro; 200 GB block; 10 TB egress/mo | Over-limit instances were terminated from 2026-08-18, and some within limits were also disabled. Idle reclaim: 7 days at p95 CPU, network and memory all <20%. Card required. **Port 25 blocked** (exemption by request; whether free accounts qualify is UNVERIFIED). Treat it as revocable. |
| GCP e2-micro | 1 instance in us-west1/us-central1/us-east1; 30 GB disk; 1 GB egress/mo | Billing account required. Port 25 blocked; 587/465 open. |
| Fly.io | No free tier for new orgs | – |
| Render | Free web services; 750 h/mo | **Workers and crons are not free.** Free Postgres expires after 30 days. Ports 25/465/587 blocked. |
| Railway | $5 trial, then $1/mo credit | Too small to run 24/7 |
| Koyeb | Mistral acquisition (Feb 2026); free tier status UNVERIFIED | – |
| Cloudflare Workers Free | 100k requests/day; 5 cron triggers; 10 ms CPU per call; Python Workers support cron | Fine as a "ping and enqueue" trigger; too little CPU for the pipeline |
| Deno Deploy | Classic shut down 2026-07-20; new plan has ≤10 crons per revision | JS/TS only |
| Claude Code Routines | Hourly minimum (research/01) | Uses subscription quota; allowlisted network |

## Supabase Free (pricing page scraped today)

- **Limits:**
  - 500 MB database; 5 GB egress plus 5 GB cached egress; 1 GB file storage; 50k MAU.
  - Shared CPU, 500 MB RAM. No backups. Logs kept 1 day.
  - Edge Functions: 500k invocations/mo, 150 s wall clock and 2 s CPU per call.
- **Projects:**
  - **Max 2 active projects.** A project **pauses after 1 week** of low database activity.
  - A paused project can be restored for up to 1 year (search snippet).
  - Milvow currently has 3 paused projects (research/00).
- **Extensions:** pgmq (Supabase Queues), pg_cron and pgvector are free. They share the 500 MB and the CPU.
- **Hosted MCP server:** `https://mcp.supabase.com/mcp`, with `read_only=true`, `project_ref=…` and `features=` options. Claude can query the database directly through it.

## Orchestrators and queues

| Tool | Licence | Stars | Latest | Fit |
|---|---|---|---|---|
| n8n | Sustainable Use License | 206,470 | 2.41.5 (2026-10-01) | Internal use free. You may build client workflows on your own instance if clients can't edit them, but may not host, white-label or resell access. Heavy. Not available now (research/00). |
| Windmill | AGPLv3 + proprietary EE | 18,084 | 1.821.0 | Runs Python natively; medium weight |
| Activepieces | MIT core + `ee/` | 24,834 | UNVERIFIED | TS-first, no-code |
| Trigger.dev | Apache-2.0 | 16,454 | 4.7.0 | TS-first |
| Inngest | Server SSPL (→ Apache after 3 yrs) | 5,907 | 4.21.1 | Licence caveat |
| Hatchet | MIT | 8,042 | py 1.41.1 | Postgres "Lite" mode |
| Prefect | Apache-2.0 | 23,964 | 3.8.7 | Needs a server process |
| Temporal | MIT | 23,410 | py 1.34.0 | Too heavy |
| **pgmq** | Apache-2.0 (PyPI package) | 5,307 | py 1.1.4 | Already on Supabase |
| **procrastinate** | MIT | 1,404 | 3.10.0 | Pure Python, Postgres-only task queue |
| pg-boss | MIT | 4,012 | 12.35.1 | Node only |
| **DBOS Transact** | MIT | 1,600 | 3.2.0 | Durable Python workflows stored in Postgres; no separate server |

## Data UIs for non-developers

| Tool | Licence and notes |
|---|---|
| Supabase Studio | Included with every hosted project |
| NocoDB | **Licence moved from AGPL to Sustainable Use on 2026-01-09**; internal use OK |
| Teable | AGPL-3.0 CE, Postgres-native; CE has no automations or AI |
| Baserow | MIT core + premium |
| Metabase / Grafana | AGPLv3; free for internal use |

## Notifications

| Channel | Limits |
|---|---|
| **Telegram Bot API** | ~1 msg/s per chat, 20 msg/min per group; free |
| ntfy.sh | 250 msgs/day **per IP**; shared IPs can use up the quota |
| Discord webhooks | No official limit published; about 30 msg/min (UNVERIFIED) |

## Worker's recommendation (a suggestion, not a decision)

1. **Database:** Supabase Free, with pgmq or procrastinate as the queue, and DBOS if durable steps are needed.
2. **Workers:** GitHub Actions cron in a **private** repo, batched into a few longer jobs at odd minutes. Public repos are free, but their logs are public, which is risky for lead data.
3. **Always-on box (optional):** Oracle A1, treated as revocable.
4. **Sending:** 587/465 or a provider's HTTPS API, never port 25.
5. **Alerts:** Telegram.
