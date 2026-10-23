#!/usr/bin/env bash
# Mirrors scripts/check.ps1 — zero-token check gate for Linux.
set -euo pipefail

root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$root"

failed=0

step() {
    local name="$1"
    shift
    local out
    if out=$("$@" 2>&1); then
        echo "PASS  $name"
    else
        echo "FAIL  $name"
        echo "$out" | tail -n 25
        ((failed++)) || true
    fi
}

# Environment switch: CHECK_ONLY_SECRETS=1 skips ruff/mypy/pytest
if [[ "${CHECK_ONLY_SECRETS:-0}" != "1" ]]; then
    step "ruff"   uv run ruff check farm tests
    step "mypy"   uv run mypy
    step "pytest" uv run pytest -q -p no:cacheprovider
fi

# Secret grep over tracked + untracked (non-ignored) files
pat='sk-or-v1-[0-9a-f]{20,}|sk-ant-[A-Za-z0-9_-]{20,}|ghp_[A-Za-z0-9]{30,}|AKIA[0-9A-Z]{16}|eyJhbGciOi[A-Za-z0-9_-]{30,}|sk-bf-(?!farm-p0-local-test)[A-Za-z0-9-]{16,}|postgres(ql)?://[^:\s]+:[^@\s]{6,}@'

# Self-test: pattern must match test string
# The fake key is built at runtime so no key-shaped literal sits in this file (the repo secret scan would flag it).
fake_key="sk-ant-$(printf 'a%.0s' {1..24})"
if ! printf '%s\n' "$fake_key" | grep -PIn "$pat" >/dev/null 2>&1; then
    echo "FAIL  secret-grep"
    echo "secret pattern self-test failed"
    echo "RESULT: 1 step(s) failed"
    exit 1
fi

# If git cannot list the files, the scan must fail loudly, never pass with nothing scanned.
if ! all_files=$(git ls-files --cached --others --exclude-standard 2>&1); then
    echo "FAIL  secret-grep"
    echo "git ls-files failed: $all_files"
    echo "RESULT: 1 step(s) failed"
    exit 1
fi
files=$(printf '%s\n' "$all_files" | grep -v -E '^(library|research/data)/' | grep -v -E '\.lock$' || true)
if [[ -z "$files" ]]; then
    echo "FAIL  secret-grep"
    echo "no files to scan"
    echo "RESULT: 1 step(s) failed"
    exit 1
fi

hits=""
count=0
while IFS= read -r f; do
    if [[ -f "$f" ]]; then
        match=$(grep -PIn "$pat" "$f" 2>&1) && true
        rc=$?
        if [[ $rc -eq 2 ]]; then
            echo "FAIL  secret-grep"
            echo "grep error on $f: $match"
            echo "RESULT: 1 step(s) failed"
            exit 1
        fi
        if [[ -n "$match" ]]; then
            while IFS= read -r line; do
                hits="${hits}${f}:${line}"$'\n'
            done <<< "$match"
        fi
    fi
    ((count++)) || true
done <<< "$files"

echo "      secret-grep scanned $count files"
if [[ -n "$hits" ]]; then
    echo "FAIL  secret-grep"
    echo -n "$hits"
    echo "RESULT: 1 step(s) failed"
    exit 1
else
    echo "PASS  secret-grep"
fi

if [[ $failed -gt 0 ]]; then
    echo "RESULT: $failed step(s) failed"
    exit 1
else
    echo "RESULT: all passed"
    exit 0
fi
