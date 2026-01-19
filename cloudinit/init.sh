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
sudo_apt_install ca-certificates curl git jq tar unzip zsh

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

# ──────────────────────────────────────────────────────────
# ohcaptain infrastructure (inbox + scheduler)
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

log "Setting up ohcaptain directories..."
mkdir -p ~/.local/ohcaptain/bin
export PATH="$HOME/.local/ohcaptain/bin:$PATH"

log "Initializing ohcaptain database..."
duckdb ~/.local/ohcaptain/data.duckdb "
  CREATE TABLE IF NOT EXISTS config (
    key TEXT PRIMARY KEY,
    value TEXT
  );
  INSERT INTO config (key, value) VALUES ('POLL_INTERVAL_SEC', '10')
    ON CONFLICT (key) DO NOTHING;

  CREATE TABLE IF NOT EXISTS inbox (
    id TEXT PRIMARY KEY,
    created_at TEXT DEFAULT (datetime('now')),
    status TEXT DEFAULT 'unread',
    command TEXT,
    exit_code INTEGER,
    result TEXT,
    error TEXT
  );
"

log "Deploying ohcaptain-scheduler..."
cat > ~/.local/ohcaptain/bin/ohcaptain-scheduler << 'SCHEDULER_EOF'
#!/bin/bash
set -uo pipefail

DB=~/.local/ohcaptain/data.duckdb

get_poll_interval() {
  duckdb "$DB" -noheader -csv \
    "SELECT value FROM config WHERE key = 'POLL_INTERVAL_SEC'" 2>/dev/null || echo "10"
}

while true; do
  # Claim one unread message
  msg=$(duckdb "$DB" -json "
    UPDATE inbox SET status = 'running'
    WHERE id = (SELECT id FROM inbox WHERE status = 'unread' LIMIT 1)
    RETURNING id, command
  " 2>/dev/null)

  if [ -n "$msg" ] && [ "$msg" != "[]" ]; then
    id=$(echo "$msg" | jq -r '.[0].id')
    cmd=$(echo "$msg" | jq -r '.[0].command')

    result=$(eval "$cmd" 2>&1)
    exit_code=$?

    # Escape single quotes in result for SQL
    escaped_result=$(printf '%s' "$result" | sed "s/'/''/g")

    if [ $exit_code -eq 0 ]; then
      duckdb "$DB" "UPDATE inbox SET status='done', exit_code=$exit_code, result='$escaped_result' WHERE id='$id'"
    else
      duckdb "$DB" "UPDATE inbox SET status='error', exit_code=$exit_code, result='$escaped_result', error='Command failed with exit code $exit_code' WHERE id='$id'"
    fi
  fi

  sleep "$(get_poll_interval)"
done
SCHEDULER_EOF
chmod +x ~/.local/ohcaptain/bin/ohcaptain-scheduler

log "Deploying ohcaptain-inbox CLI..."
cat > ~/.local/ohcaptain/bin/ohcaptain-inbox << 'INBOX_EOF'
#!/bin/bash
set -uo pipefail

DB=~/.local/ohcaptain/data.duckdb

usage() {
  echo "Usage: ohcaptain-inbox <command> [args]"
  echo ""
  echo "Commands:"
  echo "  list [--status <status>]  List inbox messages"
  echo "  send <command>            Send a command to the inbox"
  echo "  read <id>                 Mark message as pending (claim it)"
  echo "  done <id>                 Mark message as done"
  echo "  error <id> <message>      Mark message as error"
  echo "  delete <id>               Delete a message"
  exit 1
}

[ $# -lt 1 ] && usage

cmd="$1"
shift

case "$cmd" in
  list)
    status_filter=""
    if [ "${1:-}" = "--status" ] && [ -n "${2:-}" ]; then
      status_filter="WHERE status = '$2'"
    fi
    duckdb "$DB" -box "SELECT id, status, command, exit_code, created_at FROM inbox $status_filter ORDER BY created_at DESC"
    ;;
  send)
    [ $# -lt 1 ] && { echo "Usage: ohcaptain-inbox send <command>"; exit 1; }
    id=$(uuidgen 2>/dev/null || cat /proc/sys/kernel/random/uuid)
    escaped_cmd=$(printf '%s' "$1" | sed "s/'/''/g")
    duckdb "$DB" "INSERT INTO inbox (id, command) VALUES ('$id', '$escaped_cmd')"
    echo "Message queued: $id"
    ;;
  read)
    [ $# -lt 1 ] && { echo "Usage: ohcaptain-inbox read <id>"; exit 1; }
    duckdb "$DB" "UPDATE inbox SET status = 'pending' WHERE id = '$1'"
    duckdb "$DB" -json "SELECT * FROM inbox WHERE id = '$1'"
    ;;
  done)
    [ $# -lt 1 ] && { echo "Usage: ohcaptain-inbox done <id>"; exit 1; }
    duckdb "$DB" "UPDATE inbox SET status = 'done' WHERE id = '$1'"
    echo "Marked done: $1"
    ;;
  error)
    [ $# -lt 2 ] && { echo "Usage: ohcaptain-inbox error <id> <message>"; exit 1; }
    escaped_msg=$(printf '%s' "$2" | sed "s/'/''/g")
    duckdb "$DB" "UPDATE inbox SET status = 'error', error = '$escaped_msg' WHERE id = '$1'"
    echo "Marked error: $1"
    ;;
  delete)
    [ $# -lt 1 ] && { echo "Usage: ohcaptain-inbox delete <id>"; exit 1; }
    duckdb "$DB" "DELETE FROM inbox WHERE id = '$1'"
    echo "Deleted: $1"
    ;;
  *)
    usage
    ;;
esac
INBOX_EOF
chmod +x ~/.local/ohcaptain/bin/ohcaptain-inbox

log "Creating systemd user service for scheduler..."
mkdir -p ~/.config/systemd/user

cat > ~/.config/systemd/user/ohcaptain-scheduler.service << 'SERVICE_EOF'
[Unit]
Description=ohcaptain inbox scheduler
After=default.target

[Service]
ExecStart=%h/.local/ohcaptain/bin/ohcaptain-scheduler
Restart=always
RestartSec=5
Environment="PATH=/usr/local/bin:/usr/bin:/bin"

[Install]
WantedBy=default.target
SERVICE_EOF

systemctl --user daemon-reload
systemctl --user enable ohcaptain-scheduler
systemctl --user start ohcaptain-scheduler

log "ohcaptain scheduler running and enabled on boot"

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
