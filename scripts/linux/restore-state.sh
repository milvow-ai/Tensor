#!/usr/bin/env bash
# Restore (or prove the restore of) a state backup written by backup-state.sh. Needs the owner's age private key:
#   restore-state.sh --drill --identity /dev/stdin            latest archive -> temporary dir, verified, removed
#   restore-state.sh ARCHIVE --identity KEY --to DIR          into an empty directory, verified
#   restore-state.sh ARCHIVE --identity KEY --into-live       replaces FARM_DATA_DIR (service stopped first;
#                                                             the current directory is kept beside it)
# --identity /dev/stdin reads the key from the terminal (paste it, then Ctrl-D): it never lands on the disk.
set -euo pipefail

FARM_DATA_DIR="${FARM_DATA_DIR:-/var/lib/harness-farm}"
BACKUP_DIR="${FARM_BACKUP_DIR:-/var/backups/harness-farm}"
FARM_USER="${FARM_USER:-farm}"
FARM_UNIT="${FARM_UNIT:-harness-farm}"
AGE="${AGE_BIN:-age}"

archive="" identity="" target="" mode=""
while [[ $# -gt 0 ]]; do
    case "$1" in
        --identity) identity="$2"; shift 2 ;;
        --to) target="$2"; mode=to; shift 2 ;;
        --into-live) mode=live; shift ;;
        --drill) mode=drill; shift ;;
        -h | --help) sed -n '2,8p' "$0"; exit 0 ;;
        *) archive="$1"; shift ;;
    esac
done
[[ -n "$mode" ]] || { echo "choose --drill, --to DIR or --into-live (see --help)" >&2; exit 2; }
[[ -n "$identity" ]] || { echo "--identity KEYFILE is required (the owner's age private key)" >&2; exit 2; }
if [[ -z "$archive" ]]; then
    archive=$(find "$BACKUP_DIR" -maxdepth 1 -name 'farm-state-*.tar.gz.age' | sort | tail -n 1)
    [[ -n "$archive" ]] || { echo "no backups in $BACKUP_DIR" >&2; exit 2; }
fi
[[ -f "$archive" ]] || { echo "no such archive: $archive" >&2; exit 2; }
umask 077

if [[ -f "$archive.sha256" ]]; then
    (cd "$(dirname "$archive")" && sha256sum --quiet -c "$(basename "$archive").sha256") ||
        { echo "checksum mismatch: $archive is damaged" >&2; exit 1; }
fi

work=$(mktemp -d)
trap 'rm -rf "$work"' EXIT
"$AGE" --decrypt --identity "$identity" "$archive" | gzip -dc >"$work/state.tar"

case "$mode" in
    drill) dest="$work/restored" ;;
    to) dest="$target" ;;
    live) dest="$FARM_DATA_DIR.restore-$$" ;;
esac
mkdir -p "$dest"
if [[ -n "$(ls -A "$dest")" ]]; then
    echo "$dest is not empty" >&2
    exit 2
fi
tar --extract --file "$work/state.tar" -C "$dest"
(cd "$dest" && sha256sum --quiet -c MANIFEST.sha256) || { echo "restored files do not match the manifest" >&2; exit 1; }
files=$(wc -l <"$dest/MANIFEST.sha256")
for expected in secrets.env registry.yaml ai tokens; do
    [[ -e "$dest/$expected" ]] || echo "note: $expected is not in this backup"
done

case "$mode" in
    drill) echo "restore drill passed: $archive ($files files verified)" ;;
    to) echo "restored $archive into $dest ($files files verified)" ;;
    live)
        if systemctl is-active --quiet "$FARM_UNIT"; then
            rm -rf "$dest"
            echo "stop $FARM_UNIT first (systemctl stop $FARM_UNIT)" >&2
            exit 2
        fi
        rm -f "$dest/MANIFEST.sha256"
        before="$FARM_DATA_DIR.before-restore-$(date -u +%Y%m%dT%H%M%SZ)"
        if [[ -d "$FARM_DATA_DIR" ]]; then mv "$FARM_DATA_DIR" "$before"; fi
        mv "$dest" "$FARM_DATA_DIR"
        chown -R "$FARM_USER:$FARM_USER" "$FARM_DATA_DIR"
        chmod 0700 "$FARM_DATA_DIR"
        echo "restored $archive into $FARM_DATA_DIR ($files files verified; the previous state is in $before)"
        ;;
esac
