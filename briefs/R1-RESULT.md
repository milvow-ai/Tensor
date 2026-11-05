# R1 quota classification repair — verified source checkpoint

Implemented: ambiguous RESOURCE_EXHAUSTED and vague usage-limit text no longer imply hard exhaustion; transient overload is SERVER, HTTP429 throttling RATE_LIMITED, explicit exhausted quota/credits LIMIT_REACHED. Actual Codex ASCII/curly apostrophe usage-limit messages remain recognized. A later completed turn clears previous failure evidence; a subsequent failure cannot inherit obsolete quota text or undated stderr from before completion. Successful answer prose is never treated as quota evidence.

Codex resume switches now precede the resume subcommand and preserve read-only answer mode. The installed Windows codex-cli0.160.0 accepts the resulting parser shape (resume --help checked; no inference used). Timeout subprocess stdout is retained; a real local process probe returned partial stdout after a0.4s timeout. Higher-level durable failed-job partial output is still R2 work.

Independent reviewer quota_verify inspected actual code and reproduced three defects, plus stale stderr; all corrected before the final pure suite. Final Windows evidence: targeted Ruff passed; mypy --follow-imports=silent on base.py/classify.py/codex.py passed; pytest tests/test_q0b_classify.py tests/test_q0b_executors.py tests/test_cli_agent_codex.py -q -p no:cacheprovider ->46 passed in3.06s. Tests used isolated FARM_ENV_FILE/FARM_DATA_DIR/TEMP on D:. Database health tests pending until usage migration0022 is stable. Reviewer's session subsequently exhausted its own usage limit; no fabricated later review.

Workers: Farm codex01 job715a958d-ba7a-4c3b-ba5a-79ea142098b9 implemented initial patch. Farm codex04 jobde0efa7a-7978-4cd0-b21b-feb0976f7b58 reported succeeded at CLI level but explicitly could not edit due to worker permissions; no correction from that job is claimed. Lead applied small classifier correction; independent reviewer added regression cases.

Not deployed. No live account flags were reset or overridden. Remaining operations observation: existing Windows taskkill helper lacks a timeout; address in P3 supervision hardening.

## Follow-up verification — 2026-11-05

Farm Claude reviewer37b9da03-0e99-4f6d-aa59-7f7ed997b55a inspected actual code (could not execute probes). It confirmed the terminal-boundary correction and found bare429 could misclassify a source line number as throttling. Lead reproduced the explicit Claude wording miss, restored `Usage limit reached/exceeded`, and restricted numeric429 matching to HTTP/status/error channels. Regressions cover both findings.

Final expanded offline CLI suite: `python -m pytest tests/test_q0b_classify.py tests/test_q0b_executors.py tests/test_cli_agent_codex.py tests/test_cli_agent_claude.py tests/test_cli_agent_agy.py tests/test_cli_agent_hermes.py tests/test_cli_agent_base.py tests/test_cli_agent_fakes.py -q -p no:cacheprovider` ->80 passed in8.96s. Targeted classifier/test Ruff passed. Account-health suites in the separate usage run passed; combined run34passed1failed4skipped, where the failure was the new usage snapshot composite upsert, not account-health. No inference, live flags or deployment changed.
