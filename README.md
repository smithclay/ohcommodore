# ohcommodore

> Manage and coordinate a fleet of Claude Code-enabled VMs using a lightweight shell script.

Simple coding agent control plane built on top of [exe.dev](https://exe.dev) VMs. Under 1,000 lines of code.

Loosely inspired by Steve Yegge's [Gas Town](https://github.com/steveyegge/gastown), this is going to be one of approximately 400,000 agent orchestration tools in 2026. Instead of the "Mad Max" naming conventions in Yegge's post, we're going with a nautical theme. Spiritual successor to [claudetainer](https://github.com/smithclay/claudetainer).

## Install

```bash
brew install smithclay/tap/ohcommodore
```

## Quick Start

```bash
# Bootstrap the flagship
ohcommodore init

# Check fleet status
ohcommodore fleet status

# Create a ship
# GH_TOKEN should be a fine-grained access token scoped to the repo
# Create a token at github.com/settings/personal-access-tokens
# You can pass via the command line or put in .env
# Don't want to check out code? Just pass a ship name without a "/"
GH_TOKEN=... ohcommodore ship create owner/repo

# SSH into a ship
ohcommodore ship ssh reponame

# Destroy a ship
ohcommodore ship destroy reponame

# Destroy all ships
ohcommodore fleet sink

# Destroy everything (ships + flagship)
ohcommodore fleet sink --scuttle
```

## Architecture

```
Commodore (local CLI: ./ohcommodore)
    │
    │ SSH
    ▼
Flagship (exe.dev VM, runs ohcommodore as "commodore")
    │
    │ SSH
    ▼
Ships (exe.dev VMs, run ohcommodore as "captain")
```

The same `ohcommodore` script runs everywhere. Identity is determined by `~/.ohcommodore/identity.json`:
- **local** (no identity file): Commands proxy to flagship via SSH
- **commodore**: Commands execute directly on the flagship
- **captain**: Commands execute directly on ships

## Cloud Init

The `cloudinit/` directory contains the initialization script that runs on both flagship and ship VMs. The script (`init.sh`) sets up a development environment with:

- Base packages (git, curl, zsh)
- GitHub CLI with authentication
- Target repository cloned to `~/<repo-name>`
- Zellij terminal multiplexer
- Rust toolchain via rustup
- Oh My Zsh
- [Dotfiles via chezmoi](https://www.chezmoi.io/)
- DuckDB and inbox system for inter-ship communication

### Environment Variables

The init script accepts these environment variables:

| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `GH_TOKEN` | Yes | - | GitHub PAT for authentication and repo access |
| `TARGET_REPO` | No | - | Repository to clone (e.g., `owner/repo`) |
| `INIT_PATH` | No | - | Local path to init script (scp'd to VMs, overrides `INIT_URL`) |
| `INIT_URL` | No | GitHub raw URL | Remote URL to init script |
| `DOTFILES_PATH` | No | - | Local dotfiles directory (scp'd to VMs, highest priority) |
| `DOTFILES_URL` | No | `https://github.com/smithclay/ohcommodore` | Dotfiles repo URL (supports `/dotfiles` subdirectory) |
| `ROLE` | No | `captain` | Identity role: `captain` for ships, `commodore` for flagship |

### Customizing Init and Chezmoi Dotfiles

You can use local files or custom URLs for initialization:

```bash
# Use a local init script (scp'd to VMs)
INIT_PATH=./my-init.sh ohcommodore init

# Use a custom remote init script
INIT_URL=https://example.com/my-init.sh ohcommodore ship create owner/repo

# Use local chezmoi dotfiles directory (scp'd to VMs)
DOTFILES_PATH=./my-dotfiles ohcommodore init

# Use a custom dotfiles chezmoi repo URL
DOTFILES_URL=https://github.com/user/dotfiles ohcommodore ship create owner/repo
```

These can also be set in a `.env` file in the ohcommodore directory.

## Inbox

Each ship and the flagship have an inbox system for asynchronous inter-VM communication. Ships can send commands to each other that get executed by a background scheduler.

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
ohcommodore inbox read <id>        # Claim message (mark pending)
ohcommodore inbox done <id>        # Mark as completed
ohcommodore inbox error <id> "msg" # Mark as failed
ohcommodore inbox delete <id>      # Remove message
```

### How It Works

- Each VM has a DuckDB database at `~/.local/ship/data.duckdb`
- When sending a message, the sender SSHes to the recipient and INSERTs directly into their inbox table
- The `ohcommodore _scheduler` daemon polls for unread messages and executes commands
- Results (stdout, exit code) are stored back in the database
- Message statuses: `unread` → `running` → `done`/`error`

## Requirements

- bash
- ssh
- scp
- jq
- exe.dev account with SSH access configured
