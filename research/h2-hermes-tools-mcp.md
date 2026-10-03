# H2: Hermes Agent tool surface and MCP client

Commit c8301ea6 (2026-10-03). Primary sources only. `file:line` is repo source (tool files omit `tools/`); bare doc names (e.g. `browser.md:917`) live under `website/docs/`. Every agent turn also needs an LLM provider key (`python-library.md:29`), so all rows carry LLM-token cost. Roughly 100 tools (`tools-reference.md:11`); core list `toolsets.py:12-41`.

## Tool inventory

| tool / toolset | does | backend(s) | key needed / cost | local or remote | Tensor |
|---|---|---|---|---|---|
| `web_search` / `web`, `search` | ranked results (title, url, description; limit 1-100, default 5); operators pass through | Firecrawl (default), SearXNG, Brave, DDGS, Exa, Parallel, Tavily, Perplexity, Keenable, xAI, OpenAI-native, Nous gateway | none required (keyless vendor ring on by default); free: SearXNG, DDGS, Brave free tier; paid: Perplexity, xAI, keyed Firecrawl/Exa/Parallel/Tavily/Keenable | remote (SearXNG self-hosted) | H |
| `web_extract` / `web` | up to 5 URLs to markdown, PDFs ok; 15,000-char budget, full text saved to disk | Firecrawl, Tavily, Exa, Parallel, Keenable, Perplexity (snippets only); not SearXNG/Brave/DDGS/xAI | same vendors; Firecrawl 500 free credits/mo (docs) | remote | H |
| `browser_navigate/snapshot/click/type/press/scroll/back/get_images/vision/console` / `browser` | drive pages via accessibility tree with @refs; screenshot plus vision; JS eval | local Chromium (agent-browser), Lightpanda, Camofox, CDP attach, Browserbase, Browser Use cloud, Firecrawl cloud, Nous gateway | local free; cloud needs `BROWSERBASE_API_KEY`+`BROWSERBASE_PROJECT_ID`, `BROWSER_USE_API_KEY` or `FIRECRAWL_API_KEY` (paid) | local or remote | H |
| `browser_cdp`, `browser_dialog` / `browser` | raw CDP and JS dialogs; registered only if a CDP endpoint exists | `/browser connect`, `browser.cdp_url`, Browserbase, Camofox | free | either | M |
| `browser_exec` / `browser-use` | model-written Python with helpers (`new_tab`, `js`, `cdp`, `capture_screenshot`); default browser tool when `browser.backend` is unset; hidden without terminal | same browsers; `session=` isolation | free locally | host process | H |
| `browser_vault_*` (5) | sign-in, fill, TOTP from a vault | local vault | none | local | L |
| `vision_analyze` / `vision` | image path or URL; native pixels or auxiliary model | LLM | tokens | remote LLM | H |
| `read_file`, `write_file`, `patch`, `search_files` / `file` | read converts PDF, docx, xlsx, sqlite to text (PDF via lazily installed firecrawl-anydoc; scanned pages come back empty) | local fs | free | local | M |
| `terminal`, `process_manage` / `terminal` | shell and background jobs | local (Git Bash on Windows, unsandboxed), docker, ssh, singularity, modal, daytona, vercel_sandbox | cloud backends paid | either | L (risk) |
| `execute_code` / `code_execution` | Python RPC to 7 whitelisted tools (no MCP, no delegate); 300 s, 50 calls | local or remote kernel | free | either | M |
| `delegate_task` / `delegation` | parallel subagents (default 10 concurrent, depth 1) | in-process | tokens | local | M |
| `cronjob_manage` / `cronjob` | scheduled agent runs with delivery | built-in scheduler | free | local | M |
| `memory`, `session_search`, `todo_list`, `clarify`, `skill_*` | agent state and skills | SQLite, files | free | local | L |
| `image_generate`, `video_*`, `text_to_speech`, `x_search` | media and X search | FAL, OpenAI, xAI, Krea, OpenRouter, ElevenLabs, Edge TTS | keys; paid (Edge TTS free) | remote | L |
| `computer_use`, `ha_*`, `kanban_*`, `discord*`, `spotify_*`, `feishu_*`, `yb_*`, `a2a_*`, `manage_*`, `desktop_*` | niche, GUI or off-by-default | various | various | various | L |
| `mcp__<server>__<tool>` / `mcp-<server>`, `tool_search`, `tool_describe`, `tool_call` | any MCP server, deferred discovery | see MCP table | per server | either | H |
| control: `hermes-cli`, `hermes-api-server`, `hermes-webhook`, `safe`, `coding`, `custom_toolsets`, `agent.disabled_toolsets` | restrict what a run may do; webhook runs get only web_search, web_extract, vision_analyze, clarify | n/a | n/a | n/a | H |

