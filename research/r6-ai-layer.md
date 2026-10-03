# r6: AI layer, models and tooling (Q6)

Checked 2026-10-03. USD per MTok, in/out. "sec." = second-hand source (LiteLLM price table, search summaries). aws.amazon.com, docs.aws.amazon.com, openrouter.ai and arxiv.org were blocked from the sandbox, so facts from them are sec. or UNVERIFIED. research/01 is reused, not repeated.

## D rows

| Capability | Existing resource | Open source | Free option | Paid upgrade | Build ourselves? | Recommendation |
|---|---|---|---|---|---|---|
|Model gateway, routing (#49)|LiteLLM, Portkey gateway, OpenRouter|LiteLLM MIT (`enterprise/` excluded); Portkey MIT|Self-host either; OpenRouter `:free`|OpenRouter ~5.5% credit fee (sec.)|Thin router: `openai` SDK per `base_url`, `anthropic` SDK, CLI adapter|Thin router now. LiteLLM optional, hash-pinned. OpenRouter is one provider.|
|Structured extraction (#22,#24)|Anthropic JSON-schema outputs, instructor, PydanticAI, BAML, Outlines|All OSS|Ollama `format` (research/01)|Anthropic structured outputs|Pydantic schemas, validate and retry|Pydantic plus provider-native schema; instructor if retries needed; Outlines only for self-hosted vLLM.|
|Evaluation harness (#50)|promptfoo, DeepEval, Inspect|MIT, Apache-2.0, MIT|All local|–|Golden sets (P)|promptfoo for model-swap regressions plus pytest golden sets; Inspect only for agent evals.|
|Reasoning, personalisation (#24,#25,#30)|Claude Sonnet 5.5, Opus 5.5|–|Subscription via local `claude -p`|Sonnet 5.5 $2/$10, Opus 5.5 $4/$20; Batch -50%; cache reads 0.1x|Prompts, rubrics (P)|Claude stays main brain; CLI now, API key later.|
|Claim grounding (#31)|Anthropic Citations; FActScore-style atomic checks|FActScore repo|Exact-quote match in code|Citations: `cited_text` not billed as output|Yes, claim-to-evidence rules (P)|Code check first, then cross-family LLM verifier. Citations cannot combine with structured outputs.|
|Cheap bulk inference|AWS credits, OpenRouter, Groq, Ollama|Ollama|AWS credits; Groq, `:free` (research/01)|Bedrock batch -50%; Anthropic Batch -50%|No|Spend credits on experiments; never depend on them.|
|Research, orchestration worker (#19-23,#35)|Claude Code CLI, Agent SDK, Hermes|Hermes MIT; SDK MIT file, Commercial Terms|`claude -p` on subscription|API key; web search $10/1,000|Thin adapter behind CapabilityRequest|CLI adapter now, API worker later; Hermes only for cheap mechanical chores.|
|Enrichment, ops tools (#20,#27,#28)|Apollo, Clay, Hunter, Zoho Mail, Supabase, Playwright, Firecrawl MCPs|Playwright, Firecrawl|Playwright local; Firecrawl keyless tier|Provider plans|REST adapters for metered paths|MCP for exploration inside Claude CLI; REST adapters in the pipeline (ledger, idempotency).|
|Usage metering (#15)|CLI JSON `total_cost_usd`; Hermes `--usage-file`; API `usage`|–|–|–|Yes, ledger (P)|Normalise provider usage into our ledger; CLI cost is a client-side estimate.|

## E rows

| Name | URL | Capability | Pricing | Free tier / credits | API | Automation / terms | Key limitations | License | Local/VPS fit | Integration method | Maturity evidence | Relevance H/M/L |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
|AWS Bedrock|aws.amazon.com/bedrock|Claude and open models; batch|Claude global: Sonnet 5.5 $2/$10, Haiku 4.5 $1/$5, Opus 5.5 $4/$20 (sec.); regional +10% (Anthropic docs). Qwen3-Coder-Next $0.50/$1.20, GLM-5 $1/$3.20 (sec.). Batch -50% on select models, S3 JSONL, up to 24h (sec.)|New accounts: $100 + up to $100 for activities incl. Bedrock, 6 months (AWS What's New 2025-07-15, sec.); credits paying Claude tokens UNVERIFIED|bedrock-runtime, mantle|AWS terms; IAM or API key|No free inference; Claude on mantle lacks structured outputs, Batches API, web tools|Proprietary|Cloud API; either host|boto3, `anthropic[bedrock]`|12 Claude models listed, 3 invitation-only (Anthropic docs)|H|
|Bedrock mantle|bedrock-mantle.{region}.api.aws|OpenAI-compatible Responses and Chat Completions; Claude Messages at /anthropic/v1/messages|Per model, as Bedrock|Same AWS credits|OpenAI SDK, base_url .../v1|Bedrock API key (bearer) or SigV4 service bedrock-mantle|Serves gpt-oss, Qwen, GLM, DeepSeek, Kimi, Gemma (sec.); founder's two models UNVERIFIED|Proprietary|Either host|Hermes custom provider; OpenAI SDK|Responses API 2025-12; console, CloudWatch updates 2026-06 (AWS What's New titles)|H|
|OpenRouter|openrouter.ai|Multi-provider gateway|Provider price + ~5.5% credit fee (sec.). Cheap JSON models (LiteLLM table, UNVERIFIED): gpt-oss-20b $0.02/$0.09, gpt-oss-120b $0.04/$0.17, mistral-nemo $0.02/$0.03|`:free` 20 RPM, 50 RPD (1,000 after ~$10) per research/01; BYOK 1M requests/month free, then 5% (blog 2025-10; Aug-2026 change UNVERIFIED)|OpenAI-compatible|`provider.data_collection:"deny"`, `zdr:true`; account training toggles for paid and free|Extra hop; free endpoints may train; price varies by upstream|Proprietary SaaS|Either host|openai SDK, base_url|~1,292 models listed (sec., 2026-09)|M|
|Anthropic API|platform.claude.com/docs|Messages, Batch, caching, structured outputs, Citations, web search|Sonnet 5.5 $2/$10; Haiku 4.5 $1/$5; Opus 5.5 $4/$20; Batch -50%; cache write 1.25x (5m) or 2x (1h), read 0.1x; web search $10/1,000; web fetch tokens only|Small new-user credits|REST, SDKs|Commercial Terms; API key|Citations cannot combine with structured outputs; no recursive or min/max schemas; first call compiles grammar (cached 24h)|Proprietary|Either host|`anthropic` SDK; `output_config.format`; `citations.enabled`|Batch, caching, structured outputs, Citations GA (docs 2026-10-03)|H|
|Claude Code CLI|code.claude.com/docs/en/headless|Local agent worker (`claude -p`)|Subscription or API tokens|Included in subscription; ordinary individual use|`--output-format json` or `stream-json`; `--json-schema`|`claude setup-token`; no third-party reuse of subscription credentials (research/01)|`--bare` ignores subscription login and will become the `-p` default; heavy 24/7 load is a risk|Proprietary, Commercial Terms|PC yes; VPS needs API key|Subprocess with `--mcp-config`, `--allowedTools`, `--max-turns`, `--permission-mode`|Flags read from CLI reference 2026-10-03|H|
|Claude Agent SDK|code.claude.com/docs/en/agent-sdk/overview|Claude Code agent loop as a library|API tokens|None|Python, TypeScript|Commercial Terms; third parties may not offer claude.ai login (unless approved); use API key|Runs the Claude Code binary; heavyweight|MIT file; Commercial Terms govern|Either host|`claude-agent-sdk`|0.2.163 (2026-09-30); 148 releases since 2025-09|M|
|LiteLLM|pypi.org/project/litellm|SDK and proxy routing to many providers|Free; enterprise paid|Self-host|OpenAI-compatible proxy|–|Supply-chain compromise 2026-03-24 (1.82.7, 1.82.8); many dependencies|MIT; `enterprise/` commercial|Yes|pip, pinned with hashes|1.103.2 (2026-10-01); 910 releases since 2023-07|M|
|Portkey gateway|github.com/Portkey-AI/gateway|Gateway: routing, fallbacks, guardrails|OSS free; hosted, enterprise paid|Self-host|OpenAI-compatible|–|Gateway 2.0 (enterprise merge) is pre-release|MIT|Yes: `npx @portkey-ai/gateway`, Docker|base_url|portkey-ai SDK 2.3.4 (2026-07-23)|L|
|instructor|pypi.org/project/instructor|Pydantic-validated outputs with retries|Free|Free|Python SDK|–|Wraps provider clients; no routing|MIT|Yes|Library|1.17.0 (2026-09-09); 98 releases since 2023-07|M|
|PydanticAI|pypi.org/project/pydantic-ai|Typed agents and outputs|Free|Free|Python SDK|–|Agent framework; overlaps our harness|MIT|Yes|Library|2.54.0 (2026-10-03); 344 releases since 2024-05|M|
|BAML|pypi.org/project/baml-py|Prompt DSL, schema-aligned parsing|Free|Free|Python via codegen|–|Extra toolchain and DSL|Apache-2.0|Yes|Library|0.226.2 (2026-09-01); 115 releases since 2024-05|L|
|Outlines|pypi.org/project/outlines|Constrained decoding for local models|Free|Free|Python|–|Needs a local inference stack; no effect on hosted Claude|Apache-2.0|Yes|Library|1.3.3 (2026-08-06); 84 releases since 2023-03|L|
|promptfoo|github.com/promptfoo/promptfoo|Prompt and model evals|Free|Free|CLI, Node|Runs 100% locally (README)|Now part of OpenAI; README says stays MIT|MIT|Yes|YAML configs, CI|README banner read 2026-10-03; npm version UNVERIFIED|H|
|DeepEval|pypi.org/project/deepeval|Pytest-style LLM metrics, judges|Free|Free|Python|–|Judge metrics need an LLM; same self-preference risk|Apache-2.0|Yes|pytest|4.2.8 (2026-10-02); 527 releases since 2023-08|M|
|Inspect AI (UK AISI)|pypi.org/project/inspect-ai|Eval framework, agent and sandbox support|Free|Free|Python|–|Heavier than small golden sets need|MIT|Yes|Python tasks|0.3.276 (2026-10-02); 253 releases since 2024-04|L|
|Hermes Agent|github.com/NousResearch/hermes-agent|General agent CLI, gateway, cron, MCP client|Free; pay model provider|Any provider, incl. `--provider bedrock`|`hermes -z "..."` (text only); `hermes chat --oneshot -q ... --format stream-json`|MIT; `--yolo` skips approvals; `--usage-file` writes cost and tokens|No schema flag in chat options; pre-1.0 (`-q` changed at 0.21); default `--max-turns` 500; auto-injects rules, memory, skills (`--ignore-rules` skips). Verdict: fine for tool-loop chores, not for schema-bound extraction|MIT|Windows native (install.ps1), Linux and WSL2 (install.sh)|`mcp_servers:` in `~/.hermes/config.yaml`; `-m`, `--provider`|0.19.0 (2026-07-20); 11 releases since 2026-05-14|M|
|Apollo MCP|docs.apollo.io/docs/apollo-mcp|People and company search, enrichment, sequences|Uses Apollo credits|Any plan incl. free (Apollo claim, sec.)|Hosted `mcp.apollo.io/mcp`|Official; OAuth, or X-Api-Key headless|Enrichment spends credits; not a ledger. Apollo GraphQL's MCP server is a different product|Proprietary|Remote|`claude mcp add --transport http apollo https://mcp.apollo.io/mcp`|Official docs page; community server Inferensys/apollo-io-mcp exists|H|
|Clay MCP|clay.com/guides/clay-mcp|Search, enrich, drive Clay tables|Workspace credits UNVERIFIED|UNVERIFIED|`api.clay.com/v3/mcp`, Streamable HTTP|Official; OAuth|Plan and credit cost UNVERIFIED|Proprietary|Remote|`claude mcp add clay --transport http ...`|Connected in this environment 2026-10-03|H|
|Hunter MCP|hunter.io/api-documentation#mcp|Domain search, email finder, verifier, leads|Hunter plans|Free plan 50 credits/month incl. API (sec.)|Hunter remote MCP|Official; auth UNVERIFIED|hunter-io/hunter-mcp repo is deprecated|Proprietary|Remote|Remote MCP URL from Hunter docs|Repo README points to remote server|M|
|Zoho Mail MCP|zoho.com/mail/help/mcp|Read, search, send, organise mail|UNVERIFIED|UNVERIFIED|Zoho-hosted MCP server URL|Official; OAuth; admin picks tools|Assistant-oriented, not a send queue (sending is Q3); community repos exist|Proprietary|Remote|Add Zoho MCP URL to client|Zoho help pages for Cursor, VS Code, ChatGPT|M|
|Supabase MCP|github.com/supabase-community/supabase-mcp|SQL, migrations, docs, branches|MCP free; project plan applies|Supabase free plan (limits UNVERIFIED)|Hosted `mcp.supabase.com/mcp`; `project_ref`, `read_only=true`, `features`|Official (docs); client login, UNVERIFIED|Dev tool: use read_only, avoid production data|UNVERIFIED|Remote, or local via Supabase CLI|MCP URL with query params|Supabase-hosted; connected here|L|
|Playwright MCP|github.com/microsoft/playwright-mcp|Browser automation|Free|Free|stdio: `npx @playwright/mcp@latest`|Official (Microsoft); no auth|Browsers must be installed; target-site ToS apply|Apache-2.0|Yes|`claude mcp add playwright npx @playwright/mcp@latest`|Official Microsoft repo; README covers many clients|M|
|Firecrawl MCP|github.com/firecrawl/firecrawl-mcp-server|Scrape, search, crawl, map, parse|Credits|Hosted keyless tier for scrape, search, parse (rate-limited)|`npx -y firecrawl-mcp` or hosted|Official; `FIRECRAWL_API_KEY` for other tools|Our connector returned "insufficient credits" 2026-10-03|MIT|Self-host via `FIRECRAWL_API_URL`, or hosted|stdio or HTTP|Official firecrawl org repo|M|

## Task → model routing proposal

Candidates read: free now / paid later.

| Task | Model tier | Candidate models (free now / paid later) | Reason |
|---|---|---|---|
|Normalise, dedup, TTL, legal-rule checks|Code, no LLM|–|Deterministic, testable, free.|
|Page-to-facts JSON extraction (bulk)|T1 small|Ollama qwen3.5:9b, Groq gpt-oss-20b / gpt-oss-20b on Bedrock $0.07/$0.30 (sec.), Haiku 4.5 Batch $0.50/$2.50|Schema-validated; escalate on failure; local keeps prospect data on the PC.|
|Classification: ICP fit, vertical, reply intent|T1 small plus rules|Same / Haiku 4.5 for replies|Rules catch unsubscribe and bounce first; golden-set gate before any swap.|
|Entity-match tie-breaks|T1, ambiguous pairs only|Same|Rare, cheap.|
|Website-audit observations (Offer A)|T2 mid|Claude CLI on subscription / Sonnet 5.5 $2/$10 (Bedrock credits UNVERIFIED)|Each observation cites an evidence id.|
|Opportunity inference, offer choice, dossier|T3 strong|Claude CLI `-p --json-schema` / Sonnet 5.5; Opus 5.5 $4/$20 on escalation|Quality matters; volume is tens per day.|
|Email draft|T3 strong|Same|Evidence-only context; deterministic lint after.|
|Claim verification, LLM judge|T2/T3, different model family|Bedrock gpt-oss-120b, GLM-5 $1/$3.20 / Haiku 4.5|Self-preference bias (Panickssery 2024, Zheng 2023); exact-quote check in code first.|
|Web research, orchestration|Agent|Claude Code CLI `-p` / Agent SDK with API key; web search $10/1,000|See next section.|
|Mechanical agent chores|T1 agent|Hermes with Bedrock Qwen3-Coder-Next $0.50/$1.20 or GLM-5 / same|Cheap tool loop; no schema guarantee, so validate outputs.|

## Claude CLI: now vs later

**Now (founder's PC, own scripts):** call the unmodified `claude -p` as a subprocess behind the `llm.reason` and `llm.write` contracts: `--output-format json --json-schema <schema> --max-turns N --allowedTools <list> --permission-mode dontAsk --mcp-config f.json --strict-mcp-config`. Fit: low-volume dossier synthesis, drafting, exploratory research with MCP tools. Log `total_cost_usd` and `session_id`. Authenticate with `claude setup-token` (research/01). Never pass the subscription token to LiteLLM or SDK code.

**Later (VPS, volume, other users):** an API-key worker on the Messages API (Batch, caching, structured outputs, web search) or the Agent SDK with an API key. Docs say `--bare` (API key only) will become the `-p` default, so plan the swap now.

**Never via CLI:** bulk extraction or classification.

## Papers verified

All four found on arxiv.org via search, 2026-10-03.
- Gao, Yen, Yu, Chen 2023, "Enabling Large Language Models to Generate Text with Citations", arXiv 2305.14627: ALCE, a benchmark where systems retrieve evidence and answer with citations.
- Min et al. 2023, FActScore, arXiv 2305.14251 (EMNLP 2023 per repo): scores the share of atomic facts a reliable source supports; automated estimator under 2% error.
- Zheng et al. 2023, "Judging LLM-as-a-Judge with MT-Bench and Chatbot Arena", arXiv 2306.05685: strong judges agree with humans over 80%; judge biases catalogued.
- Panickssery, Bowman, Feng 2024, arXiv 2404.13076: GPT-3.5, GPT-4 and Llama 2 favour their own summaries, so the verifier must not share the writer's family.

## Unverified

- Not read (egress blocked): AWS Free Tier and Bedrock pricing pages, mantle docs, OpenRouter docs and live prices.
- Free Tier credit size came from AWS What's New 2025-07-15 via search. Whether credits pay for Claude tokens is UNVERIFIED; Activate credits are officially accepted for third-party models, a different programme. Claude Platform on AWS is postpaid via Marketplace, so credits probably do not apply [mine].
- Bedrock prices for Qwen3-Coder-Next, GLM-5, gpt-oss; Bedrock batch for Claude 5.x; both Hermes models on mantle.
- OpenRouter live prices; BYOK after the reported Aug-2026 change.
- Whether `claude -p` on a subscription has its own limit or credit pool; when `--bare` becomes default.
- Hermes version the installer pulls.
- Clay, Zoho and Hunter MCP costs and auth; Apollo "any plan" claim; Supabase MCP licence; GitHub stars (API blocked).
- Paper venues; ALCE's ~50% headline and Zheng's "self-enhancement" wording (from memory, not re-read).
