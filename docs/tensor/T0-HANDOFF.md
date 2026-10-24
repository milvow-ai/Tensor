# T0 handoff — Tensor design session + Farm server/SaaS discussion (written 2026-10-24, end of session)

Read this first if you are a new Claude session picking up Tensor design or the Farm server question.
Brief: `briefs/T0-tensor-design-session.md`. State: `.orchestrate/state.md` → "Tensor T0". No code is built in T0.

## 1. Tensor design (T0) — where it stands
| Step | Status |
|---|---|
| 1 Discovery | Round 1 answered: owner accepted all 8 proposals (D1–D8 in `docs/tensor/DISCOVERY.md`). **Round 2 asked, not answered:** deal size + sales cycle, real proof (are Cigna/Aetna case studies real?), footer postal address, who signs, who operates (hours/week), first client in sight |
| 2 Research | R7 outreach domains + mailboxes started first (warm-up gates the first send). Brief `briefs/T0-R7-outreach-mailboxes.md`. Farm job `662c3769-6059-4e2e-b6fd-e9f766da3bb1` (agy-01, Gemini 3.8 Flash) **succeeded, result NOT yet collected or reviewed**: run `ai_result` on it, verify load-bearing prices, save as `research/r7-outreach-mailboxes.md`, then write the priced proposal (buying is the owner's call). Other capabilities (TENSOR-FOUNDATION §B) not started |
| 3–6 | Requirements, Figma file "Tensor", architecture / Lego map / quality plan, build plan: not started |

Key decisions (owner, 2026-10-24): AI workflow automation offer only; US B2B, 20–500 staff, founder/COO/ops; 2 outreach
domains × 2 mailboxes ramping to ~25/day; ≤ $150/mo pilot budget; CAN-SPAM only, EU/UK/Canada excluded; every email
human-approved until Gate T; client Tensors done-for-you first; founder voice 60–110 words. Tensor runs on the server
24/7 as an app on the Farm (app token), never on a laptop.

Kickoff findings: funding-engine `main` mailbox = Gmail App Password over SMTP/IMAP (`gmail_oauth` is only an enum);
read funding-engine `main` with `git show` (its checkout is on another branch; never switch it). Reusable code: send
guard (15 checks, ramped cap), draft builder (code checks every figure, second-model audit), inbox sorter, follow-ups.

## 2. Farm server / SaaS discussion with the owner (2026-10-24)
- **Owner direction (binding):** Harness Farm is a SaaS — "small version of the big vision", same product at 10 or 10M
  users. Never advise it as a personal/team box (memory `saas-not-personal`). Don't push purchase buttons while the
  owner is thinking.
- **Server prices verified 2026-10-24** (details + sources: notes folder file `T0-server-prices-2026-10-24.md`, below):
  Hetzner raised prices 15 Jun 2026 (CPX32 4c/8 GB now €35.49; CPX42 €69.49). Cost-optimized CX33 (4c/8 GB) €8.99 and
  CX43 (8c/16 GB) €16.49 incl. IPv4 were shown unavailable; Arm CAX11/CAX21 (€6.49/€10.99 incl. IPv4) were orderable in
  Nuremberg on the owner's screen. Hetzner bills hourly with a monthly cap, postpaid; a powered-off server still bills
  (pause = snapshot + delete). Claude Code, Codex, Antigravity and Hermes all have Arm64 Linux builds. Farm needs
  Python 3.12 → Ubuntu 24.04 image.
- **Database is Supabase in Mumbai** (per the server runbook) → servers must sit in the same region as the DB; the DB is
  tiny (35 tables, 2,325 rows), so moving it is cheap now. Lead's suggestion: EU/Hetzner cell 1 + Supabase Frankfurt;
  India alternative DigitalOcean Bangalore ($48 for 8 GB per the runbook). **Not decided; nothing bought.**
- **Lead's honest assessment:** the Farm has strong design docs but no validated requirements (no SLO numbers, customer
  regions, scale targets, data residency, compliance level, pricing); multi-AI workers proven end to end only for Claude
  (S0; live pool Gemini 1/9, Codex 0/3); vault + sandboxes (F2/F3) not built. Recommended: hold the production
  region/server decision until a Farm requirements baseline (F0) is approved — the owner had not answered yet.
- **Owner idea: small server + team PCs as compute.** Lead's view: good if "control plane + secrets on the server, thin
  runners on PCs" (self-hosted-runner pattern); runners talk only to the server API, never the DB; labelled jobs
  (24/7 + client data + Tensor sending = server only); member's own AI login on their own PC; NDA/IP assignment for
  people. Needs F2 + F3 + A1 + a runner API/package (~2–3 days extra). Full write-up: notes file
  `T0-hybrid-runners-idea.md`. Owner had not decided whether F0 should adopt it.

## 3. Sessions and cards
- **Fix Farm account status and model lists** (`local_9666ae0d-…`, branch `claude/vigorous-mahavira-f03a6f`): 5 defects
  + 1 (stuck `needs_login`) fixed in `fa50d01`, `e167270`; full suite green (2,467). **Waiting for the lead's review and
  merge, a Farm restart (owner OK), then `farm ai test` on claude-01 and codex-01.** Not pushed.
- **Host the Farm on a server** (`local_36fe568e-…`, branch `claude/youthful-hellman-cb7583`): SERVER1 Linux kit
  (systemd, Tailscale, encrypted backups, watchdog) proven in WSL; runbook `docs/RUNBOOK-SERVER.md` on that branch;
  waiting for the owner's server decision. Not yet briefed on the SaaS / cell-1 framing.
- **Proposed card, not started:** "Harness Farm requirements baseline (F0)" (`task_6cd6f870`, expires 2026-10-31).
- Shared notes folder (outside the repo):
  `C:\Users\Amaan\AppData\Local\Claude-Data\claude_pzs8sxrjxfjjc\side-session-notes\local_a4959196-dcce-4104-8cc7-3d38945b7010\`
  — `T0-farm-observations.md`, `T0-server-prices-2026-10-24.md`, `T0-hybrid-runners-idea.md`.

## 4. Next steps, in order
1. Get the owner's round-2 discovery answers → finish and get approval of `docs/tensor/DISCOVERY.md`.
2. Collect R7 (job above), verify, write the priced domain/mailbox proposal.
3. Owner decisions pending: start F0 or not; hybrid runners in F0 or server-only; production region/server (after F0).
4. Continue T0: research for the rest of §B → REQUIREMENTS → Figma "Tensor" → ARCHITECTURE / LEGO-MAP / QUALITY-PLAN →
   BUILD-PLAN. Push design docs only after each owner approval.
