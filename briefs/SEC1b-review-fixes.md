# SEC1b — fix the SEC1 security-review findings

Worktree already contains the SEC1 changes (uncommitted). Keep them; apply these fixes on top. Shared contract: `briefs/CONTEXT.md`. Run commands in the foreground and report once.

## Fixes (all required)
1. **MAJOR — remove the test hook from production.** `farm/executors/cli_agent/base.py` (~lines 459-465, 579-586): delete the `is_legacy_fake_test` branch (auth_ref `cli:test` → edit mode without `allow_edit`). Update the fake tests (`tests/test_cli_agent_fakes.py` etc.) to set `allow_edit=True` + an explicit edit root instead.
2. **MAJOR — argv flag injection via session_id / model.** codex.py (~62, positional after `resume`), claude.py (~50), hermes.py (~78), agy.py (`--conversation`): validate `session_id` and `model` in all four drivers against `^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$` → BAD_REQUEST otherwise (do it once in base.py, called by every driver). For codex also insert `--` before positionals where the CLI accepts it. Test: `session_id="--dangerously-bypass-approvals-and-sandbox"` and `model="-c x=y"` are rejected for every driver, and no process is spawned.
3. **MINOR — doctor env leak.** `farm/control/doctor.py:~449` `subprocess.run([bin_path, "--version"])` → pass `env=build_child_env()`. Test that the env passed has no secret names.
4. **MINOR — redaction gaps** in `farm/secrets.py` (`_DB_URL_PW`, `register_env_secrets`): match `^[a-z][a-z0-9+.-]*://[^:/\s]*:([^@\s]+)@` (covers `postgresql+psycopg://`, `redis://`, `https://u:p@`); register both the raw and `urllib.parse.unquote()`d password. Keep the min-length floor but register any value of a secret-named env var (`*KEY*`, `*SECRET*`, `*TOKEN*`, `*PASSWORD*`, `*_VK`) at ≥ 4 chars as today; add tests for each URL form + percent-encoded password.
5. **MINOR — allow_edit / edit_roots strictness** (base.py ~586, 610-617): require `allow_edit is True` (strict bool; pydantic `StrictBool` in the model is fine); reject empty, `"."` or relative `edit_roots` entries at validation time. Tests: `"false"` string → answer mode or validation error; `""`/`"."` roots rejected.
6. **MINOR — argv length cap** (agy.py ~51, hermes.py ~47): measure `len(subprocess.list2cmdline(argv))` against 32000 instead of raw prompt chars → BAD_REQUEST. Test with a 20k prompt of `"` characters.
7. **MINOR — token-store data dir** (`farm/executors/mcp/client.py` ~41-52): derive the containment root and `resolve_token_store` from the same data_dir (one source of truth); test with FARM_DATA_DIR differing from the default.

Also add tests for edit-root traversal the review found uncovered: prefix (`D:\root` vs `D:\root2`), case variation, `..`.

## Done when
`powershell -File scripts/check.ps1` → `RESULT: all passed`. Reply ≤ 15 lines: per fix the file + test name, check tail, deviations.

## Resume note
The uncommitted files in `git status` are the finished SEC1 work (gate green). Keep them and apply only the fixes above.
