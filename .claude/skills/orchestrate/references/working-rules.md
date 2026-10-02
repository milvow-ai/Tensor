# Working rules

Read before planning. Sections: Thinking discipline · Token economics · Spawn mechanics · Anti-patterns.

## Thinking discipline

- **Goal before task.** Ask what the work is for; the literal request is often one step of it.
- **Reproduce, then fix.** See the bug or gap yourself on the real target (live site, real data) before changing anything. A fix for a symptom you have not seen is a guess.
- **Measure, don't assume.** Numbers from the real system beat a model's expectations. A weak worker recommended "best practices" that would have broken the product; a 40-line probe showed the actual cause.
- **Compare two states** to find a difference: before/after, server/client, direct path/click path, old output/new output. Differential checks find bugs that inspection misses.
- **Fix at the source.** If a build step causes it, fix the build step, not its output.
- **Smallest correct change, measured scope.** Find where a thing actually occurs before editing it. Never apply a blanket change to a short or common string.
- **Patches are exact-once and stop loudly on a mismatch; tools are idempotent.**
- **Order matters:** generate, dry-run, apply, verify. Never regenerate inputs after applying them.
- **Done means verified where it counts:** on the deployed target, as the user experiences it. A command exiting 0 is not proof, and silence from a tool is not success.
- **Taste is the user's.** For copy, brand voice, visual design and naming, show two or three concrete samples and get a yes before doing the whole thing.
- **Say what you verified and what you did not.** Never round an unverified claim up to a fact: vendor numbers, a worker's report, your own expectation.

## Token economics

- Every Claude session and subagent draws on one quota. Parallel workers buy speed, not budget.
- Cost is context length × turns. Keep the lead small: no bulk reading, no long logs, no file dumps. Use counts, file lists and 300-character slices; send output to files and print the tail.
- A fresh worker with a one-page brief costs a fraction of the same job in a heavy window, and its transcript never enters yours.
- Scripts re-run for free. Anything checked twice becomes a script.
- Batch independent tool calls in one message. Run long jobs in the background and wait for the notification.
- Write scripts to files (shell quoting mangles one-liners) and cap every command's output (`| tail`, `| cut -c1-200`).
- No worker for a two-minute task: the brief costs more than the work.

## Spawn mechanics

- Spawn with the Agent tool, `run_in_background: true`, several in one message when they are independent. Override the model per spawn. Agent definitions in `~/.claude/agents/` pin model and tools; a full id such as `claude-opus-4-8` works there.
- Parallelize research, read-only audits and independent files. Serialize anything that edits the same files: one writer at a time, named in the Prompt.
- Resume, don't respawn: send the revision to the same worker (SendMessage with its id) so it keeps its context.
- Workers die when the session limit hits. Their files are the state: every Prompt names its output file, and the lead commits a local checkpoint whenever a round verifies.
- Headless `claude -p` needs the CLI logged in; if it is not, use in-session subagents.
- The Workflow tool (scripted fan-out) only when the user asks for a workflow.

## Anti-patterns (each one happened)

- Accepting a worker's "done", or a tool's silence, without checking.
- Shipping flat writing because it passed a linter.
- Rewriting a whole site's copy before the user approved a sample.
- Blanket edits to short strings; regenerating inputs after applying them.
- Trusting a weak model's audit over a measurement.
- Guessing an ambiguous replacement target and building it twice.
- Ten debugging calls in the lead where one precise Prompt would have done.
- Polling; shell one-liners with nested quotes; reading a preview pane that is hidden.
