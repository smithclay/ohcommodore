# ohcommodore Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Build a lightweight multi-coding agent control plane on exe.dev with Commodore (local CLI), Flagship (orchestrator VM), and Ships (worker VMs).

**Architecture:** Local bash CLI (`ohcommodore`) communicates over SSH with a flagship VM that manages ship VMs. State stored as JSON on flagship, rebuildable from exe.dev APIs.

**Tech Stack:** Bash, SSH, jq, exe.dev

---

## Task 1: Create Flagship Scripts

**Files:**
- Create: `flagship/bin/ship-create`
- Create: `flagship/bin/ship-destroy`
- Create: `flagship/bin/ship-list`

These scripts will be copied to the flagship during `ohcommodore init`.

**Step 1: Create flagship directory structure**

```bash
mkdir -p flagship/bin
```

**Step 2: Write ship-list script**

Create `flagship/bin/ship-list`:
```bash
#!/usr/bin/env bash
set -euo pipefail
cat ~/.ohcommodore/fleet.json
```

**Step 3: Write ship-create script**

Create `flagship/bin/ship-create`:
```bash
#!/usr/bin/env bash
set -euo pipefail

REPO="$1"
NAME="${REPO##*/}"

: "${GH_TOKEN:?GH_TOKEN required}"

create_json=$(ssh exe.dev new --json \
  --name="ship-${NAME}" \
  --no-email \
  --env GH_TOKEN="$GH_TOKEN" \
  --env TARGET_REPO="$REPO")

ssh_dest=$(echo "$create_json" | jq -r '.ssh_dest')
[[ -n "$ssh_dest" && "$ssh_dest" != "null" ]] || { echo "ERROR: Failed to get ssh_dest" >&2; exit 1; }

jq --arg name "$NAME" \
   --arg repo "$REPO" \
   --arg dest "$ssh_dest" \
   '.ships[$name] = {repo: $repo, ssh_dest: $dest, status: "running", created_at: (now|todate)}' \
   ~/.ohcommodore/fleet.json > ~/.ohcommodore/fleet.json.tmp \
   && mv ~/.ohcommodore/fleet.json.tmp ~/.ohcommodore/fleet.json

echo "$ssh_dest"
```

**Step 4: Write ship-destroy script**

Create `flagship/bin/ship-destroy`:
```bash
#!/usr/bin/env bash
set -euo pipefail

NAME="$1"
[[ -n "$NAME" ]] || { echo "ERROR: ship name required" >&2; exit 1; }

ssh exe.dev destroy "ship-${NAME}" || true

jq --arg name "$NAME" 'del(.ships[$name])' \
   ~/.ohcommodore/fleet.json > ~/.ohcommodore/fleet.json.tmp \
   && mv ~/.ohcommodore/fleet.json.tmp ~/.ohcommodore/fleet.json

echo "Ship '$NAME' destroyed."
```

**Step 5: Make scripts executable**

```bash
chmod +x flagship/bin/*
```

**Step 6: Commit**

```bash
git add flagship/
git commit -m "feat: add flagship scripts for ship management"
```

---

## Task 2: Create Main CLI - Core Functions

**Files:**
- Create: `ohcommodore`

**Step 1: Create ohcommodore with core utilities**

Create `ohcommodore`:
```bash
#!/usr/bin/env bash
set -euo pipefail

CONFIG_DIR="$HOME/.ohcommodore"
CONFIG_FILE="$CONFIG_DIR/config.json"

log() { printf '==> %s\n' "$*"; }
die() { printf 'ERROR: %s\n' "$*" >&2; exit 1; }

need_cmd() {
  command -v "$1" >/dev/null 2>&1 || die "Missing required command: $1"
}

need_flagship() {
  [[ -f "$CONFIG_FILE" ]] || die "No flagship configured. Run: ohcommodore init"
}

flagship() {
  jq -r '.flagship' "$CONFIG_FILE"
}

flagship_vm() {
  jq -r '.flagship_vm' "$CONFIG_FILE"
}

flagship_ssh() {
  ssh "$(flagship)" "$@"
}

# Commands will be added in subsequent tasks

show_help() {
  cat <<'HELP'
ohcommodore - lightweight multi-agent control plane

USAGE:
  ohcommodore init                    Bootstrap flagship VM
  ohcommodore fleet status            Show fleet status
  ohcommodore fleet sink [--scuttle]  Destroy all ships (--scuttle: also flagship)
  ohcommodore ship create <repo>      Create ship for owner/repo
  ohcommodore ship destroy <name>     Destroy a ship
  ohcommodore ship ssh <name>         SSH into a ship
HELP
}

case "${1:-help}" in
  -h|--help|help) show_help ;;
  *) die "Unknown command: $1. Run 'ohcommodore help' for usage." ;;
esac
```

