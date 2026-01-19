# ohcommodore Design

A lightweight multi-coding agent control plane built entirely on exe.dev.

> "Simple Kubernetes for VMs running AI agents, with way less complexity."

## Overview

Three levels of abstraction:

| Level | Name | Description |
|-------|------|-------------|
| Control Plane | **Commodore** | Local CLI on your machine |
| Orchestrator | **Flagship** | Long-running exe.dev VM that manages ships |
| Workers | **Ships** | exe.dev VMs, each bound to one GitHub repo |

## Architecture

```
┌─────────────────────────────────────────────────────────────┐
│                     YOUR LAPTOP                             │
│  ┌─────────────────────────────────────────────────────┐   │
│  │  commodore CLI (bash)                               │   │
│  │  - ohcommodore fleet status                         │   │
│  │  - ohcommodore ship create owner/repo               │   │
│  │  - ohcommodore ship destroy <name>                  │   │
│  │  - ohcommodore ship ssh <name>                      │   │
│  └───────────────────────┬─────────────────────────────┘   │
└──────────────────────────┼──────────────────────────────────┘
                           │ SSH
                           ▼
┌─────────────────────────────────────────────────────────────┐
│                  FLAGSHIP (exe.dev VM)                      │
│  - Long-running coordinator                                 │
│  - ~/.ohcommodore/fleet.json (state, rebuildable)          │
│  - Receives commands via SSH                               │
│  - Spawns/destroys ships via `ssh exe.dev new/destroy`     │
└───────────────────────────┬─────────────────────────────────┘
                            │ SSH
              ┌─────────────┼─────────────┐
              ▼             ▼             ▼
┌───────────────┐  ┌───────────────┐  ┌───────────────┐
│ SHIP: repo-a  │  │ SHIP: repo-b  │  │ SHIP: repo-c  │
│ (exe.dev VM)  │  │ (exe.dev VM)  │  │ (exe.dev VM)  │
│ - 1 repo      │  │ - 1 repo      │  │ - 1 repo      │
│ - AI agent    │  │ - AI agent    │  │ - AI agent    │
└───────────────┘  └───────────────┘  └───────────────┘
```

## Design Principles

- **Zero external dependencies**: bash + ssh + jq only
- **SSH everywhere**: All communication over SSH, exe.dev handles auth
- **Rebuildable state**: fleet.json can be reconstructed from exe.dev APIs
- **Self-documenting**: Clear command structure, readable scripts

## File Structure

### Local (Commodore on your machine)

```
~/.ohcommodore/
└── config.json          # flagship SSH destination
```

**config.json:**
```json
{
  "flagship": "exedev@flagship-abc123.ssh.exe.dev",
  "flagship_vm": "flagship-abc123",
  "created_at": "2026-01-19T..."
}
```

### On Flagship VM

```
~/.ohcommodore/
├── fleet.json           # ship registry (source of truth)
└── bin/
    ├── ship-create      # called by commodore
    ├── ship-destroy
    ├── ship-list
    └── ship-ssh
```

**fleet.json:**
```json
{
  "ships": {
    "icepick": {
      "repo": "smithclay/icepick",
      "ssh_dest": "ship-icepick-x7k.ssh.exe.dev",
      "created_at": "2026-01-19T10:30:00Z",
      "status": "running"
    },
    "webapp": {
      "repo": "smithclay/webapp",
      "ssh_dest": "ship-webapp-m2p.ssh.exe.dev",
      "created_at": "2026-01-19T11:00:00Z",
      "status": "running"
    }
  },
  "updated_at": "2026-01-19T11:00:00Z"
}
```

## CLI Commands

### `ohcommodore init`

Bootstrap the flagship:
1. Create flagship VM via exe.dev
2. Wait for SSH ready
3. Copy flagship scripts to ~/.ohcommodore/bin/
4. Save flagship SSH dest to local config

### `ohcommodore fleet status`

```
$ ohcommodore fleet status

FLAGSHIP: flagship-abc123.ssh.exe.dev (up)

SHIPS (2):
  NAME       REPO                STATUS   AGE
  icepick    smithclay/icepick   running  2h
  webapp     smithclay/webapp    running  45m
```

### `ohcommodore fleet sink [--scuttle]`

Destroy all ships in the fleet:

```
$ ohcommodore fleet sink
WARNING: This will destroy all ships:
  - icepick (smithclay/icepick)
  - webapp (smithclay/webapp)

Type 'sink' to confirm: sink

==> Sinking icepick...done
==> Sinking webapp...done
==> Fleet sunk. Flagship still running.
```

With `--scuttle`: also destroys the flagship itself.

### `ohcommodore ship create <repo>`

```
$ ohcommodore ship create smithclay/icepick
==> Creating ship 'icepick' for smithclay/icepick...
==> Ship ready: ssh exedev@ship-icepick-x7k.ssh.exe.dev
```

### `ohcommodore ship destroy <name>`

```
$ ohcommodore ship destroy icepick
==> Destroying ship 'icepick'...done
```

### `ohcommodore ship ssh <name>`

```
$ ohcommodore ship ssh icepick
# Opens interactive SSH session to the ship
```

## Flagship Scripts

### `~/.ohcommodore/bin/ship-create`

