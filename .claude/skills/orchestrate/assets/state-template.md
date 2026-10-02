# Orchestrate state

## Current state
<2–3 lines: the effort, the phase, the next action>

## Active workers
| Worker | Agent | Model | Owns | Status | Output file |
|---|---|---|---|---|---|
| Build 1 | worker-build | sonnet | <files> | Not started | <path> |
| Check | worker-check | sonnet | nothing (read-only) | Not started | this file |

Status: Not started · Running · Done · Blocked · Failed. After a restart, a Running worker whose output file is missing is Failed.

## Contract
<interfaces, locked facts, never-dos, files no worker may touch>

## Decisions log
- YYYY-MM-DD: <decision> — <reason>

---

## Build 1 Prompt
**Goal:** <one paragraph: what this is for and why it matters>
**Read first:** <briefs/CONTEXT.md and the named files only>
**Already known:** <so nothing is redone>
**Steps:** <numbered, exact>
**Owns:** <the files this worker may edit>
**Must not touch:** <files>
**Done when:** <the check that passes>
**Report:** 15 lines or fewer — changed paths, check results, open issues. No diffs, no dumps.

## Build 1
**Status:**
**Files changed:**
**Decisions:**
**Open issues:**

### Revise
- [ ] 1. <numbered, specific item written by the lead>

---

## Check Prompt
**Verify:** <what the workers produced, with paths>
**Target:** <the real thing to verify against: deployed URL, real data, repo>
**Checks (in order):**
1. <command or step> — pass means <exact condition>
**Compare:** <two states to diff, if any>
**Report:** per check Pass/Fail; on Fail the exact error (file, line, message), most serious first. 15 lines or fewer.

## Check — Round 1
**Result:**

---

<!-- Copy the Prompt + results pair for each extra worker. A Write worker's Prompt uses the same fields, with Output file: one file, and Round 0 = samples only for taste work. -->