**Step 2: Make executable**

```bash
chmod +x ohcommodore
```

**Step 3: Commit**

```bash
git add ohcommodore
git commit -m "feat: add ohcommodore CLI skeleton with core utilities"
```

---

## Task 3: Implement `ohcommodore init`

**Files:**
- Modify: `ohcommodore`

**Step 1: Add SSH wait helper function**

Add after `flagship_ssh()`:
```bash
wait_for_ssh() {
  local dest="$1"
  local retries="${2:-30}"
  local delay="${3:-2}"
  local opts='-o StrictHostKeyChecking=accept-new -o BatchMode=yes -o ConnectTimeout=5'

  for ((i=1; i<=retries; i++)); do
    if ssh $opts "$dest" 'true' >/dev/null 2>&1; then
      return 0
    fi
    log "SSH not ready ($i/$retries)..."
    sleep "$delay"
  done
  return 1
}
```

**Step 2: Add cmd_init function**

Add after `wait_for_ssh()`:
```bash
cmd_init() {
  need_cmd ssh
  need_cmd jq

  [[ -f "$CONFIG_FILE" ]] && die "Already initialized. Config: $CONFIG_FILE"

  log "Creating flagship VM..."
  create_json=$(ssh exe.dev new --json --name="flagship-ohcommodore" --no-email) \
    || die "Failed to create flagship VM"

  ssh_dest=$(echo "$create_json" | jq -r '.ssh_dest // empty')
  [[ -n "$ssh_dest" ]] || die "Failed to get ssh_dest from: $create_json"

  flagship_dest="exedev@${ssh_dest}"

  log "Flagship created: $ssh_dest"
  log "Waiting for SSH..."
  sleep 5

  wait_for_ssh "$flagship_dest" || die "SSH never became ready for $flagship_dest"

  log "Installing flagship scripts..."
  ssh "$flagship_dest" 'mkdir -p ~/.ohcommodore/bin'
  scp -q flagship/bin/* "${flagship_dest}:~/.ohcommodore/bin/"
  ssh "$flagship_dest" 'chmod +x ~/.ohcommodore/bin/*'

  # Initialize empty fleet.json
  ssh "$flagship_dest" 'echo "{\"ships\":{},\"updated_at\":\"$(date -u +%Y-%m-%dT%H:%M:%SZ)\"}" > ~/.ohcommodore/fleet.json'

  log "Saving local config..."
  mkdir -p "$CONFIG_DIR"
  cat > "$CONFIG_FILE" <<EOF
{
  "flagship": "$flagship_dest",
  "flagship_vm": "flagship-ohcommodore",
  "created_at": "$(date -u +%Y-%m-%dT%H:%M:%SZ)"
}
EOF

  log "Done! Flagship ready at: $flagship_dest"
  log "Run 'ohcommodore fleet status' to verify."
}
```

**Step 3: Update case statement**

Replace the case statement:
```bash
case "${1:-help}" in
  -h|--help|help) show_help ;;
  init) cmd_init ;;
  *) die "Unknown command: $1. Run 'ohcommodore help' for usage." ;;
esac
```

**Step 4: Commit**

```bash
git add ohcommodore
git commit -m "feat: implement ohcommodore init command"
```

---

