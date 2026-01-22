#!/usr/bin/env bash
set -euo pipefail

log() { echo "==> $*"; }
die() { echo "ERROR: $*" >&2; exit 1; }

: "${GH_TOKEN:?GH_TOKEN env var is required (classic PAT or fine-grained PAT).}"

TARGET_REPO="${TARGET_REPO:-}"
DOTFILES_PATH="${DOTFILES_PATH:-}"  # Local dotfiles path (highest priority)
DOTFILES_URL="${DOTFILES_URL:-https://github.com/smithclay/ohcommodore}"  # Remote dotfiles repo

export DEBIAN_FRONTEND=noninteractive

need_cmd() { command -v "$1" >/dev/null 2>&1; }

# Portable base64 decode (works on both Linux and macOS)
base64_decode() {
  openssl base64 -d -A
}

sudo_apt_install() {
  # Usage: sudo_apt_install pkg1 pkg2 ...
  sudo apt-get install -y "$@"
}

log "Ensuring base deps exist..."
# Keep this minimal but enough for everything below.
sudo_apt_install zsh

log "Installing GitHub CLI (gh) if missing..."
if ! need_cmd gh; then
  die "expected gh cli to be installed on base image"
fi

log "Persisting gh credentials for future sessions..."
_token="$GH_TOKEN"
unset GH_TOKEN
printf '%s\n' "$_token" | gh auth login --hostname github.com --with-token >/dev/null
unset _token

log "Configuring git via gh..."
gh auth setup-git --hostname github.com >/dev/null

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

log "Installing Oh My Zsh (idempotent, non-interactive)..."
export RUNZSH=no
export CHSH=yes
export KEEP_ZSHRC=no
touch "$HOME/.zshrc"

if [[ -d "$HOME/.oh-my-zsh" ]]; then
  log "Oh My Zsh already installed — skipping"
else
  sh -c "$(curl -fsSL https://raw.githubusercontent.com/ohmyzsh/ohmyzsh/master/tools/install.sh)" "" --unattended
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

log "Installing DuckDB CLI..."
if ! need_cmd duckdb; then
  curl -fL -o /tmp/duckdb.zip \
    https://github.com/duckdb/duckdb/releases/latest/download/duckdb_cli-linux-amd64.zip
  unzip -o /tmp/duckdb.zip -d /tmp
  sudo mv /tmp/duckdb /usr/local/bin/
  sudo chmod +x /usr/local/bin/duckdb
  rm /tmp/duckdb.zip
else
  log "DuckDB already installed — skipping"
fi

log "Installing nq (job queue utility)..."
if ! need_cmd nq; then
  git clone --depth 1 https://github.com/leahneukirchen/nq /tmp/nq || die "Failed to clone nq repository"
  cd /tmp/nq || die "Failed to cd to nq directory"
  make || die "Failed to build nq"
  sudo make install PREFIX=/usr/local || die "Failed to install nq"
  rm -rf /tmp/nq
else
  log "nq already installed — skipping"
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

log "Setting up ship directories..."
mkdir -p ~/.local/bin
mkdir -p ~/.ohcommodore/ns/default

log "Initializing DuckDB SSH secrets..."
duckdb ~/.ohcommodore/ns/default/data.duckdb "
  INSTALL sshfs FROM community;
  LOAD sshfs;

  -- Configure SSH key for SSHFS connections
  CREATE PERSISTENT SECRET IF NOT EXISTS sshfs_key (
    TYPE SSH,
    KEY_PATH '$HOME/.ssh/id_ed25519'
  );
"

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

log "Setting default login shell to zsh..."
ZSH_PATH="$(command -v zsh || true)"
[[ -n "$ZSH_PATH" ]] || die "zsh not found even after install; PATH=$PATH"
if [[ "${SHELL:-}" != "$ZSH_PATH" ]]; then
  # Try user-level chsh first
  if command -v chsh >/dev/null 2>&1; then
    if chsh -s "$ZSH_PATH" "$USER" 2>/dev/null; then
      log "Default shell changed via chsh to $ZSH_PATH"
    else
      log "chsh failed (common on headless VMs); trying sudo usermod..."
      sudo usermod -s "$ZSH_PATH" "$USER"
      log "Default shell changed via usermod to $ZSH_PATH"
    fi
  else
    sudo usermod -s "$ZSH_PATH" "$USER"
    log "Default shell changed via usermod to $ZSH_PATH"
  fi
else
  log "Shell already set to zsh: $ZSH_PATH"
fi

log "Done."
log "Repo location: ${TARGET_REPO:+$HOME/$(basename "$TARGET_REPO")}"