Sources: `toolsets.py:44,115-119,186,207,244`; `web_tools.py:552-565`; `web-search.md:23-43`; `browser_tool.py:1365`; `browser_use_cli.py:828`; `code_execution_tool.py:43-45`; `config_defaults.py:281,428-432,1379-1384`.

## Browser and web details

- **Backends.** Resolved by `browser.cloud_provider`/`backend`/`engine` (`browser_tool_cloud.py:45-140`; `config_defaults.py:427-459`). Nothing set: `browser_exec` on the packaged headless Chromium (`browser.md:76-80`); `/browser use off` restores the built-in tools. Camofox is Firefox-based in Docker (`browser.md:305`). Lightpanda has no screenshots (`browser.md:508`). CDP attach needs a dedicated `--user-data-dir` on Chrome 136+ (`browser.md:569`); WSL to Windows Chrome goes through chrome-devtools-mcp (`use-mcp-with-hermes.md:121-218`). Cloud: Browserbase, Browser Use, Firecrawl (`plugins/browser/*`).
- **Capabilities.** JS rendering by real Chromium. Click, type, press, scroll, dialogs (CDP supervisor). `browser_vision` screenshot (`--full`, `browser_tool.py:1224`) goes to the main model natively or an auxiliary vision model. Downloads: docs say none (`browser.md:917`); no download code found in `browser_*.py`. PDFs: no browser PDF tool; `web_extract` accepts PDF URLs. JS eval unrestricted unless `browser.restrict_evaluate` (`browser.md:757`).
- **Persistence and headless.** Default is a throwaway profile, headless (`browser.headed` false), reaped after 120 s idle. Persistence options: Browser Use cloud profiles, Camofox `managed_persistence`, and `browser.use_real_profile` (copies your active Chromium logins to `~/.hermes/browser-profile/`; off by default; Windows needs the browser fully quit, `browser.md:220-227`).
- **Disk artifacts.** Screenshots `~/.hermes/cache/screenshots/browser_screenshot_<uuid>.png`, deleted after 24 h (`browser_tool.py:1259-1272`). Oversize snapshots spill to `cache/web/browser-snapshot-<digest>.txt` (accessibility text, not HTML; `browser_tool_snapshot.py:55-58`). `web_extract` stores `cache/web/<host>-<sha256(url)[:10]>.md`, overwritten on re-extract (`web_tools_truncate.py:69-80`), plus `extract-index.json` (url digest, file, fetched_at, title; 500 entries; `web_result_cache.py:6,29-30`). Optional WebM recordings (`browser.record_sessions`, default off, 72 h). All paths sit under `HERMES_HOME`; Tensor must copy and hash them itself. No raw HTML, HAR, headers or hashes; raw DOM only via `browser_console(expression)` or `browser_exec` writing files (workspace `cache/browser-use/workspace/<task>`, `browser_use_cli.py:283-293`).
- **Search and extract.** Free: DDGS, SearXNG (JSON format must be enabled), Brave free tier (key, 2,000 q/mo per docs). Default keyless ring rotates Exa MCP, Parallel MCP, Keenable, Firecrawl free tiers (`keyless_mcp.py:21-23`), so queries leave the machine: set `web.keyless_fallback: false` and `web.keyless_rescue: false` (both default true, `config_defaults.py:405-409`). Paid or keyed: Firecrawl, Tavily, Exa, Parallel, Perplexity, Keenable. xAI and OpenAI-native are LLM-generated search. Per-capability `web.search_backend`/`web.extract_backend`; results cached 20 min.
- **Terminal and code sandboxing.** `terminal.backend` default local, unsandboxed (`local.py:1`), provider and adapter secrets stripped from child env (`local_env_policy.py:119`), approvals `smart`; cron, `-q` and unattended runs deny (`config_defaults.py:1682-1686`). Docker: one persistent container, cap-drop ALL, no-new-privileges, tmpfs (`docker.py:3,307-313,379`), optional iron-proxy egress firewall (`docker_egress.py:1-8`). `execute_code` strips KEY/TOKEN/SECRET env (`code-execution.md:233-239`).
- **Guards.** SSRF guard blocks private and loopback hosts (`url_safety.py`; `browser_tool.py:658-660`). `security.website_blocklist` is off by default (`config_defaults.py:1786`). No robots.txt handling: grep for "robots" in `tools/`, `agent/`, `plugins/web`, `plugins/browser` finds nothing.
- **Suitability as evidence agent.** Suited: JS-capable browser, screenshots plus vision, isolated parallel sessions, API server and Python embedding (`api-server.md:17-56`), MCP client for a Tensor store, per-toolset lockdown. Unsuited: no deterministic capture or provenance, ephemeral overwritable files, non-deterministic LLM actions, prompt injection from page content into an agent holding tools, and a stealth ecosystem (Browser Use stealth, residential proxies, CAPTCHA solving: `browser.md:12,54`; `scrapling` skill) that conflicts with compliance. Best fit: analyst or constrained browsing sub-agent; Tensor does its own capture and hashing.

