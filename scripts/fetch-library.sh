#!/usr/bin/env bash
# Fetch the open-source reference library listed in research/library.tsv
# into ./library/<name> at the pinned commit. Idempotent: an existing checkout
# at the right commit is left alone. library/ is git-ignored; the manifest is
# the durable record.
#
# Manifest columns (tab-separated): name  url  commit  licence  purpose
set -euo pipefail
cd "$(dirname "$0")/.."
manifest=research/library.tsv
mkdir -p library

while IFS=$'\t' read -r name url commit licence purpose; do
  [[ -z "${name}" || "${name}" == \#* ]] && continue
  dest="library/${name}"
  if [[ -d "${dest}/.git" ]] && [[ "$(git -C "${dest}" rev-parse HEAD)" == "${commit}" ]]; then
    echo "ok    ${name} @ ${commit:0:10}"
    continue
  fi
  rm -rf "${dest}"
  git init -q "${dest}"
  git -C "${dest}" remote add origin "${url}"
  git -C "${dest}" fetch -q --depth 1 --filter=blob:limit=2m origin "${commit}"
  git -C "${dest}" checkout -q FETCH_HEAD
  echo "fetch ${name} @ ${commit:0:10}  (${licence})"
done < "${manifest}"
