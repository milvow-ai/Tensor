# 02 — Finding companies, enriching them, finding and verifying emails

Checked 2026-10-02 by a research worker. Stars come from the GitHub API; release dates from PyPI, the Go proxy and crates.io. UNVERIFIED marks anything not confirmed on a primary page.

## Open-source tools

| Repo | Stars | Licence | Last push / release | Lang | Use | Gotchas |
|---|---|---|---|---|---|---|
| gosom/google-maps-scraper | 6,235 | MIT | 2026-09-24 / v1.18.1 | Go | Maps places (name, phone, site, rating, reviews), crawls sites for emails; web UI and REST API | **Breaches Google ToS** (see below). Needs proxies at scale. Fast mode caps at 21 results/query; ~120 places/min at `-c 8 -depth 1`. |
| speedyapply/JobSpy | 4,378 | MIT | push 2026-10-01; **PyPI 1.1.82 is from 2025-07-28** | Py | Job posts (Indeed, LinkedIn, Glassdoor, Google, ZipRecruiter) as hiring signals; Indeed adds employee and revenue labels | LinkedIn rate-limits ~page 10 per IP; Glassdoor after ~30 requests per IP; ~1,000 jobs/search. LinkedIn ToS applies. |
| unclecode/crawl4ai | 84,620 | Apache-2.0 | 0.9.4 (2026-09-23) | Py | Website → LLM-ready markdown, plus LLM extraction (team/about pages, owner names) | Needs Playwright/Chromium |
| reacherhq/check-if-email-exists | 10,072 | **AGPL-3.0 or paid commercial** | crate 0.11.7 (2026-02-05) | Rust | SMTP mailbox check without sending; catch-all detection | **Needs outbound port 25.** The commercial-use licence needs a legal read. |
| AfterShip/email-verifier | 1,631 | MIT | v1.5.0 (2026-09-10) | Go | Syntax, MX, disposable, role and catch-all checks; optional SMTP | SMTP is off by default and needs port 25. Safest licence. |
| projectdiscovery/wappalyzergo | 1,109 | MIT | v0.3.3 (2026-09-27) | Go | Tech-stack detection (Shopify, chat widgets, booking tools) | Raw HTML and headers only, so widgets injected by JavaScript can be missed |
| enthec/webappanalyzer | 585 | GPL-3.0 | 2026-09-16 | JSON | Wappalyzer fingerprint database | GPL |
| yc-oss/api | 240 | none stated | updated daily | JSON | 6,268 YC companies (team size, site, isHiring) | Startups; mostly outside likely ICPs |
| laramies/theHarvester | 17,747 | GPL-2.0 | 2026-10-01 | Py | OSINT: emails, names, subdomains | GPL |
| ScrapeGraphAI/Scrapegraph-ai | 31,484 | MIT | 2026-09-25 | Py | LLM-driven scraping; alternative to crawl4ai | – |

## Free data sources the worker rated underrated

| Source | Facts | Gotchas |
|---|---|---|
| **Overture Maps Places** | Over 53M places with phone, website and brand (release 2026-03-18.0). CDLA-Permissive-2.0 / Apache-2.0. Free on S3/Azure via `pip install overturemaps`. | Small-business coverage varies |
| **Foursquare OS Places** | Over 100M places, Apache-2.0, updated monthly | Since Oct 2025 you need a portal token, or use the gated Hugging Face copy |
| **HTTP Archive on BigQuery** | `httparchive.crawl.pages.technologies` holds Wappalyzer detections, so you can list every site running Shopify or a given widget. BigQuery's free quota is 1 TB/month. | Always filter on partition date and client, or one query can use up the quota |
| **ATS job-board JSON** (no auth) | Greenhouse `boards-api.greenhouse.io/v1/boards/{token}/jobs`; Lever `api.lever.co/v0/postings/{site}?mode=json`; Ashby `api.ashbyhq.com/posting-api/job-board/{name}` | You need each company's board slug |
| UK Companies House API | Free with a key, 600 requests per 5 min. Officer lists give decision-maker names. | UK companies only |
| Overpass (OSM) | Regular use: under 100 queries and under 10 MB per day on the main instance | "Commercial use should use self-hosted or paid Overpass servers" |
| Meta Ad Library API | All ad types only for ads delivered to the EU/UK | No US commercial ads |
| Google Ads Transparency (BigQuery) | Free dataset | **EEA and Turkey only**; no spend data |

## Free tiers of commercial tools

| Tool | Free allowance | Notes |
|---|---|---|
| Apollo | 900 credits/seat/year, granted monthly (~75/mo); 2 sequences; Gmail only | Card UNVERIFIED. ToS: no access "on behalf of any person or entity other than you", which matters if Milvow ever prospects for clients. |
| Hunter | 50 credits/mo; free API key; Finder charges only on a hit | – |
| Snov.io | 50 credits per 30 days, 100 recipients | **No export, API or integrations** on free |
| Prospeo | 100 credits/mo | **No Enrich API on free** |
| GetProspect | 50 valid emails + 100 verifications; API listed | – |
| Tomba | 25 searches + 50 verifications/mo | UNVERIFIED (blog only) |
| **Clay** | 500 actions/mo, 200 rows/table. **Clay MCP gives "500 free Clay credits on first connect".** | Already connected (research/00). Monthly free data credits UNVERIFIED. |
| Findymail | One-time 10 finder + 10 verifier credits | UNVERIFIED |
| **Firecrawl** | **1,000 credits/mo** (1,000 pages or 500 searches), 2 concurrent requests, no card | Live pricing page; many blogs still say 500 one-time |
| Google Places API (New) | Text Search IDs-only is unlimited. **Fields with website, phone and rating bill at the Enterprise SKU: 1,000 free/month**, then $20–35 per 1,000. | Billing account needed (UNVERIFIED) |
| Yelp Places API | **No free tier**; 30-day evaluation trial only | – |
| Outscraper | First 500 businesses free (rolling 30 days) | UNVERIFIED (site blocked the scraper) |

## Legal and ToS flags

- **Google Maps:**
  - Platform ToS §3.2.3(a) forbids scraping Maps content for use outside the services.
  - The consumer terms forbid using Maps to build "a business listings database, mailing list, or telemarketing list".
  - Google sued SerpApi on 2025-12-19. A 2026-07-20 dismissal of the core claim is reported (UNVERIFIED).
- **LinkedIn:**
  - User Agreement §8.2 bans scraping bots.
  - In hiQ v. LinkedIn (N.D. Cal. 2022) hiQ was held to have breached the agreement: a $500k judgment and deletion of the scraped data. The anti-hacking-law ruling does not protect against contract claims.
- **Apollo:** no reselling or redistribution of contact data, and no scraping of the platform.
- **Port 25:** both SMTP verifiers need it. GitHub Actions, GCP, Oracle and Render block it (research/04). A host with port 25 open, or a SOCKS5 proxy, is required for self-hosted SMTP verification.

## Worker's suggested $0 path (a suggestion, not a decision)

1. Discover via Overture or Foursquare places (clean licences), or HTTP Archive for tech-defined segments such as Shopify.
2. Crawl with crawl4ai and detect tech with wappalyzergo.
3. Check hiring signals via the ATS JSON endpoints.
4. Guess email patterns and verify with AfterShip email-verifier on a host with port 25 open.
5. Spot-check with free credits from Hunter, Prospeo, GetProspect and Clay.
