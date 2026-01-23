#!/usr/bin/env bash
set -euo pipefail

# Log to cloud-init location (same as AWS EC2)
LOG_FILE="/var/log/cloud-init-output.log"
touch "$LOG_FILE" 2>/dev/null || LOG_FILE="$HOME/.ohcommodore/init.log"
mkdir -p "$(dirname "$LOG_FILE")"

# Redirect all output to log file (quiet mode)
exec >> "$LOG_FILE" 2>&1

log() { echo "[$(date '+%Y-%m-%d %H:%M:%S')] $*"; }
die() { echo "[$(date '+%Y-%m-%d %H:%M:%S')] FAILED: $*"; exit 1; }

log "Starting ohcommodore init..."

TARGET_REPO="${TARGET_REPO:-}"
DOTFILES_PATH="${DOTFILES_PATH:-}"  # Local dotfiles path (highest priority)
DOTFILES_URL="${DOTFILES_URL:-https://github.com/smithclay/ohcommodore}"  # Remote dotfiles repo

# GH_TOKEN only required if cloning a repo
if [[ -n "$TARGET_REPO" && -z "${GH_TOKEN:-}" ]]; then
  die "GH_TOKEN env var is required when TARGET_REPO is set"
fi

export DEBIAN_FRONTEND=noninteractive

need_cmd() { command -v "$1" >/dev/null 2>&1; }

# Portable base64 decode (works on both Linux and macOS)
base64_decode() {
  openssl base64 -d -A
}

log "Installing GitHub CLI (gh) if missing..."
if ! need_cmd gh; then
  die "expected gh cli to be installed on base image"
fi

# Only authenticate gh if we have a token
if [[ -n "${GH_TOKEN:-}" ]]; then
  log "Persisting gh credentials for future sessions..."
  _token="$GH_TOKEN"
  # Must unset GH_TOKEN before gh auth login, otherwise gh uses env var instead of storing credentials
  unset GH_TOKEN
  printf '%s\n' "$_token" | gh auth login --hostname github.com --with-token >/dev/null 2>&1
  unset _token

  log "Configuring git via gh..."
  gh auth setup-git --hostname github.com >/dev/null 2>&1
else
  # Clear any stale GH_TOKEN from environment
  unset GH_TOKEN 2>/dev/null || true
fi

if [[ -n "$TARGET_REPO" ]]; then
  log "Verifying token can access repo: $TARGET_REPO"
  gh repo view "$TARGET_REPO" --json nameWithOwner >/dev/null

  REPO_NAME="$(basename "$TARGET_REPO")"
  DEST="$HOME/$REPO_NAME"

  if [[ -d "$DEST/.git" ]]; then
    log "Repo already exists at $DEST — fetching latest"
    (cd "$DEST" && git fetch --all --prune)
  else
    log "Cloning $TARGET_REPO into $DEST"
    gh repo clone "$TARGET_REPO" "$DEST"
  fi
fi

log "Installing zellij (idempotent)..."
if ! need_cmd zellij; then
  tmpdir="$(mktemp -d)"
  trap 'rm -rf "$tmpdir"' EXIT
  curl -fL -o "$tmpdir/zellij.tgz" \
    https://github.com/zellij-org/zellij/releases/latest/download/zellij-x86_64-unknown-linux-musl.tar.gz
  tar -xzf "$tmpdir/zellij.tgz" -C "$tmpdir"
  sudo mv "$tmpdir/zellij" /usr/local/bin/
  sudo chmod +x /usr/local/bin/zellij
else
  log "zellij already installed — skipping"
fi

log "Installing chezmoi and applying dotfiles (idempotent)..."
if ! need_cmd chezmoi; then
  sh -c "$(curl -fsLS get.chezmoi.io)" -- -b "$HOME/.local/bin"
  export PATH="$HOME/.local/bin:$PATH"
fi

if [[ -n "$DOTFILES_PATH" ]]; then
  # Use local dotfiles (scp'd by ohcommodore)
  if [[ -d "$DOTFILES_PATH" ]]; then
    log "Applying dotfiles from local path: $DOTFILES_PATH"
    chezmoi apply --source "$DOTFILES_PATH"
  else
    log "Warning: DOTFILES_PATH set but directory not found: $DOTFILES_PATH"
  fi
elif [[ -d "$HOME/.local/share/chezmoi" ]]; then
  log "chezmoi repo already initialized — applying latest"
  chezmoi apply
