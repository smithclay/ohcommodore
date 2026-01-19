# ohcommodore

A lightweight multi-coding agent control plane built on top of [exe.dev](https://exe.dev) VMs. Manage a fleet of pre-configured VMs with Claude Code configured and source code checked out.

Loosely inspired by Steve Yegge's [Gas Town](https://github.com/steveyegge/gastown), this is going to be one of approximately 400,000 agent orchestration tools in 2026. Instead of the "Mad Max" naming conventions in Yegge's post, we're going with a nautical theme.

## Quick Start

```bash
# Bootstrap the flagship
./ohcommodore init

# Check fleet status
./ohcommodore fleet status

# Create a ship for a repo
# GH_TOKEN should be a fine-grained access token scoped to the repo
# Create a token at https://github.com/settings/personal-access-tokens
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

## Cloud Init

The `cloudinit/` directory contains the initialization script that runs on each new ship VM. The script (`init.sh`) sets up a development environment with:

- Base packages (git, curl, zsh)
- GitHub CLI with authentication
- Target repository cloned to `~/<repo-name>`
- Zellij terminal multiplexer
- Rust toolchain via rustup
- Oh My Zsh
- [Dotfiles via chezmoi](https://www.chezmoi.io/)

### Environment Variables

The init script accepts these environment variables:

| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `GH_TOKEN` | Yes | - | GitHub PAT for authentication and repo access |
| `TARGET_REPO` | No | - | Repository to clone (e.g., `owner/repo`) |
| `CHEZMOI_INIT_USER` | No | `smithclay` | GitHub user whose dotfiles to apply |

### Customizing the Init Script

You can override the init script URL when creating ships:

```bash
# Use a custom init script
INIT_URL=https://example.com/my-init.sh ./ohcommodore ship create owner/repo
```

The `INIT_URL` environment variable can be set on the flagship to change the default for all ships.

## Requirements

- bash
- ssh
- scp
- jq
- exe.dev account with SSH access configured
