#!/usr/bin/env bash
# Encrypted snapshot of the server's own state (harness-farm-backup.timer, daily; update.sh before an update):
# the CLI logins (ai/), the MCP OAuth token stores (tokens/), the secrets file, the registry, Hermes and Bifrost
# config and AI job results. The database is on Supabase (`farm db backup` dumps it before a migration);
# binaries, caches, logs and workspaces are left out (install-server.sh reinstalls the binaries).
#
# The archive is encrypted with age to the public keys in FARM_BACKUP_RECIPIENTS: the server can write a backup
# but cannot read one; the owner keeps the private key off the server. MANIFEST.sha256 inside the archive lists
# every file, so restore-state.sh proves a restore byte for byte.
set -euo pipefail

FARM_DATA_DIR="${FARM_DATA_DIR:-/var/lib/harness-farm}"
BACKUP_DIR="${FARM_BACKUP_DIR:-/var/backups/harness-farm}"
RECIPIENTS="${FARM_BACKUP_RECIPIENTS:-/etc/harness-farm/backup-recipients.txt}"
KEEP_DAYS="${FARM_BACKUP_KEEP_DAYS:-14}"
AGE="${AGE_BIN:-age}"

[[ -s "$RECIPIENTS" ]] || { echo "no age recipients in $RECIPIENTS (docs/RUNBOOK-SERVER.md, Backups)" >&2; exit 2; }
[[ -d "$FARM_DATA_DIR" ]] || { echo "no state directory $FARM_DATA_DIR" >&2; exit 2; }
command -v "$AGE" >/dev/null || { echo "age is not installed (apt install age)" >&2; exit 2; }
umask 077
mkdir -p "$BACKUP_DIR"

stamp=$(date -u +%Y%m%dT%H%M%SZ)
out="$BACKUP_DIR/farm-state-$stamp.tar.gz.age"
work=$(mktemp -d)
trap 'rm -rf "$work"' EXIT

excludes=(
    --exclude=./logs --exclude=./workspaces --exclude=./backups --exclude=./heartbeats --exclude=./watchdog
    --exclude=./pg --exclude=./pgtest --exclude=./.cache --exclude=./.npm --exclude=./.local
    --exclude='./ai/*/.cache' --exclude='./ai/*/.npm' --exclude='./bifrost/logs.db*' --exclude='*.lock'
)
# Exit status 1 is "a file changed while it was read" (an OAuth refresh, say): the snapshot is still usable.
rc=0
tar --create --file "$work/state.tar" -C "$FARM_DATA_DIR" "${excludes[@]}" . || rc=$?
if [[ $rc -gt 1 ]]; then
    echo "tar failed (exit $rc)" >&2
    exit 1
fi

# The manifest is computed from the archive itself, so it matches what a restore gets back.
mkdir "$work/tree"
tar --extract --file "$work/state.tar" -C "$work/tree"
(cd "$work/tree" && find . -type f -print0 | sort -z | xargs -0 -r sha256sum) >"$work/MANIFEST.sha256"
tar --append --file "$work/state.tar" -C "$work" MANIFEST.sha256
files=$(wc -l <"$work/MANIFEST.sha256")

gzip -c "$work/state.tar" | "$AGE" --encrypt --recipients-file "$RECIPIENTS" --output "$out.part"
mv "$out.part" "$out"
(cd "$BACKUP_DIR" && sha256sum "$(basename "$out")" >"$(basename "$out").sha256")

find "$BACKUP_DIR" -maxdepth 1 -name 'farm-state-*.tar.gz.age*' -mtime +"$KEEP_DAYS" -delete
echo "state backup $out ($files files, $(du -h "$out" | cut -f1))"