## Task 4: Implement `ohcommodore fleet status`

**Files:**
- Modify: `ohcommodore`

**Step 1: Add cmd_fleet_status function**

Add after `cmd_init()`:
```bash
cmd_fleet_status() {
  need_flagship

  echo "FLAGSHIP: $(flagship)"
  echo ""

  fleet_json=$(flagship_ssh 'cat ~/.ohcommodore/fleet.json')

  ship_count=$(echo "$fleet_json" | jq '.ships | length')

  if [[ "$ship_count" -eq 0 ]]; then
    echo "SHIPS: (none)"
  else
    echo "SHIPS ($ship_count):"
    printf "  %-15s %-30s %s\n" "NAME" "REPO" "STATUS"
    echo "$fleet_json" | jq -r '.ships | to_entries[] | "  \(.key)\t\(.value.repo)\t\(.value.status)"' | \
      while IFS=$'\t' read -r name repo status; do
        printf "  %-15s %-30s %s\n" "$name" "$repo" "$status"
      done
  fi
}
```

**Step 2: Update case statement**

```bash
case "${1:-help}" in
  -h|--help|help) show_help ;;
  init) cmd_init ;;
  fleet)
    case "${2:-}" in
      status) cmd_fleet_status ;;
      *) die "Usage: ohcommodore fleet [status|sink]" ;;
    esac ;;
  *) die "Unknown command: $1. Run 'ohcommodore help' for usage." ;;
esac
```

**Step 3: Commit**

```bash
git add ohcommodore
git commit -m "feat: implement ohcommodore fleet status command"
```

---

## Task 5: Implement Ship Commands

**Files:**
- Modify: `ohcommodore`

**Step 1: Add cmd_ship_create function**

Add after `cmd_fleet_status()`:
```bash
cmd_ship_create() {
  need_flagship
  local repo="$1"
  [[ -n "$repo" ]] || die "Usage: ohcommodore ship create <owner/repo>"

  local name="${repo##*/}"
  log "Creating ship '$name' for $repo..."

  ssh_dest=$(flagship_ssh "GH_TOKEN='$GH_TOKEN' ~/.ohcommodore/bin/ship-create '$repo'")

  log "Ship ready: exedev@$ssh_dest"
}
```

**Step 2: Add cmd_ship_destroy function**

```bash
cmd_ship_destroy() {
  need_flagship
  local name="$1"
  [[ -n "$name" ]] || die "Usage: ohcommodore ship destroy <name>"

  log "Destroying ship '$name'..."
  flagship_ssh "~/.ohcommodore/bin/ship-destroy '$name'"
  log "Done."
}
```

**Step 3: Add cmd_ship_ssh function**

```bash
cmd_ship_ssh() {
  need_flagship
  local name="$1"
  [[ -n "$name" ]] || die "Usage: ohcommodore ship ssh <name>"

  local dest
  dest=$(flagship_ssh "cat ~/.ohcommodore/fleet.json" | jq -r --arg n "$name" '.ships[$n].ssh_dest // empty')
  [[ -n "$dest" ]] || die "Ship '$name' not found"

  exec ssh "exedev@$dest"
}
```

**Step 4: Update case statement**

```bash
case "${1:-help}" in
  -h|--help|help) show_help ;;
  init) cmd_init ;;
  fleet)
    case "${2:-}" in
      status) cmd_fleet_status ;;
      *) die "Usage: ohcommodore fleet [status|sink]" ;;
    esac ;;
  ship)
    case "${2:-}" in
      create) cmd_ship_create "${3:-}" ;;
      destroy) cmd_ship_destroy "${3:-}" ;;
      ssh) cmd_ship_ssh "${3:-}" ;;
      *) die "Usage: ohcommodore ship [create|destroy|ssh] <arg>" ;;
    esac ;;
  *) die "Unknown command: $1. Run 'ohcommodore help' for usage." ;;
esac
```

**Step 5: Commit**

```bash
git add ohcommodore
git commit -m "feat: implement ship create, destroy, ssh commands"
```

---

