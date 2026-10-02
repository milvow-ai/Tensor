# 01 — AI models: free tiers, Claude access, routing layer

Checked 2026-10-02 by a research worker. Market facts are dated, and UNVERIFIED marks anything the worker could not confirm on a primary page.

## What changed recently

- **Cerebras** no longer has a permanent free tier: there is a $5 trial for 30 days, and it needs a card.
- **GitHub Models was retired on 2026-07-30.**
- **The Gemini free tier is small and trains on your data.** Gemini is a backup option only.
- The dependable $0 options for bulk work are **Groq, OpenRouter `:free`, Cloudflare Workers AI and local Ollama**.

## Free tiers

| Provider | Free limits | Card | Trains on data | Source (seen 2026-10-02) |
|---|---|---|---|---|
| Gemini API (AI Studio) | Free models include Gemini 3.8/3.7/3.6/3.5 Flash, 3.5 and 3.1 Flash-Lite, 2.5 Flash(-Lite) and Gemma 4; 3.1 Pro Preview is not free. Per-model RPM and RPD are shown only in the AI Studio dashboard. UNVERIFIED forum report: 3.8 Flash ≈ 20 RPD, Flash-Lite ≈ 500 RPD. | No | **Yes** on the free tier | ai.google.dev/gemini-api/docs/pricing, …/rate-limits |
| Groq (Free plan) | gpt-oss-120b, gpt-oss-20b, gpt-oss-safeguard-20b and qwen/qwen3.8-27b: **30 RPM, 1K RPD, 8K TPM, 200K TPD**. Llama chat models are no longer on the free list. | No | No. Data may be kept 30 days unless zero data retention is on. | console.groq.com/docs/rate-limits, /docs/your-data |
| OpenRouter `:free` | 20 RPM. **50 RPD** if you have bought less than 10 credits in total, **1,000 RPD** after about $10 of one-time credits. | No | Varies by upstream provider; training providers can be switched off in settings | openrouter.ai/docs/api-reference/limits |
| Cerebras | Trial only: $5 for 30 days | **Yes** | UNVERIFIED | inference-docs.cerebras.ai |
| Mistral "Free mode" | Limits are not published (shown in the account). UNVERIFIED: ~1 req/s. | No | **Yes by default**, with an opt-out | docs.mistral.ai |
| GitHub Models | **Retired 2026-07-30** | – | – | docs.github.com/en/github-models |
| Cloudflare Workers AI | **10,000 Neurons/day**. For llama-3.1-8b that is about 390K input or 133K output tokens a day. | No (UNVERIFIED) | No | developers.cloudflare.com/workers-ai |
| NVIDIA build.nvidia.com | Prototyping only. Limits are unpublished; users report about 40 RPM. | UNVERIFIED | UNVERIFIED | NVIDIA forum |
| **Ollama (local)** | No quota; uses your own hardware. qwen3.5 comes in 0.8b/2b/4b/9b and gemma4 in e2b/e4b. JSON-schema output via `format`. Use v0.34.4+ (structured-output fix); latest is v0.35.0 (2026-09-28). | No | No | ollama.com/library, GitHub releases |

## Claude access (Anthropic docs, seen 2026-10-02)

- **Subscription token in CI.** This is officially documented. `anthropics/claude-code-action` takes `claude_code_oauth_token`, which you generate with `claude setup-token`. It works on Pro, Max, Team and Enterprise.
  - The token lasts 1 year.
  - It can only make model requests; claude.ai connectors are not available through it.
  - `CLAUDE_CODE_OAUTH_TOKEN` is documented "for CI pipelines and scripts where browser login isn't available".
  - `claude -p --bare` ignores this token and needs an API key.
- **Terms that limit how the subscription can be used** (code.claude.com/docs/en/legal-and-compliance; Consumer Terms effective 2025-10-08):
  - Subscription sign-in is for "ordinary use of Claude Code and other native Anthropic applications".
  - Anyone building products or services, including with the Agent SDK, should use API keys.
  - Routing Free, Pro or Max credentials through third-party tools is not allowed.
  - Usage limits assume "ordinary, individual usage".
  - **What this means for Tensor:** running the unmodified `claude` CLI with your own token in your own scripts is the documented route. Putting the token inside LiteLLM or your own SDK code is not allowed. Heavy 24/7 load is a risk under the ordinary-use clause.
  - Secondary source (The Register, 2026-02-20): third-party tools using subscription logins were blocked from 2026-01-09.
- **Routines** (code.claude.com/docs/en/routines, research preview):
  - A saved prompt, repo and connectors, run on Anthropic's cloud. Triggers are a schedule, the API or a GitHub event.
  - The schedule's **minimum interval is 1 hour**.
  - Rate limits: 100 scheduled runs per hour per account; 30 per hour per routine for run-now plus API; 100 API calls per hour per account.
  - Runs draw down subscription usage.
  - The default environment reaches only allowlisted domains, the same restriction this container has (research/00).
  - A routine turns itself off after 72 hours without a GitHub connection.
- **API prices**, per million tokens (platform.claude.com pricing), for when there is budget:

| Model | Standard in/out | Batch in/out (50% off) |
|---|---|---|
| Haiku 4.5 | $1 / $5 | $0.50 / $2.50 |
| Sonnet 5.5 | $2 / $10 | $1 / $5 |
| Sonnet 5 | $2 / $10 | $1 / $5 |
| Opus 5.5 | $4 / $20 | $2 / $10 |

Cache hits cost 0.1× the input price. Claude 4.7 and later count about 30% more tokens for the same text.

## Routing layer

- **LiteLLM (BerriAI/litellm):**
  - Actively maintained: v1.103.2 released 2026-10-01; about 60K stars; MIT on PyPI.
  - It routes to every live provider above.
  - **Supply-chain incident on 2026-03-24:** PyPI versions 1.82.7 and 1.82.8 were malicious for about 40 minutes. They stole credentials and planted a `litellm_init.pth` backdoor, delivered through a compromised CI action. Both versions are now removed. If you use it, pin versions with hashes.
  - Sources: docs.litellm.ai/blog/security-update-march-2026, wiz.io.
- **Alternative:** most of these providers (Groq, OpenRouter, Ollama, Gemini, Cloudflare) expose OpenAI-compatible endpoints. A thin router over the `openai` SDK with a per-provider `base_url`, plus the `anthropic` SDK, plus a `claude` CLI adapter, would cover them with far fewer dependencies. This is my inference, for the design phase to decide.

## Worker's suggestions for bulk tasks

These are the research worker's recommendations, not decisions:

1. Local Ollama (qwen3.5:4b/9b or gemma4:e4b) with JSON-schema output, for page-to-facts extraction. No quota, no training on data.
2. Groq gpt-oss-20b or qwen3.8-27b, at 1K RPD per model with no training.
3. Cloudflare Workers AI as overflow.
