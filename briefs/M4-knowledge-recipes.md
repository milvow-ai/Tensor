# Brief M4 — knowledge layer, evidence capture, recipes, Hermes farm-agent, policy

Rules: follow `briefs/CONTEXT.md` §6. Touch only the files under "Owns". Never git add/commit/push. Never read `.env`.
Live network allowed only for the acceptance run against ONE public website (default `https://example.com` — robots-permitted, tiny) and Hermes via its Bifrost VK (≤ $0.05).

## Goal
Answers the Farm returns become durable, cited knowledge (HANDOFF §4.1, §4.5, §4.7): facts with provenance and freshness, website evidence
(screenshot, HTML, sha256, robots decision) stored under `FARM_DATA_DIR/evidence/` with a small thumbnail uploaded to Supabase Storage when configured,
multi-step **recipes** as durable workflows, and Hermes as a policy-gated agent resource whose only tools are Farm MCP tools.

## Read first
`briefs/CONTEXT.md`, `HANDOFF.md` §4.1–§4.2, §4.5, §4.7, `docs/HERMES-INTEGRATION.md` §14–15, `research/r5-ui-and-web.md` (evidence rules: cite only
screenshot-confirmed facts and dated CrUX p75; never Lighthouse scores), `library/crawl4ai` docs (screenshot + HTML + markdown capture), `farm/durable/*`,
`farm/resources/router.py`, `farm/executors/cli_agent/hermes.py` (if present).

## Owns
`farm/knowledge/{entities,facts,evidence,cache_policy}.py` (canonical keys via tldextract (registrable domain) for companies and normalised email/LinkedIn
URL for people; fact upsert keeps history, freshness per attribute TTL table; lookup returns fresh → use, stale → policy (refresh or serve-stale with
flag), missing → acquire), `farm/executors/browser.py` (Crawl4AI/Playwright capture: robots.txt check first and record the decision; full-page screenshot
PNG + HTML + markdown; sha256; tool version; thumbnail 480 px WebP; Supabase Storage upload of the thumbnail only when `SUPABASE_URL` +
`SUPABASE_SERVICE_ROLE_KEY` are set, path recorded in `evidence.thumb_path`), `farm/recipes/{analyze_website,find_person,find_email,research_company}.py`
as durable workflows composed of Farm capability calls (each step through the router, so pools/quotas/fallbacks apply; facts written with evidence ids),
`farm/policy/rules.py` (allow/deny per caller × capability × connection scope; agent callers (Hermes) can never call admin tools, never send email, never
exceed per-run unit/cost caps; every block → `policy_block` event), `farm/executors/agent.py` (Hermes `agent_task` resource: profile `farm-agent`,
Farm MCP over stdio as its only toolset (`mcp_servers.farm` with `sampling: false`), submit tools `submit_findings(...)` validated by Pydantic, prose ignored,
iteration/time caps), gateway tools `analyze_website`, `research_company`, `agent_task`, `get_facts(entity)`, `get_evidence(id)`, migration if needed for
fact history, Playwright browsers install note in docs, tests `tests/test_knowledge_*.py`, `tests/test_recipes_*.py`, `tests/test_policy.py`,
`tests/test_accept_m4.py`.

## Acceptance
- Offline: recipes with mocked capabilities write facts with evidence ids; second identical `research_company` = cache hit, cost 0, no provider calls; stale fact triggers refresh per policy; a Hermes-caller attempt to call an admin tool or exceed its cap is blocked and logged.
- Live (marked `live`, run once, report): `analyze_website(https://example.com)` → evidence row with existing PNG/HTML files, matching sha256, robots decision; `agent_task` via Hermes farm-agent submits a validated result using only Farm tools (its config dump shows no terminal/web/browser toolsets).

## Done when
`scripts/check.ps1` → `RESULT: all passed`; live acceptance run reported.

## Reply (≤ 15 lines)
Files changed; test count; check tail; live run results + cost; deviations.
