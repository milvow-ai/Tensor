## D rows

| Capability | Existing resource | Open source | Free option | Paid upgrade | Build ourselves? | Recommendation |
|---|---|---|---|---|---|---|
|Admin shell, CRUD screens|shadcn/ui starters; react-admin; Refine|All MIT|Free|Vendor tiers UNVERIFIED|Yes, thin: the screens are the product|Fork one starter; replace Clerk hosted auth with local login; ship static files behind the API|
|Pipeline builder|React Flow|MIT|Free; attribution optional|Pro $169 or $289/mo|Yes: palette generated from stage schemas|Adopt library, build editor; stored pipeline JSON stays canonical|
|Node config forms|RJSF, JSON Forms, uniforms|Apache-2.0; MIT; MIT|Free|None|Thin: Pydantic to JSON Schema to form|RJSF with @rjsf/shadcn; JSON Forms backup; skip uniforms (stale)|
|Tables, charts|TanStack Table; Recharts; ECharts; Tremor|MIT; MIT; Apache-2.0; Apache-2.0|Free|None|No|TanStack Table plus Recharts; skip @tremor/react (unmaintained)|
|Low-code console|Appsmith, Windmill, ToolJet, Budibase, Retool|Apache-2.0; AGPL/Apache plus EE; AGPL-3.0; per-package; closed|Free tiers; Retool is cloud-first|Appsmith $15/user/mo|Pipeline editor and dossier view are custom in every option|Fallback only: Appsmith|
|Workflow tools as config UI|n8n, Node-RED, Kestra|Sustainable Use (internal use); Apache-2.0; Apache-2.0|Free self-host|Enterprise tiers|No|Reject: each would own the pipeline definition [mine]|
|Render and screenshot evidence|Playwright for Python|Apache-2.0|Free|Browserbase from $20/mo|Thin `web.render` in `worker-browser`|Store PNG (fold and full page), DOM, headers, final URL, UTC time, viewport, tool version, SHA-256 [mine]|
|Fallback fetchers|browser-use, Stagehand, Firecrawl, Jina Reader, Browserless|MIT; MIT; AGPL-3.0; Apache-2.0 repo; SSPL|Limited free tiers|Paid tiers|No|Rare fallback: cost, nondeterminism, or third-party sight of prospect URLs|
|robots.txt etiquette|RFC 9309 (Sept 2022)|Python robotparser (RFC coverage UNVERIFIED)|Free|None|Thin policy|4xx: may crawl. 5xx or unreachable: assume disallow. Cache at most 24 h. Not access control. Named user agent with contact URL; one render per prospect [mine]|
|Performance evidence|PSI v5, CrUX, CrUX History|Closed Google APIs|PSI 25,000/day (secondary source); CrUX 150/min|CrUX quota not purchasable|No: call, store raw JSON|PSI mobile first; CrUX only when present; 404 means unknown, not slow|
|Lab, accessibility, structured data, mobile|Lighthouse, axe-core, extruct|Apache-2.0; MPL-2.0; UNVERIFIED|Free|None|Parse JSON-LD ourselves; mobile via Playwright emulation|Mobile-Friendly Test and API retired 2023-12-01; no public Rich Results Test API found|
|Facts safe to cite [mine]|Playwright, DOM, headers, CrUX|–|–|–|–|What a recipient sees on a phone in seconds: no tap-to-call, no CTA above the fold, form field count, listed broken links, missing title or H1, stale copyright year, hero image MB, failed HTTPS redirect. CrUX p75 only if present, dated|
|Facts NOT to cite [mine]|Lighthouse, axe-core|–|–|–|–|Lighthouse scores or exact lab timings (vary per run); accessibility counts or legal framing (axe finds about 57% of WCAG issues); revenue-loss figures; ranking claims; anything fetched through a bot wall|

## E rows

