# A3A-R1 — UI kit polish after the lead's design review (Round 0)

Rules: same as `briefs/A3A-ui-kit.md` — touch only the kit, gallery, format and token files you created, no `.env`, no git
commands, every command in the foreground.

Fix every item exactly:
1. **Numbers in a proportional font.** Big numbers and values (MetricCard value, BreakdownCard total, TaskCard cost,
   InsightsPanel values, MeterBar percentages, chart axis labels) use **Geist Sans semibold with `tabular-nums`**, like the
   owner's reference. Geist Mono is only for ids, code and technical names (e.g. `write_enrichment`, `claude-02`).
2. **Realistic, current sample data.** AIs and models: Claude Sonnet 5.5 / Opus 5.5, Codex, Gemini 3.8 Flash, DeepSeek V4.
   Account ids like `claude-02`, `agy-01`, `codex-01`, `hermes-01`. No "Claude 3.7", "DeepSeek V3", "Gemini 2.0",
   "agy-claude-01".
3. **AppSidebar at short heights:** the nav list scrolls inside its own area; the PlanCard stays pinned at the bottom and never
   covers nav items. Show the gallery demo at a realistic height (e.g. 720 px) with all items reachable.
4. **DayChip:** the "Selected" state label must fit inside the chip (no overflow) at every size; use sentence case labels in the
   standalone-states demo ("Default", "Selected", "Has items", "Badge", "Disabled").
5. **Gallery header copy:** title "Component gallery", subtitle "Every Harness Farm component in every state, light and dark."
   No internal jargon anywhere in the gallery.
6. **Locale demo:** label the card "Tokens consumed"; the en-US / en-IN toggle at the top changes its formatting (350,834 ↔
   3,50,834) — no "(en-IN demo)" text.

Then: `pnpm check`, `pnpm test`, `pnpm test:e2e` (regenerate the two gallery screenshots). Reply in 10 lines or fewer: items
1–6 done or not, test results, screenshot paths.

## Resume note
A previous run stopped early. The files listed by `git status` are its partial work: review them, keep what is right, fix what is not, finish the brief.

