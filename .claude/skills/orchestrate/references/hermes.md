# Hermes (open-weight models, on the user's API budget)

Read only if Hermes is in the plan. Sections: When to use · Command · The brief · Rules.

## When to use

- **Qwen** executes exact steps well and decides badly: it mis-scoped edits, stalled without output, and wrote generic advice that contradicted the system it was auditing. Give it steps and a check, never a judgment call.
- **GLM-5** reasons better but burns money on open-ended briefs. Use it for short, closed tasks: draft from an outline, fill a fixed template, review against a checklist. Never for exploration. Closed brief, word cap, one output file.
- **Free OpenRouter models** are heavily rate-limited: single calls only.
- Whatever a non-Claude model returns, worker-check verifies before it is used.

## Command

```
HERMES_HOME="<home>" OPENROUTER_API_KEY= OPENAI_API_KEY= ANTHROPIC_API_KEY= "$LOCALAPPDATA/hermes/bin/hermes.exe" -z "Read briefs/<x>.md and carry out every step exactly, using tools. Reply as it asks." --in . --provider custom:bedrock-mantle -m qwen.qwen3-coder-next -t terminal,file,code_execution,todo --yolo --usage-file hermes-work/<x>-usage.json > hermes-work/<x>-reply.txt 2>&1
```

- Ask which Hermes home to use. Each home has its own keys, and a key refreshed in one does not update another.
- `-m zai.glm-5` for the bounded-judgment cases.
- Run it in the background. If no artifact appears within about 15 minutes, kill it and do the job another way.
- About 13K tokens of overhead per call: one call with all the steps, not many small calls.

## The brief

`briefs/<x>.md` holds numbered exact steps, exact paths, one output file, a reply format, and what not to touch. On Windows tell it to use `node.exe`, to make HTTP calls from a script, and to run in the foreground.

`--yolo` skips approvals, so the lead's gates do not apply to Hermes unless the brief says so. Every brief starts with this block:

```
Rules for every step: touch only <listed files>. Never run git push, git commit,
a deploy, a delete, or any command that sends data off this machine. If a step
would need any of these, stop and write the reason to hermes-work/<x>-reply.txt.
```

## Rules

- It spends the user's money: ask for a budget, stay inside it, and report the spend from the usage file.
- Add a row for it in the Active workers table (Agent: hermes, Model: qwen.qwen3-coder-next or zai.glm-5, Output file: the one the brief names).
- A Check Prompt covers every file Hermes produced.