## MCP client capabilities

| feature | supported? | config key | source |
|---|---|---|---|
| transports | stdio, Streamable HTTP (default for `url`), SSE; SSE retry on some first-connect failures | `command,args,env,cwd`; `url,headers`; `transport: sse` | `mcp_tool_transport.py:371-403,561-581,608-653` |
| static auth | yes; `${VAR}` from profile `.env`; unset var in url/headers fails closed | `headers`, `env` | `mcp_tool_transport.py:117`; `mcp_tool_config.py:366-375` |
| OAuth 2.1 PKCE | yes; CIMD then DCR; own client ok; paste-back or proxied callback for remote hosts | `auth: oauth`, `oauth.*` | `mcp_oauth.py:1225`; `mcp-config-reference.md:323-422` |
| device code (RFC 8628) | yes, terminal login only | `oauth.flow: device` | `mcp_oauth_device.py:204`; `mcp-config-reference.md:342-378` |
| headless refresh | yes: auto refresh with cross-process lock; gateway and reload never open a browser, a dead refresh token parks the server until `hermes mcp login` | `HERMES_HOME/mcp-tokens/<server>.json` | `mcp_oauth.py:56-80,206,229-233,343-353`; `mcp.md:782` |
| mTLS, CA, proxy | yes | `client_cert`, `client_key`, `ssl_verify`; `HTTPS_PROXY` | `mcp-config-reference.md:53-55`; `mcp.md:345` |
| multiple servers | yes; 4 connects at a time | `mcp_servers.<name>`; `mcp.discovery_concurrency` | `mcp_tool_discovery.py:27-36` |
| include/exclude | yes, exact or glob; include wins | `tools.include`, `tools.exclude` | `mcp_tool_registration.py:222-223`; `mcp_tool_schema.py:243` |
| dynamic tool search | yes: BM25 bridge defers MCP/plugin tools; `tools/list_changed` refresh (tools only) | `tools.tool_search.enabled/defer/threshold_pct` | `tool_search.py:1-8`; `config_defaults.py:1987-1996`; `mcp_tool_health.py:100-120` |
| timeouts, reconnect | tool call 300 s, connect 60 s, HTTP keepalive 180 s; 5 reconnects (3 first connect), backoff cap 60 s, then parked and probed every 300 s | `timeout`, `connect_timeout`, `keepalive_interval` | `mcp_tool_common.py:41-55`; `mcp_tool.py:233-246`; `mcp_tool_server_run.py:406-423` |
| resources, prompts | yes, as wrapper tools when the server has them | `tools.resources`, `tools.prompts` | `mcp_tool_handlers.py:674-686` |
| sampling | yes, default on (rpm 10, 30 s, 4096 tokens, 5 tool rounds) | `sampling.*` | `mcp_tool_server_run.py:268-270`; `mcp_tool_sampling.py:100-105` |
| elicitation | form mode via approval surface; URL mode declined | `elicitation.enabled/timeout` | `mcp_tool_server_run.py:273-276`; `mcp.md:978` |
| roots | not found (grep of `mcp_tool*.py`) | n/a | n/a |
| trust, supply chain | `untrusted` forces approval for tools lacking `readOnlyHint`; OSV malware preflight on stdio (fail-open); description injection scan warns | `trust: full or untrusted` | `mcp_tool.py:34-51,478-489`; `mcp_tool_handlers.py:52-81`; `mcp_tool_schema.py:32-44` |
| names and CLI | `mcp__<server>__<tool>`, 64-char clamp (`mcp.md:569-579` and `tool-search.md` still say `mcp_<server>_<tool>`); `hermes mcp add/remove/list/test/configure/login/reauth/install/catalog/serve`, `/reload-mcp` | n/a | `mcp_tool_schema.py:153-173`; `mcp_config.py:1110-1131` |
| Hermes as server; catalog | `hermes mcp serve` is stdio, messaging tools only; `optional-mcps/` has 65 manifests, all HTTP transport (55 OAuth, 10 no-auth) | `lazy`, `idle_timeout_seconds` for heavy servers | `mcp.md:993-1090`; `optional-mcps/*/manifest.yaml` |

Config file `~/.hermes/config.yaml` (per profile), secrets in `~/.hermes/.env`. Minimal example (server module name is hypothetical):