elif [[ -n "$DOTFILES_URL" ]]; then
  # Clone repo and apply from /dotfiles subdirectory
  log "Cloning dotfiles from: $DOTFILES_URL"
  tmpdir=$(mktemp -d)
  if git clone --depth 1 "$DOTFILES_URL" "$tmpdir/repo" 2>/dev/null; then
    if [[ -d "$tmpdir/repo/dotfiles" ]]; then
      log "Applying dotfiles from repo subdirectory"
      chezmoi apply --source "$tmpdir/repo/dotfiles"
    elif [[ -f "$tmpdir/repo/.chezmoiroot" ]] || [[ -d "$tmpdir/repo/home" ]]; then
      # Standard chezmoi repo structure
      log "Applying dotfiles from standard chezmoi repo"
      chezmoi init --apply "$DOTFILES_URL" || log "Warning: Failed to apply dotfiles"
    else
      log "Warning: No dotfiles/ subdirectory or standard chezmoi structure found in $DOTFILES_URL"
    fi
    rm -rf "$tmpdir"
  else
    log "Warning: Failed to clone dotfiles from $DOTFILES_URL"
  fi
else
  log "No dotfiles configured — skipping chezmoi setup"
fi

# ──────────────────────────────────────────────────────────
# ship infrastructure (inbox + scheduler)
# ──────────────────────────────────────────────────────────


# ──────────────────────────────────────────────────────────
# NATS infrastructure (commodore only)
# ──────────────────────────────────────────────────────────

if [[ "${ROLE:-}" == "commodore" ]]; then
  log "Installing nats-server..."
  if ! need_cmd nats-server; then
    curl -fsSL https://github.com/nats-io/nats-server/releases/download/v2.12.3/nats-server-v2.12.3-linux-amd64.tar.gz | tar xz -C /tmp
    sudo mv /tmp/nats-server-*/nats-server /usr/local/bin/
    sudo chmod +x /usr/local/bin/nats-server
    rm -rf /tmp/nats-server-*
  fi

  log "Installing nats CLI..."
  if ! need_cmd nats; then
    curl -fsSL -o /tmp/nats-cli.zip https://github.com/nats-io/natscli/releases/download/v0.3.0/nats-0.3.0-linux-amd64.zip
    unzip -o /tmp/nats-cli.zip -d /tmp
    sudo mv /tmp/nats-0.3.0-linux-amd64/nats /usr/local/bin/
    sudo chmod +x /usr/local/bin/nats
    rm -rf /tmp/nats-cli.zip /tmp/nats-*
  fi

  log "Creating nats-server systemd service..."
  sudo tee /etc/systemd/system/nats-server.service > /dev/null << 'NATS_SERVICE'
[Unit]
Description=NATS Server
After=network.target

[Service]
ExecStart=/usr/local/bin/nats-server -a 127.0.0.1 -p 4222
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
NATS_SERVICE

  log "Starting nats-server..."
  sudo systemctl daemon-reload
  sudo systemctl enable --now nats-server
fi

log "Setting up SSH keys..."
mkdir -p ~/.ssh
chmod 700 ~/.ssh

# Check if pre-generated keys were passed via env vars (base64 encoded)
if [[ -n "${SHIP_SSH_PRIVKEY_B64:-}" && -n "${SHIP_SSH_PUBKEY_B64:-}" ]]; then
  log "Using pre-generated SSH keys from env vars..."
  echo "$SHIP_SSH_PRIVKEY_B64" | base64_decode > ~/.ssh/id_ed25519
  echo "$SHIP_SSH_PUBKEY_B64" | base64_decode > ~/.ssh/id_ed25519.pub
  chmod 600 ~/.ssh/id_ed25519
  chmod 644 ~/.ssh/id_ed25519.pub
  # Clear sensitive env vars immediately after use
  unset SHIP_SSH_PRIVKEY_B64 SHIP_SSH_PUBKEY_B64
  log "SSH keys installed from env vars"
elif [[ ! -f ~/.ssh/id_ed25519 ]]; then
  log "Generating SSH key..."
  ssh-keygen -t ed25519 -N "" -f ~/.ssh/id_ed25519 -q
  log "SSH key generated"

  # Only register with exe.dev if we generated the key ourselves
  log "Adding exe.dev to known_hosts..."
  ssh-keyscan -H exe.dev >> ~/.ssh/known_hosts 2>/dev/null || true

  log "Registering SSH key with exe.dev..."
  ssh exe.dev ssh-key add "$(cat ~/.ssh/id_ed25519.pub)" || log "Warning: Could not add SSH key (may already exist)"
