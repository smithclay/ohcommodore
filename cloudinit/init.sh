#!/usr/bin/env bash
set -euo pipefail

log() { echo "==> $*"; }
die() { echo "ERROR: $*" >&2; exit 1; }

: "${GH_TOKEN:?GH_TOKEN env var is required (classic PAT or fine-grained PAT).}"

TARGET_REPO="${TARGET_REPO:-}"
CHEZMOI_INIT_USER="${CHEZMOI_INIT_USER:-smithclay}"

export DEBIAN_FRONTEND=noninteractive

need_cmd() { command -v "$1" >/dev/null 2>&1; }

sudo_apt_install() {
  # Usage: sudo_apt_install pkg1 pkg2 ...
  sudo apt-get update -y
  sudo apt-get install -y "$@"
}

log "Ensuring base deps exist..."
# Keep this minimal but enough for everything below.
sudo_apt_install ca-certificates curl git tar zsh

log "Installing GitHub CLI (gh) if missing..."
if ! need_cmd gh; then
  # Add GitHub CLI apt repo/key only if missing.
  if [[ ! -f /usr/share/keyrings/githubcli-archive-keyring.gpg ]]; then
    curl -fsSL https://cli.github.com/packages/githubcli-archive-keyring.gpg \
      | sudo dd of=/usr/share/keyrings/githubcli-archive-keyring.gpg >/dev/null
    sudo chmod go+r /usr/share/keyrings/githubcli-archive-keyring.gpg
  fi

  if [[ ! -f /etc/apt/sources.list.d/github-cli.list ]]; then
    echo "deb [arch=$(dpkg --print-architecture) signed-by=/usr/share/keyrings/githubcli-archive-keyring.gpg] https://cli.github.com/packages stable main" \
      | sudo tee /etc/apt/sources.list.d/github-cli.list >/dev/null
  fi

  sudo apt-get update -y
  sudo apt-get install -y gh
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

log "Installing Rust via rustup (idempotent)..."
if [[ ! -x "$HOME/.cargo/bin/rustup" ]]; then
  curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs \
    | sh -s -- -y --profile minimal
else
  log "rustup already present — ensuring toolchain is installed"
  "$HOME/.cargo/bin/rustup" toolchain install stable >/dev/null 2>&1 || true
fi

# Make cargo available in *this* script run.
if [[ -f "$HOME/.cargo/env" ]]; then
  # shellcheck disable=SC1090
  source "$HOME/.cargo/env"
fi

log "Rust versions:"
rustc --version || true
cargo --version || true

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

if [[ -d "$HOME/.local/share/chezmoi" ]]; then
  log "chezmoi repo already initialized — applying latest"
  chezmoi apply
else
  # Check if dotfiles repo exists before attempting init
  if gh repo view "$CHEZMOI_INIT_USER/dotfiles" --json name >/dev/null 2>&1; then
    log "Initializing chezmoi for user: $CHEZMOI_INIT_USER"
    chezmoi init --apply "$CHEZMOI_INIT_USER"
  else
    log "No dotfiles repo found for $CHEZMOI_INIT_USER — skipping chezmoi setup"
  fi
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