```yaml
mcp_servers:
  tensor:                         # tools appear as mcp__tensor__<tool>
    command: python
    args: ["-m", "tensor.mcp_server"]
    env: { TENSOR_DB_URL: "${TENSOR_DB_URL}" }
    tools: { include: [get_company, save_evidence], resources: false, prompts: false }
    timeout: 120
  vendor:
    url: https://mcp.example.com/mcp
    auth: oauth                   # or headers: { Authorization: "Bearer ${TOKEN}" }
    trust: untrusted
```

## Messaging, webhooks, email

- **Gateway.** `hermes gateway`; 29 listed platforms (`index.md:31-66`), default-deny allowlists (`index.md:350-352`), Windows service via Task Scheduler (`index.md:696`). Platform toolsets such as `hermes-telegram` and `hermes-email` get the full core set including terminal (`toolsets.py:217-243`).
- **Email adapter.** IMAP poll (15 s) and automatic in-thread SMTP replies to allowlisted senders, `MEDIA:` attachments (`email.md:105,128-181`); `EMAIL_*` env vars (`email.md:215-228`).
- **RISK: outbound email paths.** (1) email adapter replies; (2) `deliver: email` on webhook routes and cron (`webhooks.md:89,354`; `EMAIL_HOME_ADDRESS`, `email.md:106`); (3) `hermes send --to` (`cli-commands.md:479-485`); (4) skills `himalaya` and `google-workspace` (`gmail send`) plus plain `terminal`; (5) catalog MCP servers `klaviyo`, `close`, `intercom` (tool lists not inspected). There is deliberately no agent-callable send_message tool (`toolsets.py:200-201`; `send_message_tool.py` has no registration), but grep found no sendmail, smtp, himalaya or gmail-send patterns in `tools/approval*.py` or `tirith_security.py`, so terminal-sent mail is not specifically gated. Tensor must configure no `EMAIL_*`, install neither skill, deny `terminal`, and allowlist MCP tools.
- **Webhooks.** HTTP on 8644, HMAC or GitLab token, `INSECURE_NO_AUTH` exists; routes carry `prompt`, `skills`, `toolsets`, `deliver`, `deliver_only` (`webhooks.md:53,77-94`). `hermes-webhook` is the safe four-tool subset (`toolsets.py:44,244`), but `index.md:749` says full tools. Payloads are untrusted.
- **API server.** OpenAI-compatible on 8642, `/v1/runs` with an approval endpoint (`api-server.md:17-56,448-570`); toolset `hermes-api-server` (`toolsets.py:207`). A2A inbound 9900, outbound tools off by default (`a2a.md`).

## Skills and plugins of interest

- **Bundled skills:** `blocked-page-recovery` (Wayback, archive.today, Jina, provenance rules), `grounded-citations`, `competitor-news-monitor`, `product-price-monitor`, `github` (gh CLI), `google-workspace`, `notion`, `airtable`, `pdf`, `xlsx`; dev/ops: `systematic-debugging`, `sdlc-review`, optional `docker-management`.
- **Optional skills:** `domain-intel` (passive subdomain, SSL, WHOIS, DNS), `searxng-search`, `duckduckgo-search`, `parallel-cli`, `rss-feeds`, `watchers`, `har-derived-api-client`, `mcporter`, `mcp-oauth-remote-gateway`. Avoid `scrapling` (Cloudflare bypass) and `web-pentest`.
- **Extension points:** plugin web backend (`search`/`extract` envelope) or browser provider (`create_session` returns `cdp_url`) could route fetches through a Tensor capture service (`web-search-provider-plugin.md:146-207`; `browser-provider-plugin.md:62-105`). `ctx.call_mcp` is allowlisted per plugin (`plugins.md:895-930`).
- **Catalog:** `optional-mcps` entries of note: `indeed` (job listings), `globalping`, `attio`, `neon`, `supabase`, `notion`, `context7`. `plugin-catalog/` lists 417 plugins (408 community, 8 official; e.g. `crawl4ai`, `apify`), unaudited. GitHub MCP is deliberately absent (`mcp.md:302-306`).

## Unverified

- Vendor behaviour (JS rendering, robots, stealth, CAPTCHA) for Firecrawl, Exa and others; Tool Gateway pricing; free-tier quotas (docs only).
- Downloads or PDF printing through `browser_exec` or raw CDP (possible in principle, not shipped).
- Whether `--full` screenshots are full-page; image format and size.
- Windows status of Lightpanda, Camofox and cua-driver; shell is Git Bash (`local.py:115-181`).
- Doc/code drift: MCP tool naming, webhook toolset, "no downloads".
- Only outlined or skimmed: `telegram.md`, `web-dashboard.md`, `delegation`, `security.md`, skill internals. Tests not read.
- MCP roots assumed absent from grep alone.
