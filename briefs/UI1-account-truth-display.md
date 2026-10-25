# UI1 — Account rows tell the truth and stay readable (builder: claude-02)

Owner screenshot 2026-10-25 of `/integrations` (Farm → APIs) shows: full raw CLI error dumps (multi-line JSON, long Google
URLs) under accounts marked **active**; "Active accounts 100%" bars drawn **red**; "4 of 4 active" while one account's
last error is an expired OAuth session; every card says "Tools: 0". Worktree `D:\Harness Farm\wt-ui`, branch
`ui-accounts`. Console in `console/`. Kit components in `console/src/components/kit/*`. No commits, no `.env`.
Running commands may be blocked for you: then write correct, type-safe code; the lead runs checks and e2e.

## Fix
1. **Error text:** an account row shows its last error only when the account is not usable (status other than
   `active`, or `login_state` = needs_login / auth error kind, or circuit open / cooldown). Then show ONE line, max ~120
   characters, plain words first: map known patterns to a short label + action ("Quota reached — resets in 8h",
   "Sign-in expired — Sign in again", "Not eligible for Antigravity — verify the Google account", "Provider endpoint
   unavailable"); the full raw text goes behind a `kit/EvidencePopover` ("Details"). Never print raw JSON or URLs inline.
   Put the mapping in one pure function with a vitest unit test (`console/src/lib/farm/account-error.ts` + test).
2. **Status truth:** the row chip shows "Needs sign-in" (amber) when the last error is an auth/expired-session error even
   if `status` says active, and the card's "N of M accounts active" and the meter count only accounts that are usable by
   the same rule. Same pure function decides "usable".
3. **Meter colour:** usable share 100 % → green, 1–99 % → amber, 0 % → red (`kit/MeterBar` tone prop, or its existing API).
4. **"Tools: 0":** find where the card's tool count comes from (`console/src/lib/farm/*` loader + the SQL view it reads in
   `console/sql/` or the Supabase views). If the count is really unknown (catalogue not synced), show "—" with a tooltip
   "Catalogue not loaded yet", never a false 0. Report what the source is.
Files: `console/src/app/(main)/integrations/**`, `console/src/app/(main)/pools/tools/**`, `console/src/lib/farm/account-error.ts`
(+ test), the loader file for the tool count if a bug is there. Do NOT edit `console/src/app/(main)/pools/_components/**`
(another builder owns it right now); if the AI pool cards there need the same rule, export the pure function and say so.

## Report (≤ 12 lines)
Files changed, the error→label mapping table, where "Tools: 0" comes from and what you changed.
