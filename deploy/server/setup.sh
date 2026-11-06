#!/usr/bin/env bash
# Jarvis server setup for Ubuntu Server 24.04. Safe to re-run: every step checks before it changes.
#
#   sudo ./deploy/server/setup.sh            # firewall allows SSH over Tailscale only
#   sudo ALLOW_LAN_SSH=1 ./deploy/server/setup.sh   # also allow SSH from private LAN ranges
#
# After it finishes, as the jarvis user: `sudo -iu jarvis claude setup-token` (Claude Pro login).
set -euo pipefail

REPO_URL="${REPO_URL:-https://github.com/Soham-1827/Jarvis.git}"
# Deploy a tag or commit you trust (e.g. REPO_REF=v0.1.0). Protect `main` on GitHub if deploying it.
REPO_REF="${REPO_REF:-main}"
UV_VERSION="${UV_VERSION:-0.12.11}"
APP_DIR=/opt/jarvis
DATA_DIR=/var/lib/jarvis
WORK_ROOT=/srv/jarvis
ETC_DIR=/etc/jarvis
UNIT=jarvis-brain.service
PORT=8765

log() { printf '\n==> %s\n' "$*"; }

require_root() {
  if [[ $EUID -ne 0 ]]; then
    echo "run with sudo" >&2
    exit 1
  fi
}

install_packages() {
  log "packages"
  apt-get update -qq
  DEBIAN_FRONTEND=noninteractive apt-get install -y -qq \
    git curl ca-certificates ufw sqlite3 ffmpeg unattended-upgrades >/dev/null
  dpkg-reconfigure -f noninteractive unattended-upgrades
}

create_users() {
  log "users"
  id jarvis &>/dev/null || useradd --create-home --shell /bin/bash jarvis
  # Workers never log in; Phase 2 runs them under their own systemd unit as this user.
  id jarvis-worker &>/dev/null || useradd --create-home --shell /usr/sbin/nologin jarvis-worker
}

create_dirs() {
  log "directories"
  install -d -o jarvis -g jarvis -m 0750 "$APP_DIR" "$DATA_DIR"
  install -d -o jarvis -g jarvis-worker -m 2770 "$WORK_ROOT" "$WORK_ROOT/repos" "$WORK_ROOT/work"
  install -d -o root -g jarvis -m 0750 "$ETC_DIR"
  if [[ ! -f "$ETC_DIR/env" ]]; then
    install -o root -g jarvis -m 0640 /dev/null "$ETC_DIR/env"
    cat >"$ETC_DIR/env" <<EOF
JARVIS_DATA_DIR=$DATA_DIR
JARVIS_BIND_HOSTS=["127.0.0.1"]
JARVIS_PORT=$PORT
JARVIS_LOG_LEVEL=INFO
EOF
  fi
}

install_tailscale() {
  log "tailscale"
  if ! command -v tailscale &>/dev/null; then
    # Official apt repo with its signing key, instead of piping an install script into a root shell.
    local codename
    codename="$(. /etc/os-release && echo "$VERSION_CODENAME")"
    curl -fsSL "https://pkgs.tailscale.com/stable/ubuntu/${codename}.noarmor.gpg" \
      -o /usr/share/keyrings/tailscale-archive-keyring.gpg
    curl -fsSL "https://pkgs.tailscale.com/stable/ubuntu/${codename}.tailscale-keyring.list" \
      -o /etc/apt/sources.list.d/tailscale.list
    apt-get update -qq
    DEBIAN_FRONTEND=noninteractive apt-get install -y -qq tailscale >/dev/null
  fi
  if ! tailscale status &>/dev/null; then
    echo "Tailscale is installed but not logged in. Run: sudo tailscale up --ssh=false"
  fi
}

configure_firewall() {
  log "firewall"
  ufw default deny incoming >/dev/null
  ufw default allow outgoing >/dev/null
  # Only SSH from the tailnet. `tailscale serve` reaches the brain over loopback, so no 443 rule.
  ufw allow in on tailscale0 to any port 22 proto tcp >/dev/null
  if [[ "${ALLOW_LAN_SSH:-0}" == "1" ]]; then
    for net in 10.0.0.0/8 172.16.0.0/12 192.168.0.0/16; do
      ufw allow from "$net" to any port 22 proto tcp >/dev/null
    done
  fi
  ufw --force enable >/dev/null
  ufw status verbose
}

disable_sleep() {
  log "power: never sleep"
  systemctl mask --quiet sleep.target suspend.target hibernate.target hybrid-sleep.target
}

install_user_tools() {
  log "uv + Claude Code for the jarvis user (official installers over HTTPS, uv pinned)"
  # Accepted risk: these vendor installers are trusted over HTTPS. They run as the unprivileged
  # jarvis user, never as root.
  sudo -iu jarvis bash -c "command -v uv >/dev/null || curl -LsSf https://astral.sh/uv/${UV_VERSION}/install.sh | sh"
  sudo -iu jarvis bash -c 'command -v claude >/dev/null || curl -fsSL https://claude.ai/install.sh | bash'
}

deploy_app() {
  log "app code"
  if [[ -d "$APP_DIR/.git" ]]; then
    sudo -u jarvis git -C "$APP_DIR" fetch --quiet --tags origin
  else
    sudo -u jarvis git clone --quiet "$REPO_URL" "$APP_DIR"
  fi
  # Resolve the ref (branch, tag or commit) and pin the checkout to it.
  local commit
  commit="$(sudo -u jarvis git -C "$APP_DIR" rev-parse --verify "origin/$REPO_REF" 2>/dev/null \
    || sudo -u jarvis git -C "$APP_DIR" rev-parse --verify "$REPO_REF^{commit}")"
  sudo -u jarvis git -C "$APP_DIR" checkout --quiet --detach "$commit"
  echo "deployed $REPO_REF at $commit"
  sudo -iu jarvis bash -c 'cd "$1" && ~/.local/bin/uv sync --extra brain --no-dev --locked' _ "$APP_DIR"
}

install_service() {
  log "systemd unit"
  install -m 0644 "$APP_DIR/deploy/server/$UNIT" "/etc/systemd/system/$UNIT"
  systemctl daemon-reload
  systemctl enable --quiet "$UNIT"
  systemctl restart "$UNIT"
  sleep 2
  curl -fsS "http://127.0.0.1:$PORT/health" && echo
}

expose_on_tailnet() {
  log "tailscale serve (HTTPS on the tailnet only)"
  if tailscale status &>/dev/null; then
    tailscale serve --bg --https=443 "http://127.0.0.1:$PORT"
    tailscale serve status
  else
    echo "skipped: log in to Tailscale first, then re-run this script"
  fi
}

main() {
  require_root
  install_packages
  create_users
  create_dirs
  install_tailscale
  configure_firewall
  disable_sleep
  install_user_tools
  deploy_app
  install_service
  expose_on_tailnet
  log "done. Next: sudo -iu jarvis claude setup-token"
}

main "$@"
