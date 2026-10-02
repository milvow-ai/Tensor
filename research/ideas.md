# Candidate ideas for the design phase

These are candidates, not decisions. Phase 2 accepts, changes or drops each one.

Unless a line cites a source, the reasoning is mine: *This is my reasoning, not a published framework.* Citations marked "verify" come from memory and must be checked before Phase 2 relies on them.

## How the system is shaped

1. **Playbooks as config.**
   - **Idea:** each offer × ICP is one YAML file holding sources, hard gates, scoring signals, LLM rubric, segments, message angles, sequence, send caps and approval mode. Adding a market means adding a file; the dashboard edits it through a JSON-Schema form. This is what makes Tensor "SaaS-like" without multi-tenancy.
   - **Falsifier:** if two real playbooks need code changes to differ, the config surface is wrong.
2. **Code decides the flow; models are typed functions.**
   - **Idea:** deterministic code owns state, ordering, rate limits and every side effect (sending, writing). LLMs are called only for judgement and return JSON against a schema. Anything that can be a rule is a rule.
   - **Grounding:** Anthropic's "Building effective agents" (2024) separates predefined-path workflows from model-directed agents. A send-capable pipeline belongs on the workflow side.
3. **Postgres is the memory and the queue.**
   - **Idea:** each lead is a row in a state machine, and each stage claims work with `FOR UPDATE SKIP LOCKED` (or pgmq/procrastinate, research/04). An append-only `events` table is the audit trail. A `learnings` table holds what Claude proposes and a human accepts.
   - **Status:** a tested sketch is in `sketches/data-model-v0.sql`. It is not a decision.
4. **Budget meter.**
   - **Idea:** every free-tier quota (Groq RPD, Hunter credits, Clay credits, Firecrawl pages) is a counted resource. A call reserves quota atomically and falls through to the next provider when one runs out, so $0 holds even on a bad day. The sketch has `consume_quota()`.
5. **Model routing by stakes, not by habit.**
   - **Bulk and low-stakes** (page → facts, industry tagging): local Ollama, Groq or Cloudflare.
   - **Judgement and writing** (borderline qualification, the personalised lines, reply triage, reply drafts): Claude. That means the unmodified `claude` CLI on the subscription until there is API budget (terms in research/01).
   - **Critic:** a different model family from the writer. LLM judges tend to favour their own outputs; Zheng et al. 2023 (MT-Bench, "self-enhancement bias") and Panickssery et al. 2024. *Verify both.*
   - **Never route reply contents to a provider that trains on free-tier data.**
6. **Thin router over OpenAI-compatible endpoints instead of LiteLLM** (research/01): fewer dependencies, and it avoids the March-2026 supply-chain incident class. The trade-off is that new providers have to be added by hand.

## Message quality (anti-slop)

7. **Grounded personalisation.**
   - **Idea:** every personalised sentence cites a stored fact (source URL plus verbatim quote). No fact means no email: the lead is downgraded, never padded.
8. **Constrained generation.**
   - **Idea:** a human approves 2–3 skeletons per angle once. Per lead, Claude only picks the angle and writes 1–2 grounded sentences. Less slop, fewer tokens, consistent voice.
9. **Deterministic lint before any human sees a draft.**
   - **Checks:** word count, banned phrases, one CTA, and no links or tracking in the first touch (pending research/03).
   - **Proof points:** no claim of past results unless it is listed in the playbook's `proof_points`. This blocks invented case studies.
10. **Approval dial.**
    - **Idea:** the first N drafts per playbook are approved by hand, then a sample, then automatic, all set per playbook. Approvals happen in the dashboard (Telegram buttons optional).

## Who to target and what to offer

11. **Signal-based targeting.**
    - **Idea:** a public job post for a role whose work is automatable (data entry, ops coordinator, lead researcher, admin) is evidence of both the pain and the budget. Sources: ATS JSON and JobSpy (research/02).
    - **Scoring:** O*NET task data plus published LLM-exposure scores (research/08) say how automatable that role's tasks are, rather than guessing.
12. **Offers that are costly to fake.**
    - **Idea:** a small working demo or teardown built on the prospect's own public data.
    - **Grounding:** Spence 1973 (signalling) and Akerlof 1970 (when buyers can't judge quality, cheap claims are discounted). A new agency without proof is exactly that case.
    - **Self-proof:** Tensor itself is a proof point for the "prospecting & enrichment" workflow on Milvow's site.
13. **White-label agency playbook.**
    - **Idea:** Milvow's FAQ already offers white-label work to agencies. One partner can mean many projects.
    - **Risk (my judgement, unverified):** white-label offers from India are a heavily pitched category, so the angle must be specific.
14. **Week 1 does not wait for the full engine.**
    - **Idea:** the first small batch runs semi-manually through the connected tools (Clay MCP + Claude + a human pressing send) while the engine is built. This follows Paul Graham, "Do Things that Don't Scale" (2013).

## Safety, compliance, memory

15. **Contact discipline.**
    - **Idea:** one live conversation per company across all playbooks (a database constraint), and a global suppression list stored by hash.
    - **Cooldowns:** a hashed contact ledger keeps cool-down rules working after non-responders' personal data is purged. GDPR Art. 5(1)(c) data minimisation.
16. **Claude can read everything Tensor knows.** The Supabase MCP gives Claude SQL access. "What replied today?" becomes a question, and a weekly Routine can write the learnings review.
17. **Channels Tensor must not automate become tasks.** LinkedIn touches and calls go into a human task queue, because LinkedIn automation breaches its ToS (research/02).

## Known traps (from the research so far)

- Scraping Google Maps or LinkedIn (ToS; hiQ outcome).
- Reusing Apollo data for clients (ToS).
- A subscription token inside LiteLLM or SDK code (Anthropic terms).
- Gemini free tier for anything containing replies (trains on data).
- Public GitHub repo logs with lead data.
- Port-25 SMTP verification on hosts that block it.
- Supabase project pausing after a week idle.
