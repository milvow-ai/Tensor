#!/usr/bin/env bash
# Set up a Harness Farm server on Ubuntu 24.04 (run as root; safe to run again):
#   sudo bash install-server.sh --repo git@github.com:milvow-ai/Tensor.git --branch effort2/stage1-saas
# Installs the packages, the system user `farm` (home = state directory), the code in /opt/harness-farm with
# its venv, /etc/harness-farm/farm.env, the systemd units (enabled, not started), /usr/local/bin/farm, swap
# and the firewall (only SSH and the tailnet get in). It never reads or prints a secret, and starts nothing:
# the owner copies the secrets, logs the CLIs in and starts the Farm (docs/RUNBOOK-SERVER.md).
#   --skip-packages --no-firewall --no-swap --no-tailscale    leave those parts alone (tests, an existing host)
#   --http-port N                                            FARM_HTTP_PORT in a new farm.env (default 8787)
set -euo pipefail

APP=/opt/harness-farm
ETC=/etc/harness-farm
DATA=/var/lib/harness-farm
BACKUPS=/var/backups/harness-farm
FARM_USER=farm
UV_VERSION=0.12.17
repo="" branch="effort2/stage1-saas" packages=1 firewall=1 swap=1 tailscale=1 http_port=8787
while [[ $# -gt 0 ]]; do
    case "$1" in
        --repo) repo="$2"; shift 2 ;;
        --branch) branch="$2"; shift 2 ;;
        --skip-packages) packages=0; shift ;;
        --no-firewall) firewall=0; shift ;;
        --no-swap) swap=0; shift ;;
        --no-tailscale) tailscale=0; shift ;;
        --http-port) http_port="$2"; shift 2 ;;
        -h | --help) sed -n '2,10p' "$0"; exit 0 ;;
        *) echo "unknown option $1" >&2; exit 2 ;;
    esac
done
[[ $EUID -eq 0 ]] || { echo "run as root (sudo)" >&2; exit 2; }
step() { printf '\n== %s\n' "$1"; }

if [[ $packages -eq 1 ]]; then
    step "packages"
    export DEBIAN_FRONTEND=noninteractive
    apt-get update -qq
    apt-get install -y -qq git curl ca-certificates python3.12 age jq unzip rsync nodejs npm ufw postgresql-common
    # pg_dump must be at least the server's version (Supabase runs PostgreSQL 17; Ubuntu 24.04 ships 16):
    # the PostgreSQL project's own apt repository, set up by Ubuntu's postgresql-common helper.
    if ! command -v pg_dump >/dev/null || [[ "$(pg_dump --version | grep -oE '[0-9]+' | head -n1)" -lt 17 ]]; then
        /usr/share/postgresql-common/pgdg/apt.postgresql.org.sh -y
        apt-get install -y -qq postgresql-client-17
    fi
fi

if ! command -v uv >/dev/null || [[ "$(uv --version | awk '{print $2}')" != "$UV_VERSION" ]]; then
    step "uv $UV_VERSION"
    tmp=$(mktemp -d)
    triple=uv-$(uname -m)-unknown-linux-gnu
    curl -fsSL -o "$tmp/$triple.tar.gz" "https://github.com/astral-sh/uv/releases/download/$UV_VERSION/$triple.tar.gz"
    curl -fsSL -o "$tmp/$triple.tar.gz.sha256" "https://github.com/astral-sh/uv/releases/download/$UV_VERSION/$triple.tar.gz.sha256"
    (cd "$tmp" && sha256sum -c "$triple.tar.gz.sha256")
    tar -xzf "$tmp/$triple.tar.gz" -C "$tmp"
    install -m 0755 "$tmp/$triple/uv" "$tmp/$triple/uvx" /usr/local/bin/
    rm -rf "$tmp"
fi

step "user $FARM_USER"
if ! id "$FARM_USER" >/dev/null 2>&1; then
    useradd --system --user-group --home-dir "$DATA" --create-home --shell /bin/bash "$FARM_USER"
fi
install -d -m 0700 -o "$FARM_USER" -g "$FARM_USER" "$DATA" "$DATA/ai" "$DATA/tokens" "$DATA/workspaces" \
    "$DATA/logs" "$DATA/bifrost" "$DATA/.local" "$DATA/.local/bin"
install -d -m 0700 "$BACKUPS"

step "code $APP ($branch)"
if [[ ! -d "$APP/.git" ]]; then
    [[ -n "$repo" ]] || { echo "--repo is required for the first install" >&2; exit 2; }
    git clone --quiet --branch "$branch" "$repo" "$APP"
fi
git -C "$APP" config core.fileMode true
(cd "$APP" && UV_PYTHON_DOWNLOADS=never uv sync --frozen --no-dev --compile-bytecode --python /usr/bin/python3.12 --quiet)
chmod -R a+rX "$APP"

step "settings $ETC"
install -d -m 0755 "$ETC"
if [[ ! -f "$ETC/farm.env" ]]; then
    sed "s/^FARM_HTTP_PORT=.*/FARM_HTTP_PORT=$http_port/" "$APP/deploy/farm.env.example" >"$ETC/farm.env"
    chmod 0644 "$ETC/farm.env"
fi
if [[ ! -f "$DATA/registry.yaml" && -f "$APP/config/registry.server.yaml" ]]; then
    install -m 0600 -o "$FARM_USER" -g "$FARM_USER" "$APP/config/registry.server.yaml" "$DATA/registry.yaml"
fi
install -m 0755 "$APP/scripts/linux/farm" /usr/local/bin/farm

step "systemd units"
for unit in "$APP"/deploy/systemd/*.service "$APP"/deploy/systemd/*.timer; do
    install -m 0644 "$unit" /etc/systemd/system/
done
systemctl daemon-reload
systemctl enable --quiet harness-farm.service harness-farm-watchdog.timer harness-farm-backup.timer
# Bifrost only serves Hermes and the llm executor: enable it when its config is in place (RUNBOOK §5).

if [[ $swap -eq 1 ]] && ! swapon --show --noheadings | grep -q .; then
    step "swap 4 GB"
    fallocate -l 4G /swapfile
    chmod 0600 /swapfile
    mkswap -q /swapfile
    swapon /swapfile
    grep -q '^/swapfile ' /etc/fstab || echo '/swapfile none swap sw 0 0' >>/etc/fstab
fi

if [[ $tailscale -eq 1 ]] && ! command -v tailscale >/dev/null; then
    step "tailscale"
    curl -fsSL https://tailscale.com/install.sh | sh
fi

if [[ $firewall -eq 1 ]]; then
    step "firewall"
    ufw default deny incoming >/dev/null
    ufw default allow outgoing >/dev/null
    ufw allow OpenSSH >/dev/null   # keep until SSH over Tailscale works (RUNBOOK §3), then: ufw delete allow OpenSSH
    ufw allow in on tailscale0 >/dev/null
    ufw --force enable >/dev/null
fi

step "next (docs/RUNBOOK-SERVER.md)"
cat <<EOF
  1. tailscale up --ssh --hostname harness-farm      (opens a login URL; then: tailscale serve --bg $http_port)
  2. copy the secrets:   install -m 0600 -o farm -g farm .env $DATA/secrets.env
  3. registry:           $DATA/registry.yaml (farm registry for-server on the PC)
  4. log the CLIs in:    farm ai login <id> --local ; farm ai test <id> --local
  5. start:              systemctl start harness-farm   (after the PC's Farm is stopped: cutover)
EOF