| Name | URL | Capability | Pricing (paid) | Free tier / limits | API | Key limitations | License | Local (Windows) / VPS fit | Integration method | Maturity evidence | Relevance H/M/L |
|---|---|---|---|---|---|---|---|---|---|---|---|
|shadcn starters (satnaing, Kiranism, arhamkhnz)|github.com/satnaing/shadcn-admin|Admin scaffold|Free|Free|None|Two use Clerk hosted auth; two pin Table 8.21; no backend|MIT|Static build; fits|Fork, call Tensor API|React 19, Tailwind 4, Recharts 3.8, Vite 8 or Next 16.2/16.3; commit dates UNVERIFIED|H|
|react-admin, Refine|github.com/marmelab/react-admin|CRUD framework|Vendor tiers UNVERIFIED|Free|REST data providers|CRUD-shaped; no graph editor or evidence viewer|MIT|SPA; fits|Optional scaffold|react-admin 5.15.4 (2026-09-25), 24 releases/12 mo; Refine 5.0.12 (2026-04-02), 8|M|
|React Flow|reactflow.dev|Node-graph editor|Pro $169 or $289/mo|Free; attribution optional|JS lib|Layout, validation, undo are ours|MIT|Browser lib; fits|Graph to pipeline JSON|@xyflow/react 12.12.0 (2026-09-24), 15 releases/12 mo|H|
|RJSF, JSON Forms, uniforms|rjsf-team.github.io/react-jsonschema-form|Schema-driven forms|Free|Free|JS libs|Secret and model pickers need custom widgets|Apache-2.0; MIT; MIT|Fits|Pydantic JSON Schema|RJSF 6.11.0 (2026-09-29), 27 releases; JSON Forms 3.8.0 (2026-06-16), 2; uniforms 4.0.0 (2025-02-28), 0|H|
|TanStack Table|tanstack.com/table|Headless data grid|Free|Free|JS lib|No ready inline-edit UI|MIT|Fits|Approval queue, inbox|9.2.4 (2026-08-28), 7 releases/12 mo|H|
|Recharts, ECharts, Tremor|recharts.org|Charts|Free|Free|JS libs|@tremor/react 3.18.7 last published 2025-01-13|MIT; Apache-2.0; Apache-2.0|Fits|Funnel, provider usage|Recharts 3.10.1 (2026-07-25), 14 releases; ECharts 6.1.0 (2026-05-19)|H|
|Windmill|windmill.dev|Scripts, flows, apps|Team, Enterprise (UNVERIFIED)|CE: unlimited runs, 50 users, 3 workspaces, 10 SSO users|REST, CLI|Low-code app editor labelled legacy; flows live in its DB; SAML, audit logs, Git sync are EE|AGPLv3 or Apache-2.0 by directory; proprietary EE|Docker Compose or K8s; heavy|Scripts calling Tensor API, or full-code React apps it hosts|1.821.0, 18,084 stars (research/04)|M|
|Appsmith|appsmith.com|Low-code UI over REST, SQL|Business $15/user/mo; Enterprise $2,500/mo per 100 users|Free: 5 users (cloud), 5 workspaces, 3 Git repos, Google SSO|REST, Postgres datasources|Internal-tool look [mine]; Custom widget pulls libraries from CDN, so use Iframe widget for our editor|Apache-2.0|Docker; self-host caps UNVERIFIED|Datasource = Tensor API|Version, stars UNVERIFIED|M|
|ToolJet|tooljet.com|Low-code UI, workflows|Basic $29, Pro $79, Team $199 per builder/mo|2 builders, 50 end users|REST, SQL; JS or Python queries|SSO, RBAC, audit, Git sync paid|AGPL-3.0|Docker|As Appsmith|"38,000+" stars (vendor claim)|L|
|Budibase|budibase.com|Low-code UI, automations|Pro $19/mo; Premium $50/creator|Self-host: 1 workspace, unlimited apps; user cap sources conflict|REST, SQL|Per-package licences|Per package (GPL core UNVERIFIED)|Docker|Plugin CLI|UNVERIFIED|L|
|n8n|n8n.io|Workflow automation|Cloud, Enterprise (UNVERIFIED)|Self-host free, internal business use|Webhooks, REST|Workflows live in its DB; no dashboards; Python Code node needs external task runner since v2.0|Sustainable Use v1.0; .ee. files paid|Docker or npm; heavy|Webhook to Tensor|2.41.5 (2026-10-01), 206,470 stars (research/04)|L|
|Node-RED|nodered.org|Flow editor|None|Free|HTTP nodes|JS runtime; dashboards separate (UNVERIFIED)|Apache-2.0|npm|None|UNVERIFIED|L|
|Kestra|kestra.io|Orchestrator with UI|Enterprise (UNVERIFIED)|OSS: no-code editor, topology; RBAC, SSO, audit are EE|REST|JVM plus Postgres; Apps edition UNVERIFIED|Apache-2.0|Docker|Python tasks in containers|Vendor blog cites Kestra 2.0|L|
|Retool|retool.com|Hosted internal-tool builder|Paid (UNVERIFIED)|5 users, 500 workflow runs/mo, 5 GB database|REST, SQL|Cloud-first; self-host reported Enterprise-only (sources conflict); cloud must reach local DB [mine]|Proprietary|Poor local-first fit|None|n/a|L|
|Playwright (Python)|playwright.dev/python|Render, screenshot, DOM|Free|Free|Python lib|Needs browser binaries; bot walls can block|Apache-2.0|Windows, Linux (UNVERIFIED here)|`worker-browser`|1.63.0 (PyPI 2026-09-15; npm 2026-09-04)|H|
|browser-use|github.com/browser-use/browser-use|LLM-driven browsing|Cloud (UNVERIFIED)|Free|Python 3.11+|Pre-1.0; token cost; nondeterministic|MIT|Fits|Rare fallback|0.13.10 (2026-09-04)|L|
|Stagehand, Browserbase|browserbase.com|AI browser SDK, hosted browsers|Developer $20/mo (25 concurrent, 100 h); Startup $99/mo|3 concurrent, 1 browser hour/mo, 15 min sessions|SDK (TS, Python)|Hosted browsers route prospect traffic via third party|MIT (SDK)|SDK local, browsers cloud|Rare fallback|4.1.0 (npm, PyPI 2026-09-09)|L|
|Firecrawl|firecrawl.dev|Scrape, crawl to markdown|Hobby $19/mo (5,000 credits); Standard $99; Growth $399|About 1,000 credits (cadence UNVERIFIED)|REST; firecrawl-py (MIT)|Self-host lacks Fire-engine (anti-bot, proxy); compose "not production"; our connected account returned "Insufficient credits" 2026-10-03|AGPL-3.0|Docker Compose; heavy|Fallback fetcher|firecrawl-py 4.46.2 (2026-10-02)|M|
|Jina Reader|jina.ai/reader|URL to markdown|Paid: 500 RPM, 2M TPM|Free key: 100 RPM, 100K TPM, 10M tokens|REST|Third party sees URLs; Elastic bought Jina AI (Oct 2025), free tier future UNVERIFIED|Apache-2.0 repo; service terms separate|Hosted only|Fallback fetcher|Elastic filing: acquired 2025-10-07|L|
|Browserless|browserless.io|Browsers as a service|20k, 180k, 500k units/mo plans (prices UNVERIFIED)|1,000 units/mo, 2 concurrent (unit = 30 s)|REST, CDP|Closed-source commercial or CI use needs commercial licence|SSPL-1.0 or commercial|Docker image|Skip; Playwright direct|UNVERIFIED|L|
|PageSpeed Insights API v5|developers.google.com/speed/docs/insights/v5/get-started|Lighthouse lab plus CrUX field data|Free|25,000/day, 400/100 s (secondary sources); key recommended|REST|Lab scores vary; runs from Google's network; field data only if CrUX has it|Google API terms|HTTPS call|`web.audit`, raw JSON as evidence|v5 since Nov 2018|H|
|CrUX API, History API|developer.chrome.com/docs/crux/api|Real-user Core Web Vitals, 28-day window|Free|150 queries/min per project; History: 40 weekly periods|REST|Origin must be public and "sufficiently popular" (threshold undisclosed); 404 when no data|Google terms|HTTPS call|Second call after PSI|Page dates UNVERIFIED|H|
|Lighthouse CLI|github.com/GoogleChrome/lighthouse|Local lab audit|Free|Free|CLI, Node module|Run-to-run variance (docs); Node 22.19+|Apache-2.0|Windows UNVERIFIED; VPS fine|Subprocess JSON|13.5.0 (2026-09-18), 10 releases/12 mo|M|
|axe-core|github.com/dequelabs/axe-core|Accessibility rules|Deque paid tools UNVERIFIED|Free|JS; @axe-core/playwright 4.13.0; axe-playwright-python 0.1.8 (community)|About 57% of WCAG issues; "incomplete" needs humans|MPL-2.0|Inject via Playwright|Internal flags only|4.13.0 (2026-08-05), 8 releases/12 mo|M|
|extruct|pypi.org/project/extruct|JSON-LD, microdata extraction|Free|Free|Python lib|Dormant: last release 2024-11-08|UNVERIFIED (PyPI field empty)|Fits|Or parse JSON-LD ourselves|0.18.0|L|

