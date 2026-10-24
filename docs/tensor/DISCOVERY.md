# Tensor — Discovery (T0, step 1)

**Status (2026-10-24):** round 1 answered (all eight proposals accepted); round 2 (the items with no default) asked.
Owner: Amaan (Milvow). Lead: Claude (T0 design session).
Brief: `briefs/T0-tensor-design-session.md`. Nothing is built before the owner approves `docs/tensor/BUILD-PLAN.md`.

## Sources read (2026-10-24)
- `briefs/T0-tensor-design-session.md`; `docs/TENSOR-FOUNDATION.md` §A, §B, §G, "Decisions needed"; `docs/ARCHITECTURE.md`
  §1–2, §12, ADR 11; `research/00-audit.md`; the saved memories in `memory/mem0-import.jsonl`.
- funding-engine `main` at `e7a273e` (code only): `README.md`; `engine/engine/outreach/*` (module docstrings and
  signatures; `prompts.py` was not opened because it may hold client wording).
- Live Farm, `farm_guide` (2026-10-24): Clay 2/2 accounts usable; AI workers Claude 3/4, Gemini 4/9 (free tier), Hermes
  1/1, Codex 0/3; MCP pass-through clay, figma-bridge, langfuse, mem0, sentry and stitch usable; microsoft-learn 0/1.

## What we know going in (to re-validate with the owner)
| # | Fact | Source, date | Status |
|---|---|---|---|
| K1 | Tensor is Milvow's B2B client finder first; then personalised Tensors are sold to mid-size and large companies, US market first | owner, brief T0, 2026-10-24 | given |
| K2 | Quality bar: agency-grade, personalised, no false claims, every feature moves revenue | owner, brief T0, 2026-10-24 | given |
| K3 | Offers in the foundation: A website experience, B AI workflow automation | foundation §A, 2026-10-03 | re-validate: the brief now says "AI automation workflows" |
| K4 | Earlier market: US and Canada, small-to-mid B2B, default ICP 20–249 staff; pilot verticals 5412, 5242, 5416/5413, 4238/332 were judgements, not facts | foundation, 2026-10-03 | re-validate |
| K5 | The Cigna and Aetna case-study links on milvow.com redirect to the homepage; `/pricing` redirects to `/contact` | research/00, 2026-10-02 | owner to confirm which proof is real |
| K6 | milvow.com mail runs on Zoho (India DC); cold mail must never go from it, because a suspension would hit the agency's own mail | research/00 and r3, 2026-10-02/03 | carried over |
| K7 | New outreach domains and mailboxes need 2–4 weeks of warm-up; buying them is the owner's decision | brief T0, 2026-10-24 | raised in round 1 |
| K8 | Earlier budget was "about $0 until the first client" | memory import, 2026-10-03 | re-validate: the owner now pays for the accounts connected to the Farm |

## Findings at kickoff (engineering)
- **F1 — mailbox auth.** funding-engine `main` connects a mailbox with a **Gmail App Password over SMTP/IMAP**
  (AES-GCM, the ciphertext bound to address and role). `gmail_oauth` exists only as an allowed adapter value (enum and
  database check); the OAuth path is not built. Which one Tensor uses is decided in the architecture step.
- **F2 — branch.** The local funding-engine checkout is on `fix/daily-report-unfinished-runs`. T0 reads `main` with
  `git show` and never switches branches in that repo.
- **F3 — reusable code on `main`.** The send guard (15 named checks, all run, pure function; a daily cap that ramps
  and falls back to the start figure on bounces ≥ 3 % or any complaint; a test-mode allowlist that fails closed; a send
  window). The draft builder (the model writes only the words; code checks every figure against confirmed facts; a
  second model audits; drafts built on unconfirmed facts go to test addresses only). The inbox sorter (Message-ID
  threading, bounces and out-of-office replies recognised from headers in code, the next action decided by code). Follow-ups
  on day 4, 10 and 21, cancelled by any reply. Two human approvals (lead, then draft). The do-not-contact list, alerts,
  and the dashboard screens for drafts, the pipeline board and review.
- **F4 — scope change.** Foundation §A put multi-tenancy out of scope. Selling Tensors to clients brings it in: each
  client's pack (offers, proof, ICP, rules, voice, mailboxes) is tenant data (`docs/ARCHITECTURE.md` §12).
- **F5 — stale research.** `research/` is dated 2026-10-02/03. Prices and plan limits are refreshed in the research step.

## Round 1 — business frame (asked 2026-10-24)
The owner may answer "default" to accept the proposal in brackets.

1. **Goals.** What does success look like at day 90? What is a typical Milvow deal size and sales cycle?
   [≥ 4 qualified meetings a month by day 90; reply-rate and cost-per-lead targets fixed after a 200-email baseline]