```bash
#!/usr/bin/env bash
set -euo pipefail

REPO="$1"
NAME="${REPO##*/}"  # smithclay/icepick → icepick

# Create ship via exe.dev
create_json=$(ssh exe.dev new --json \
  --name="ship-${NAME}" \
  --no-email \
  --env GH_TOKEN="$GH_TOKEN" \
  --env TARGET_REPO="$REPO")

ssh_dest=$(echo "$create_json" | jq -r '.ssh_dest')

# Update fleet.json
jq --arg name "$NAME" \
   --arg repo "$REPO" \
   --arg dest "$ssh_dest" \
   '.ships[$name] = {repo: $repo, ssh_dest: $dest, status: "running", created_at: now|todate}' \
   ~/.ohcommodore/fleet.json > tmp && mv tmp ~/.ohcommodore/fleet.json

echo "$ssh_dest"
```

### `~/.ohcommodore/bin/ship-destroy`

```bash
#!/usr/bin/env bash
set -euo pipefail
ssh exe.dev destroy "ship-${1}"
jq --arg name "$1" 'del(.ships[$name])' ~/.ohcommodore/fleet.json > tmp && mv tmp ~/.ohcommodore/fleet.json
```

### `~/.ohcommodore/bin/ship-list`

```bash
#!/usr/bin/env bash
cat ~/.ohcommodore/fleet.json
```

## Main CLI Script

```bash
#!/usr/bin/env bash
set -euo pipefail

CONFIG_DIR="$HOME/.ohcommodore"
CONFIG_FILE="$CONFIG_DIR/config.json"

die() { printf 'ERROR: %s\n' "$*" >&2; exit 1; }
need_flagship() { [[ -f "$CONFIG_FILE" ]] || die "No flagship. Run: ohcommodore init"; }
flagship() { jq -r '.flagship' "$CONFIG_FILE"; }
flagship_ssh() { ssh "$(flagship)" "$@"; }

cmd_init() {
  # Create flagship VM, install scripts, save config
  ...
}

cmd_fleet_status() {
  need_flagship
  echo "FLAGSHIP: $(flagship) (up)"
  echo ""
  flagship_ssh '~/.ohcommodore/bin/ship-list' | jq -r '
    "SHIPS (\(.ships | length)):",
    "  NAME\t\tREPO\t\t\tSTATUS",
    (.ships | to_entries[] | "  \(.key)\t\t\(.value.repo)\t\(.value.status)")
  '
}

cmd_fleet_sink() {
  need_flagship
  ships=$(flagship_ssh '~/.ohcommodore/bin/ship-list' | jq -r '.ships | keys[]')
  [[ -z "$ships" ]] && { echo "No ships to sink."; exit 0; }

  echo "WARNING: This will destroy all ships:"
  echo "$ships" | while read -r name; do echo "  - $name"; done
  printf "Type 'sink' to confirm: "; read -r confirm
  [[ "$confirm" == "sink" ]] || die "Aborted."

  echo "$ships" | while read -r name; do
    echo "==> Sinking $name..."
    flagship_ssh "~/.ohcommodore/bin/ship-destroy '$name'"
  done
  echo "==> Fleet sunk."

  [[ "${1:-}" == "--scuttle" ]] && {
    echo "==> Scuttling flagship..."
    ssh exe.dev destroy "$(jq -r '.flagship_vm' "$CONFIG_FILE")"
    rm -rf "$CONFIG_DIR"
    echo "==> Fleet decommissioned."
  }
}

cmd_ship_create() { need_flagship; flagship_ssh "~/.ohcommodore/bin/ship-create '$1'"; }
cmd_ship_destroy() { need_flagship; flagship_ssh "~/.ohcommodore/bin/ship-destroy '$1'"; }
cmd_ship_ssh() {
  need_flagship
  dest=$(flagship_ssh "~/.ohcommodore/bin/ship-list" | jq -r --arg n "$1" '.ships[$n].ssh_dest')
  [[ -n "$dest" && "$dest" != "null" ]] || die "Ship '$1' not found"
  exec ssh "exedev@$dest"
}

case "${1:-help}" in
  init)           cmd_init ;;
  fleet)
    case "${2:-}" in
      status)     cmd_fleet_status ;;
      sink)       cmd_fleet_sink "${3:-}" ;;
      *)          die "Usage: ohcommodore fleet [status|sink]" ;;
    esac ;;
  ship)
    case "${2:-}" in
      create)     cmd_ship_create "${3:?repo required}" ;;
      destroy)    cmd_ship_destroy "${3:?name required}" ;;
      ssh)        cmd_ship_ssh "${3:?name required}" ;;
      *)          die "Usage: ohcommodore ship [create|destroy|ssh] <arg>" ;;
    esac ;;
  *)              echo "Usage: ohcommodore [init|fleet|ship] ..." ;;
esac
```

## State Recovery

If `fleet.json` is lost, reconstruct from exe.dev:

```bash
ssh exe.dev list --json | jq '
  [.[] | select(.name | startswith("ship-"))] |
  reduce .[] as $vm ({}; .[$vm.name | ltrimstr("ship-")] = {
    ssh_dest: $vm.ssh_dest,
    status: "running",
    repo: "unknown"
  }) | {ships: ., updated_at: now|todate}
'
```

## Future Work

- **Task dispatching**: Send work to ships, collect results
- **Ship health checks**: Monitor ship status, auto-restart
- **Logs aggregation**: Stream logs from all ships
- **Multiple flagships**: Different flagships for different teams/projects