## Build vs adopt decision

**Primary: build a thin SPA and adopt the parts.** Vite (or Next static export) with a shadcn/ui starter (MIT), React Flow for the pipeline graph, RJSF with @rjsf/shadcn for per-node config, TanStack Table for approvals and inbox, Recharts for funnel and usage. Pydantic emits JSON Schema, so one definition drives validation, forms and node palettes, and the pipeline stays a versioned JSON document in Postgres. It ships as static files behind the API: identical on a Windows PC and a VPS, no extra database, all MIT or Apache-2.0. Keep only the starter's layout and theme; replace Clerk with a local login.

**Why not adopt:** the two screens that matter, the pipeline builder bound to Tensor's schema and the dossier with evidence and screenshots, are bespoke in every option. Adoption buys only CRUD tables, the cheapest part to build. n8n, Kestra, Node-RED and Windmill flows would each become a second owner of the pipeline definition.

**Runner-up and fallback: Appsmith (Apache-2.0).** It reads the Tensor API as a datasource, so Python stays the source of truth, and it gives inline-edit tables for approvals and the inbox quickly. It lost because its look is internal-tool, not SaaS-grade [mine]; the pipeline editor still needs our React Flow page in an Iframe widget; app definitions live in Appsmith's store; SAML and OIDC are paid. Switch only if approvals must go live before the React shell is ready.

**Also rejected:** Windmill (heavy; low-code editor labelled legacy; revisit only if Q4 picks it as the engine), Retool (cloud-first), ToolJet (AGPL, per-builder pricing). For ad-hoc table browsing use NocoDB or Metabase from research/04.

## Unverified

- Checked 2026-10-03. Vendor prices and limits come from search-engine summaries of official pages. WebFetch was blocked for developers.google.com, developer.chrome.com and rfc-editor.org; Firecrawl had no credits; GitHub API and HTML were blocked, so stars and commit dates are missing except research/04 figures.
- PSI quota: secondary sources only.
- Firecrawl free-plan size; Budibase user cap and GPL scope; Appsmith self-host caps; Retool self-host policy; Kestra Apps edition; Node-RED dashboard; Windows runs for every tool; extruct licence; react-admin and Refine paid prices; Windmill, n8n, Kestra and Browserless prices.
- "No Rich Results Test API" rests on search snippets.
- Core Web Vitals thresholds not checked.
