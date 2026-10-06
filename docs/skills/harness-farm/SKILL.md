---
name: harness-farm
description: Delegate research, bulk processing, or second opinions to Harness Farm AI workers, or use connected MCP servers. Use when tasks can be parallelized across Claude, Codex, Gemini, or Hermes, when you need specialized tools without cluttering primary context, or to obtain independent critiques.
---

# Harness Farm Skill

Harness Farm routes capability calls, manages AI worker pools, fronts external MCP servers, and tracks quotas, costs, and budgets.

## Step 1: Discover Capabilities Live
Always call `farm_guide()` first to inspect what accounts, models, and tools are available right now:
```python
guide = await call_tool("farm_guide", {"section": "all"})
```
Use `farm_guide(section="ai")` for AI workers or `farm_guide(section="mcp")` for external MCP tools.

## Key Recipes

### 1. Parallel Research Across Models
Launch tasks across distinct AI accounts concurrently:
```python
jobs = await call_tool("ai_start_many", {
    "jobs": [
        {"task": "Analyze architecture patterns for X", "ai": "claude"},
        {"task": "Search benchmarks and alternatives for X", "ai": "gemini"}
    ]
})
job_ids = [j["job_id"] for j in jobs["jobs"]]
await call_tool("ai_wait", {"job_ids": job_ids})
results = [await call_tool("ai_result", {"job_id": jid}) for jid in job_ids]
```

### 2. Second Opinion & Critique
Get a second model to review code or design proposals:
```python
draft = await call_tool("ask_ai", {"task": "Propose database schema for multi-tenant billing", "ai": "claude"})
critique = await call_tool("ask_ai", {
    "task": f"Critique this billing schema for edge cases and lock contention:\n{draft['result']['text']}",
    "ai": "codex"
})
```

### 3. Multi-turn Follow-up
Continue a conversation with the same worker session maintaining context:
```python
job = await call_tool("ai_start", {"task": "Audit codebase for insecure temp file usage", "ai": "claude"})
res = await call_tool("ai_result", {"job_id": job["job_id"]})
reply = await call_tool("ai_reply", {
    "conversation_id": job["conversation_id"],
    "message": "Draft fixes for the two high-severity issues found."
})
```

### 4. Credit-Safe MCP Tool Calls
Connect to MCP servers using `<server>__<tool>`. Check `farm_guide("mcp")` to see which tools are read-only:
```python
# Read-only tools consume zero credits
info = await call_tool("clay__get-credits-available", {})

# Pin a specific account when needed:
res = await call_tool("clay__search-companies", {
    "query": "acme",
    "_farm": {"account": "clay-01"}
})
```

## Cost and Credit Etiquette
- **Capacity**: Check `get_capacity()` before queuing large batches.
- **Budgets**: The Farm enforces hard stops on credit and USD budgets; failed jobs report remaining limits.
- **Free-First**: Preference is given to subscription and free quotas before pay-per-token workers.
- **Audit Trails**: Every call records a `run_id`. Call `get_run(run_id)` to review routing decisions and failover events.