## Task 6: Implement `ohcommodore fleet sink`

**Files:**
- Modify: `ohcommodore`

**Step 1: Add cmd_fleet_sink function**

Add after `cmd_ship_ssh()`:
```bash
cmd_fleet_sink() {
  need_flagship
  local scuttle="${1:-}"

  local ships
  ships=$(flagship_ssh 'cat ~/.ohcommodore/fleet.json' | jq -r '.ships | keys[]')

  if [[ -z "$ships" ]]; then
    echo "No ships to sink."
    return 0
  fi

  echo "WARNING: This will destroy all ships:"
  echo "$ships" | while read -r name; do
    local repo
    repo=$(flagship_ssh 'cat ~/.ohcommodore/fleet.json' | jq -r --arg n "$name" '.ships[$n].repo')
    echo "  - $name ($repo)"
  done
  echo ""
  printf "Type 'sink' to confirm: "
  read -r confirm
  [[ "$confirm" == "sink" ]] || die "Aborted."

  echo "$ships" | while read -r name; do
    log "Sinking $name..."
    flagship_ssh "~/.ohcommodore/bin/ship-destroy '$name'" >/dev/null
  done
  log "Fleet sunk."

  if [[ "$scuttle" == "--scuttle" ]]; then
    log "Scuttling flagship..."
    ssh exe.dev destroy "$(flagship_vm)" || true
    rm -rf "$CONFIG_DIR"
    log "Fleet decommissioned."
  else
    echo "Flagship still running."
  fi
}
```

**Step 2: Update case statement**

```bash
case "${1:-help}" in
  -h|--help|help) show_help ;;
  init) cmd_init ;;
  fleet)
    case "${2:-}" in
      status) cmd_fleet_status ;;
      sink) cmd_fleet_sink "${3:-}" ;;
      *) die "Usage: ohcommodore fleet [status|sink]" ;;
    esac ;;
  ship)
    case "${2:-}" in
      create) cmd_ship_create "${3:-}" ;;
      destroy) cmd_ship_destroy "${3:-}" ;;
      ssh) cmd_ship_ssh "${3:-}" ;;
      *) die "Usage: ohcommodore ship [create|destroy|ssh] <arg>" ;;
    esac ;;
  *) die "Unknown command: $1. Run 'ohcommodore help' for usage." ;;
esac
```

**Step 3: Commit**

```bash
git add ohcommodore
git commit -m "feat: implement fleet sink command with --scuttle option"
```

---

## Task 7: Add README

**Files:**
- Create: `README.md`

**Step 1: Write README**

Create `README.md`:
```markdown
# ohcommodore

A lightweight multi-coding agent control plane built on [exe.dev](https://exe.dev).

## Quick Start

```bash
# Bootstrap the flagship
./ohcommodore init

# Check fleet status
./ohcommodore fleet status

# Create a ship for a repo
GH_TOKEN=... ./ohcommodore ship create owner/repo

# SSH into a ship
./ohcommodore ship ssh reponame

# Destroy a ship
./ohcommodore ship destroy reponame

# Destroy all ships
./ohcommodore fleet sink

# Destroy everything (ships + flagship)
./ohcommodore fleet sink --scuttle
```

## Architecture

```
Commodore (local CLI)
    │
    │ SSH
    ▼
Flagship (exe.dev VM)
    │
    │ SSH
    ▼
Ships (exe.dev VMs, one per repo)
```

## Requirements

- bash
- ssh
- jq
- exe.dev account with SSH access configured
```

**Step 2: Commit**

```bash
git add README.md
git commit -m "docs: add README with quick start guide"
```

---

## Summary

| Task | Description | Files |
|------|-------------|-------|
| 1 | Flagship scripts | `flagship/bin/*` |
| 2 | CLI skeleton | `ohcommodore` |
| 3 | init command | `ohcommodore` |
| 4 | fleet status | `ohcommodore` |
| 5 | ship commands | `ohcommodore` |
| 6 | fleet sink | `ohcommodore` |
| 7 | README | `README.md` |
