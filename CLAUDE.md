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
    │ Stores fleet.json, manages ships via SSH
    │
    │ SSH
    ▼
Ships (exe.dev VMs, one per GitHub repo)
```

**Key design principle**: Zero external dependencies beyond bash, ssh, scp, and jq. All communication happens over SSH.

## File Structure

| Path | Purpose |
|------|---------|
| `ohcommodore` | Main CLI script (runs locally) |
| `flagship/bin/` | Scripts deployed to flagship VM (`ship-create`, `ship-destroy`, `ship-list`) |
| `cloudinit/init.sh` | Initialization script that runs on each new ship VM |

**Local config**: `~/.ohcommodore/config.json` stores flagship SSH destination

**Flagship state**: `~/.ohcommodore/fleet.json` stores ship registry (source of truth)

## Commands

```bash
# Bootstrap flagship VM
./ohcommodore init

# Fleet management
./ohcommodore fleet status
./ohcommodore fleet sink              # Destroy all ships (prompts for confirmation)
./ohcommodore fleet sink --scuttle    # Destroy ships + flagship

# Ship management (requires GH_TOKEN env var for create)
GH_TOKEN=... ./ohcommodore ship create owner/repo
./ohcommodore ship ssh reponame
./ohcommodore ship destroy reponame
```

## Environment Variables

| Variable | Where Used | Description |
|----------|------------|-------------|
| `GH_TOKEN` | ship create, cloudinit | GitHub PAT for repo access (required) |
| `TARGET_REPO` | cloudinit | Repository to clone (e.g., `owner/repo`) |
| `INIT_URL` | ship-create | Override the init script URL |
| `CHEZMOI_INIT_USER` | cloudinit | GitHub user for dotfiles (default: `smithclay`) |

## Ship Initialization

When a ship is created, `cloudinit/init.sh` runs and installs:
- GitHub CLI with authenticated token
- Target repository cloned to `~/<repo-name>`
- Zellij terminal multiplexer
- Rust toolchain via rustup
- Oh My Zsh with zsh as default shell
- Dotfiles via chezmoi
