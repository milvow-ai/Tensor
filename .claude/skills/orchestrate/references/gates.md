# Gates by kind of work

Read when writing a Check Prompt. Pick the block that matches the work, name every check in the Prompt, and say what "pass" means in exact terms.

## Code and config
Default order, using whatever the project uses:
1. Typecheck (`tsc --noEmit`, `mypy`, `pyright`)
2. Lint (`eslint .`, `ruff check .`)
3. Tests (`jest`, `pytest`, `go test ./...`)
4. Build (`npm run build`, `cargo build`), if applicable
5. Smoke tests for the features the state file lists

Add where they apply:
- A gate script that loads the real thing and fails on errors.
- A dry run before every apply.
- A differential check for anything with two render paths (server/client, direct/click path).
- Verification on the deployed target, only after the user has authorised the deploy.

## Visual work
Look at it in a real or headless browser, desktop and phone, before it ships. A hidden preview pane paints black and pauses media, so use headless. Never crop, restyle or re-lay-out beyond what was asked. worker-check saves screenshots to the output file path so the lead can review them.

## Writing
Mechanical checks only; voice and taste are the lead's review.
- Every number, name and claim traces to an input the Prompt supplied. List any that do not.
- No `[NEEDS INPUT]` markers left, unless the Prompt allows them.
- Length, headings and links match the Prompt.

## Research
- Primary sources with dates.
- Separate what a source says from what is inferred.
- Names and APIs checked against the registry or docs before they are written into rules or code.

## Performance and data
Measure on the real system before and after, like for like.

## Non-Claude output (Hermes)
Always checked against the real thing before use. The Check Prompt lists every file Hermes produced and the exact condition for pass.

## Check Prompt format
```
## Check Prompt
**Verify:** <paths the workers produced>
**Target:** <deployed URL, real data, repo>
**Checks (in order):**
1. <command or step> — pass means <exact condition>
**Compare:** <two states to diff, if any>
**Report:** per check Pass/Fail; on Fail the exact error (file, line, message), most serious first. 15 lines or fewer.
```

## Check round format
```
## Check — Round N
**Result:** PASS | FAIL
1. <check> — Pass
2. <check> — Fail: <file>:<line> <message>
```