2. **Offers and proof.** Does Tensor sell AI workflow automation only, or the website offer too? Which proof is real and
   citable (named clients, numbers)? Are the Cigna and Aetna case studies real (K5)?
   [workflow automation only; an email may cite only proof the owner has confirmed in writing]
3. **Prospects.** Industries, company size, buyer titles; any vertical where Milvow already has wins?
   [US; 20–500 staff; founder, COO or head of operations; 2–3 verticals run as measured experiments]
4. **Volume and money.** Sends per day at steady state? Monthly pilot ceiling on top of current subscriptions? May the
   lead bring a priced proposal for outreach domains and mailboxes now (warm-up gates the first send)?
   [2 domains × 2 mailboxes, ramping to about 25 per mailbox per day; ≤ $150 a month]
5. **Compliance.** Which postal address goes in the footer?
   [US B2B only, under CAN-SPAM: honest headers, postal address, one-click unsubscribe, suppression kept forever;
   EU, UK and Canadian prospects excluded until their rules are designed]
6. **Operators.** Who runs Tensor day to day and approves emails, and for how many hours a week?
   [a person approves every email until 100 sends with 0 false claims (Gate T); then approval by exception]
7. **Product.** Are client Tensors a done-for-you service (Milvow runs each client's Tensor) or software the client's
   team runs? Is a first client in sight, and in which industry? [done-for-you first; client logins later]
8. **Voice.** Who signs the emails (name, title), and in what tone?
   [founder voice, plain, 60–110 words, one question, no links in the first email]

## Answers

### Round 1 (owner, 2026-10-24): all eight proposals accepted
The right-hand column is the lead's working reading; the owner confirms it when approving this document.

| # | Decision | Working reading |
|---|---|---|
| D1 | Goal: ≥ 4 qualified meetings a month by day 90; reply-rate and cost-per-lead targets are fixed after a 200-email baseline | Day 90 counts from the first real send. A qualified meeting is a held call with a decision maker at an ICP company who confirms a workflow problem Milvow solves |
| D2 | Tensor sells AI workflow automation only; an email cites only proof the owner has confirmed in writing | The website offer (A) and its audit module leave the first build |
| D3 | Prospects: US; 20–500 staff; founder, COO or head of operations; 2–3 verticals run as measured experiments | Verticals are chosen in the research step from evidence, then confirmed by the owner |
| D4 | Sending: 2 outreach domains × 2 mailboxes, ramping to about 25 per mailbox a day; ≤ $150 a month on top of current subscriptions; a priced domain and mailbox proposal comes first | Ceiling ≈ 100 sends a day; the real daily volume is set by the approval time available (round 2, Q5). Buying stays the owner's decision |
| D5 | Compliance: US B2B only, under CAN-SPAM (honest headers, postal address, one-click unsubscribe, suppression kept forever); EU, UK and Canadian prospects excluded until their rules are designed | The country rule is a hard check in the send guard, not only a search filter |
| D6 | A person approves every email until 100 sends with 0 false claims (Gate T); then approval by exception | Replies Tensor sends are approved the same way in that period |
| D7 | Client Tensors are a done-for-you service first (Milvow runs them); client logins come later | Each client is a tenant with its own client pack; Milvow is client #1 |
| D8 | Voice: founder voice, plain, 60–110 words, one question, no links in the first email | Follow-ups obey the same rules; a style lint enforces them in code |

### Round 2 — the items with no default (asked 2026-10-24)
1. **Deal size and sales cycle.** Typical value of a first engagement (USD) and the time from first call to signature.
   This sets the cost a meeting may carry and how long follow-ups run.
2. **Real proof.** Each proof point Tensor may cite: the client (named or anonymous), what Milvow built, the measured
   result, and whether the client allows naming. Are the Cigna and Aetna case studies real?
3. **Postal address** for the footer. CAN-SPAM accepts a street address, a USPS PO box or a private mailbox registered
   with a commercial mail receiving agency.
4. **Who signs.** The name and title on the emails.
5. **Who operates.** Who reviews dossiers and approves emails day to day, and how many hours a week.
6. **First client.** Is a first Tensor client in sight? If so, its industry and size.

## Decisions
| Date | Decision | By |
|---|---|---|
| 2026-10-24 | Tensor is designed in its own session from brief T0 (discovery → research → requirements → Figma flows → architecture → build plan); no code before BUILD-PLAN approval | owner |
| 2026-10-24 | Round 1: all eight proposals accepted (D1–D8) | owner |
| 2026-10-24 | The outreach domain and mailbox research runs first, because warm-up gates the first send (D4) | owner (D4), lead (order) |
