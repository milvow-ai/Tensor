# 00 — What Milvow already has (audit, 2026-10-02)

Everything here was observed on 2026-10-02 through the connected tools or live fetches. Nothing is assumed.

## Domain and email

| Item | Observed | How |
|---|---|---|
| Registrar | milvow.com at Namecheap, registered 2025-08-17, **expires 2027-08-17, auto-renew OFF** | Namecheap connector |
| Website | A record → 76.76.21.21 and `www` CNAME → `cname.vercel-dns.com`; the page itself is built with Framer (footer badge) | Namecheap DNS, Firecrawl |
| Mailbox provider | **Zoho Mail, India data centre**: MX `mx.zoho.in`, `mx2.zoho.in`, `mx3.zoho.in` | Namecheap DNS |
| SPF | `v=spf1 include:zoho.in ~all` | Namecheap DNS |
| DKIM | selector `zmail._domainkey` present (RSA key) | Namecheap DNS |
| DMARC | `v=DMARC1; p=none; rua=…; ruf=…; adkim=s; aspf=s; pct=100` (reports go to a milvow.com mailbox) | Namecheap DNS |
| Public contact address | `hello@milvow.com` | contact page |
| `n8n` subdomain | A → 177.7.33.91. The user says this VPS is expired and not available. | Namecheap DNS + user |

So SPF, DKIM and DMARC already exist on the main domain. Whether the Zoho plan allows SMTP/IMAP/API automation is not yet known. That is in research/03 and a question to the user.

## Website content that prospects will see

Cold-email recipients usually check the sender's site, so this matters for the pipeline.

- Positioning: "AI-native growth partner for B2B: AI workflows and websites, plus search and ecommerce." Based in Delhi, India.
- The FAQ says Milvow also delivers "the same work white-label for agencies".
- The contact form asks for location (US, Canada, UK & Europe, UAE & Middle East, Australia & NZ, India, Other) and budget bands (Under $5,000 / $5–10k / $10–25k / $25k+ / Not sure yet).
- **Case studies:** the home page lists "Cigna Smart Health Systems", "Aetna Health Data Ecosystem" and "ConnexAI Platform Website". Fetching `/project/cigna-smart-health-systems` returned the home page, so the case-study page does not resolve.
- `/pricing` returned the contact page.
- The "by the numbers" counters render as `0+` and `0` in the fetched HTML. They may be animated counters that a scraper captures at zero.

Whether the case studies are real client work is a question for the user. If they are template placeholders, they need to go before any outreach.

## Connected services in this Claude session

| Service | State | Relevance |
|---|---|---|
| Supabase | Org "milvow-ai's Org" with 3 projects, **all INACTIVE (paused)**: APTIX (ap-southeast-2), milvow-dev (ap-south-1), Milvow_bsp (ap-south-1). The free plan allows 2 active projects (see research/04). | Database and memory candidate. A new or restored project is needed. |
| Clay | Workspace "Milvow's Workspace" connected. It exposes 19 enrichment functions, including Find People at Company, Work Email (provider waterfall), Company Job Openings, Website Technology Stack, Company News and Enrich Company. | Enrichment, metered by Clay credits |
| Gmail | milvow.ai@gmail.com: 11 sent messages, 1,161 unread | Not a cold-sending inbox. Possible notifications or drafts. |
| Firecrawl | Connected. It hit its per-minute rate limit during this session. | Scraping and search, metered |
| Namecheap | Connected (read used only) | DNS changes need the user's word |
| Notion, Google Drive, Calendar, Vercel, Figma | Connected, not used yet | Calendar is relevant for booking calls |
| Cloudflare | Needs authorisation in claude.ai connector settings | Optional free hosting |

## This Claude Code cloud container

- Outbound network is allowlisted. Reachable: github.com, api.github.com, raw.githubusercontent.com, pypi.org, registry.npmjs.org. Blocked: huggingface.co, census.gov, onetcenter.org, openrouter.ai, groq, overpass-api.de, dns.google, milvow.com (via WebFetch). MCP connectors are not affected, because they run server-side.
- So this container cannot be Tensor's scraping runtime unless the environment's network access is widened. That is changed in the environment settings, from the cloud environment menu → Edit → Network access.
- Hermes is not installed here.
- Nothing in `~/.claude` survives the container, so durable state must be committed to the repo.

## Not available (per the user)

- No n8n instance right now.
- The VPS is expired.
- Budget is $0.