else
  log "SSH key already exists"
fi

# Ensure exe.dev and exe.xyz hosts are in known_hosts
log "Adding exe.dev to known_hosts..."
ssh-keyscan -H exe.dev >> ~/.ssh/known_hosts 2>/dev/null || true

# Add SSH config to auto-accept exe.xyz hosts (all exe.dev VMs)
log "Configuring SSH for exe.xyz hosts..."
if ! grep -qF '*.exe.xyz' ~/.ssh/config 2>/dev/null; then
  cat >> ~/.ssh/config << 'SSHCONFIG'
Host *.exe.xyz
  StrictHostKeyChecking accept-new
  UserKnownHostsFile ~/.ssh/known_hosts
  User exedev
SSHCONFIG
  chmod 600 ~/.ssh/config
fi

# ──────────────────────────────────────────────────────────
# NATS infrastructure (captain/ships only)
# ──────────────────────────────────────────────────────────

if [[ "${ROLE:-}" == "captain" && -n "${FLAGSHIP_SSH_DEST:-}" ]]; then
  log "Installing autossh for NATS tunnel..."
  if ! need_cmd autossh; then
    sudo apt-get update -qq
    sudo apt-get install -y autossh
  fi

  log "Installing nats CLI..."
  if ! need_cmd nats; then
    curl -fsSL -o /tmp/nats-cli.zip https://github.com/nats-io/natscli/releases/download/v0.3.0/nats-0.3.0-linux-amd64.zip
    unzip -o /tmp/nats-cli.zip -d /tmp
    sudo mv /tmp/nats-0.3.0-linux-amd64/nats /usr/local/bin/
    sudo chmod +x /usr/local/bin/nats
    rm -rf /tmp/nats-cli.zip /tmp/nats-*
  fi

  log "Creating autossh tunnel service for NATS..."
  sudo tee /etc/systemd/system/ohcom-tunnel.service > /dev/null << TUNNEL_EOF
[Unit]
Description=ohcommodore NATS tunnel to flagship
After=network.target

[Service]
User=exedev
ExecStart=/usr/bin/autossh -M 0 -N -o "ServerAliveInterval 30" -o "ServerAliveCountMax 3" -L 4222:localhost:4222 ${FLAGSHIP_SSH_DEST}
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
TUNNEL_EOF

  log "Starting NATS tunnel..."
  sudo systemctl daemon-reload
  sudo systemctl enable --now ohcom-tunnel
fi

log "Setting up ship directories..."
mkdir -p ~/.local/bin ~/.ohcommodore/ns/default

log "Creating systemd user service for scheduler..."
mkdir -p ~/.config/systemd/user

cat > ~/.config/systemd/user/ship-scheduler.service << 'SERVICE_EOF'
[Unit]
Description=ohcommodore inbox scheduler
After=default.target

[Service]
ExecStart=%h/.local/bin/ohcommodore _scheduler
Restart=always
RestartSec=5
Environment="PATH=/usr/local/bin:/usr/bin:/bin"

[Install]
WantedBy=default.target
SERVICE_EOF

# Try to enable systemd user service (may fail in some VM environments)
if systemctl --user daemon-reload 2>/dev/null && \
   systemctl --user enable ship-scheduler 2>/dev/null && \
   systemctl --user start ship-scheduler 2>/dev/null; then
  log "ship scheduler running and enabled on boot"
else
  log "Warning: systemd user services not available; run ship-scheduler manually if needed"
fi

# Scrub sensitive env vars from common persistence locations
log "Cleaning up sensitive environment variables..."
for f in ~/.bashrc ~/.zshrc ~/.profile ~/.bash_profile ~/.ssh/environment ~/.pam_environment; do
  if [[ -f "$f" ]]; then
    sed -i '/GH_TOKEN\|SHIP_SSH_PRIVKEY_B64\|SHIP_SSH_PUBKEY_B64/d' "$f" 2>/dev/null || true
  fi
done
# Clear from systemd user environment
systemctl --user unset-environment GH_TOKEN SHIP_SSH_PRIVKEY_B64 SHIP_SSH_PUBKEY_B64 2>/dev/null || true

# Configure Claude Code to skip onboarding
log "Configuring Claude Code..."
echo '{"hasCompletedOnboarding":true}' > ~/.claude.json

log "Init complete. Repo: ${TARGET_REPO:-none}"
