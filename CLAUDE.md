# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

ohcommodore is a lightweight multi-coding agent control plane built on exe.dev VMs. It manages a fleet of pre-configured VMs with source code checked out, using a nautical-themed architecture.

## Architecture

```
Commodore (local CLI: ./ohcommodore)
    │
    │ SSH
    ▼
Flagship (exe.dev VM, long-running coordinator)
    │ Stores fleet in DuckDB, manages ships via SSH
    │
    │ SSH
    ▼
Ships (exe.dev VMs, one per GitHub repo)
```

**Key design principle**: Minimal external dependencies (bash, ssh, scp, duckdb). All communication happens over SSH.

## File Structure

| Path | Purpose |
|------|---------|
| `ohcommodore` | Main CLI script (runs everywhere: local, flagship, ships) |
| `cloudinit/init.sh` | OS-level initialization (apt, tools, zsh, etc.) |

**Identity detection:** `~/.ohcommodore/identity.json` with `{"role":"local|commodore|captain"}`

**Local config:** `~/.ohcommodore/config.json` stores flagship SSH destination (local only)

**VM state:** `~/.ohcommodore/ns/<namespace>/data.duckdb` stores fleet registry (flagship), local config, and messages.

## Commands

```bash
# Bootstrap flagship VM (requires GH_TOKEN)
GH_TOKEN=... ./ohcommodore init

# Fleet management
./ohcommodore fleet status
./ohcommodore fleet sink              # Destroy all ships (prompts for confirmation)
./ohcommodore fleet sink --scuttle    # Destroy ships + flagship

# Ship management (requires GH_TOKEN env var for create)
GH_TOKEN=... ./ohcommodore ship create owner/repo
./ohcommodore ship ssh reponame
./ohcommodore ship destroy reponame
```

### Inbox Commands

Run these commands on a ship (via `ohcommodore ship ssh <ship-name>`):

```bash
# List inbox messages
ohcommodore inbox list
ohcommodore inbox list --status done

# Send a command to another ship
ohcommodore inbox send captain@other-ship "cargo test"

# Send a command to commodore
ohcommodore inbox send commodore@flagship-host "echo 'Report from ship'"

# Get this ship's identity
ohcommodore inbox identity

# Manual message management
ohcommodore inbox read <id>        # Mark handled and return message JSON
```

## Environment Variables

| Variable | Where Used | Description |
|----------|------------|-------------|
| `GH_TOKEN` | init, ship create, cloudinit | GitHub PAT for repo access (required) |
| `TARGET_REPO` | cloudinit | Repository to clone (e.g., `owner/repo`) |
| `INIT_PATH` | init, ship create | Local path to init script (scp'd to VMs, overrides `INIT_URL`) |
| `INIT_URL` | init, ship create | Override the init script URL |
| `DOTFILES_PATH` | init, ship create | Local dotfiles directory (scp'd to VMs, highest priority) |
| `DOTFILES_URL` | init, ship create, cloudinit | Dotfiles repo URL (default: `https://github.com/smithclay/ohcommodore`) |
| `ROLE` | cloudinit | Identity role: `captain` (ships) or `commodore` (flagship) |

## Ship Initialization

When a ship is created, `cloudinit/init.sh` runs and installs:
- GitHub CLI with authenticated token
- Target repository cloned to `~/<repo-name>`
- Zellij terminal multiplexer
- Rust toolchain via rustup
- Oh My Zsh with zsh as default shell
- Dotfiles via chezmoi
- DuckDB CLI and inbox system (`ship-inbox`, `ship-scheduler`)

## v2 Queue System

The v2 messaging system replaces remote DuckDB writes with file-based NDJSON queue transport.

### Directory Layout (per namespace)

```
~/.ohcommodore/ns/<namespace>/
├── q/
│   ├── inbound/           # Incoming messages (visible)
│   │   └── .incoming/     # Staging area for SCP
│   ├── outbound/          # Outgoing messages
│   ├── dead/              # Failed messages + .reason files
├── artifacts/             # Command output files
│   └── <request_id>/
│       ├── stdout.txt
│       └── stderr.txt
└── data.duckdb            # Messages table
```

### Message Format

Messages are JSON files with this envelope:

```json
{
  "message_id": "uuid",
  "created_at": "2026-01-20T19:12:03Z",
  "source": "captain@ship-01",
  "dest": "commodore@flagship",
  "topic": "cmd.exec",
  "job_id": null,
  "lease_token": null,
  "payload": {}
}
```

### Protocol Topics

| Topic | Direction | Purpose |
|-------|-----------|---------|
| `cmd.exec` | → ship | Execute a command |
| `cmd.result` | ← ship | Return execution result |

### Environment Variables (v2)

| Variable | Default | Description |
|----------|---------|-------------|
| `OHCOM_NS` | `default` | Active namespace |

### Commands (v2)

```bash
ohcommodore queue status     # Show queue counts and DB stats
```
