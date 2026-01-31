# ocaptain

[![PyPI](https://img.shields.io/pypi/v/ocaptain.svg)](https://pypi.org/project/ocaptain/)
[![Python](https://img.shields.io/badge/Python-3.12+-blue.svg)](https://www.python.org/)

> O Captain! my Captain! our fearful Claude Code session is done, The repo has weather'd every rack, the prize we sought is won.

Minimalist multi-coding agent control plane with [Tailscale](https://tailscale.com) mesh networking. Orchestration is managed by Claude Code's built-in [task list](https://x.com/trq212/status/2014480496013803643?s=20) feature: work is distributed by the `ocaptain` CLI to VMs ("ships") that work in parallel on a plan you generate with Claude.

Ships can be provisioned on multiple backends:
- **[sprites.dev](https://sprites.dev)** — Cloud VMs with instant provisioning
- **[exe.dev](https://exe.dev)** — Alternative cloud VM provider
- **[BoxLite](https://github.com/anthropics/boxlite)** — Local micro-VMs for development (sub-second boot)

No Kubernetes, no containers, no asking for permissions.

## Table of Contents

- [What it does](#what-it-does)
- [Why?](#why)
- [Quickstart](#quickstart)
- [Architecture](#architecture)
- [CLI Reference](#cli-reference)
- [Voyage Plans](#voyage-plans)
- [Configuration](#configuration)
- [Security](#security)

## What it does

Provisions a fleet of VMs (cloud or local), each running an autonomous Claude Code agent. Ships sync files via Mutagen and coordinate through a shared task list—no central scheduler, just agents racing to complete work.

```
You (local) → ocaptain sail → Ship VMs → Ships claim tasks → Code syncs back
```

## Why?

- Deploy parallel, autonomous Claude Code instances with one command
- No container complexity: full Linux VMs with direct access
- Watch Claude Code sessions in real-time via tmux
- Task-based coordination means no conflicts, no merge hell
- Local storage with Mutagen sync—no storage VM required
- Built-in OpenTelemetry observability

## Quickstart

### Prerequisites

1. **VM Provider** (choose one):
   - [sprites.dev](https://sprites.dev) account with `sprite` CLI installed
   - [exe.dev](https://exe.dev) account
   - [BoxLite](https://github.com/anthropics/boxlite) for local micro-VMs (`pip install ocaptain[boxlite]`)
2. [Tailscale](https://tailscale.com) installed and running
3. [Mutagen](https://mutagen.io) installed (`brew install mutagen-io/mutagen/mutagen`)
4. Claude Code long-lived OAuth token (subscription required, from `claude setup-token`)
5. Tailscale OAuth secret for ephemeral auth keys (used to connect VMs)

### Install

```bash
# Install from PyPI (recommended)
uv tool install ocaptain

# Or run directly without installing
uvx ocaptain doctor
```

### Set credentials

```bash
# Claude Code OAuth token (from `claude setup-token`)
export CLAUDE_CODE_OAUTH_TOKEN="your-token-here"

# Tailscale OAuth secret (for ephemeral ship auth keys)
export OCAPTAIN_TAILSCALE_OAUTH_SECRET="tskey-client-xxxx"

# Provider-specific (choose one):
export OCAPTAIN_SPRITES_ORG="your-org"     # For sprites.dev
export OCAPTAIN_PROVIDER="boxlite"          # For local BoxLite VMs

# Optional: GitHub token for private repos
export GH_TOKEN="ghp_xxxx"
```

### Check prerequisites

```bash
ocaptain doctor
```

### Make a plan

At the moment, you need to use Claude Code to create a detailed dependency plan to pass to ocaptain.

### Use an existing plan

For convenience, example plans are available in the `examples/generated-plans` directory.

### Create a custom plan

In Claude Code, run the following then restart:

```bash
/plugin marketplace add smithclay/skills
/plugin install ocaptain-skills@smithclay-skills
```

You're now ready to generate a detailed plan in Claude Code using `/voyage-plan`:

```
Plan a voyage with ocaptain: take an empty repository and make a to-do list app.
```

### Launch a voyage

```bash
# A plan is required, see "Make a plan" above
ocaptain sail ./examples/generated-plans/multilingual-readme

# Monitor status
ocaptain status

# Attach to a ship's tmux session to watch Claude work
ocaptain shell voyage-abc123 ship-0

# View aggregated logs
ocaptain logs voyage-abc123 --follow

# Clone the workspace when done
ocaptain clone voyage-abc123

# Scuttle the fleet
ocaptain sink voyage-abc123
```

## Architecture

```mermaid
flowchart TB
    subgraph Local["Local Machine"]
        CLI[ocaptain CLI]
        Voyages[(~/voyages/)]
        Telemetry[OTLP Collector]
    end

    subgraph Tailscale["Tailscale Mesh"]
        TS[100.x.x.x]
    end

    subgraph Provider["VM Provider"]
        subgraph Fleet["Ship VMs"]
            S0[Ship 0<br/>Claude Code]
            S1[Ship 1<br/>Claude Code]
            S2[Ship N<br/>Claude Code]
        end
    end

    CLI -->|Mutagen Sync| TS
    TS -->|Workspace + Tasks| Fleet
    Fleet -->|Telemetry| Telemetry
    S0 & S1 & S2 -->|tmux sessions| CLI
```

**Supported Providers:**
- `sprites` — sprites.dev cloud VMs (default)
- `exedev` — exe.dev cloud VMs
- `boxlite` — Local micro-VMs for development

### Components

| Component | Description |
|-----------|-------------|
| **Local Voyages** | `~/voyages/<voyage-id>/` contains workspace, tasks, logs, and artifacts |
| **Ship VMs** | VMs (cloud or local) running Claude Code autonomously in tmux sessions |
| **Tailscale Mesh** | Ships join tailnet with ephemeral keys for direct connectivity |
| **Mutagen Sync** | Two-way file sync between laptop and ships (workspace + tasks) |
| **Task List** | Shared JSON files in `~/.claude/tasks/`. Ships race to claim pending tasks |

### Voyage Lifecycle

1. **Setup** — Local voyage directory created, repo cloned, tasks seeded
2. **Bootstrap** — Ship VMs provisioned in parallel, join Tailscale, install Claude
3. **Sync** — Mutagen sessions established for workspace and tasks
4. **Launch** — Claude starts in tmux on each ship (autonomous operation)
5. **Work** — Ships claim tasks, do work, changes sync back via Mutagen
6. **Complete** — All tasks done, workspace ready to clone
7. **Sink** — VMs destroyed, Tailscale nodes removed

## CLI Reference

### `ocaptain sail <plan>`

Launch a new voyage from a plan directory.

```bash
ocaptain sail ./plans/add-auth --ships 5
```

| Option | Description |
|--------|-------------|
| `--ships, -n` | Override recommended ship count |
| `--no-telemetry` | Disable OTLP telemetry collection |

### `ocaptain status [voyage_id]`

Show voyage status derived from task list. Auto-selects if only one active voyage.

```bash
ocaptain status
ocaptain status voyage-abc123
```

### `ocaptain logs <voyage_id>`

View aggregated logs from all ships.

```bash
ocaptain logs voyage-abc123
ocaptain logs voyage-abc123 --follow --grep "error"
```

| Option | Description |
|--------|-------------|
| `--ship, -s` | Filter to specific ship |
| `--follow, -f` | Stream logs in real-time |
| `--grep, -g` | Filter log lines by pattern |
| `--tail, -n` | Show last N lines |

### `ocaptain tasks <voyage_id>`

Display task list with status, assignees, and blockers.

```bash
ocaptain tasks voyage-abc123
ocaptain tasks voyage-abc123 --status pending
```

### `ocaptain shell <voyage_id> [ship_id]`

Attach to a ship's tmux session to observe Claude working.

```bash
ocaptain shell voyage-abc123 ship-0      # Attach to ship's tmux
ocaptain shell voyage-abc123 ship-0 --raw  # Direct SSH
```

### `ocaptain clone [voyage_id]`

Clone the workspace from local voyage storage.

```bash
ocaptain clone                    # Auto-select if one voyage
ocaptain clone voyage-abc123 -d ./my-copy
```

### `ocaptain sink <voyage_id>`

Destroy voyage VMs and clean up.

```bash
ocaptain sink voyage-abc123       # Destroy ships
ocaptain sink --all -f            # Destroy ALL ocaptain VMs
```

| Option | Description |
|--------|-------------|
| `--all` | Destroy ALL ocaptain VMs |
| `--force, -f` | Skip confirmation |

### `ocaptain doctor`

Check system prerequisites and configuration.

```bash
ocaptain doctor
```

### `ocaptain telemetry-start` / `telemetry-stop`

Start or stop the local OTLP telemetry collector.

```bash
ocaptain telemetry-start
ocaptain telemetry-stop
```

## Configuration

### Environment Variables

| Variable | Required | Description |
|----------|----------|-------------|
| `CLAUDE_CODE_OAUTH_TOKEN` | Yes | Claude Code authentication token |
| `OCAPTAIN_TAILSCALE_OAUTH_SECRET` | Yes | Tailscale OAuth secret for ephemeral keys |
| `OCAPTAIN_PROVIDER` | No | VM provider: `sprites`, `exedev`, or `boxlite` (default: `sprites`) |
| `OCAPTAIN_SPRITES_ORG` | Yes* | sprites.dev organization name (*required for sprites provider) |
| `GH_TOKEN` | No | GitHub token for private repos |
| `OCAPTAIN_DEFAULT_SHIPS` | No | Default ship count (default: `3`) |

### Provider Setup

#### sprites.dev (default)

Install the `sprite` CLI and authenticate:

```bash
# Test connectivity
sprite list -o your-org

# Create a test sprite
sprite create -o your-org test-sprite
```

#### BoxLite (local development)

BoxLite runs hardware-isolated micro-VMs locally with sub-second boot times. Ideal for testing and development without cloud costs.

```bash
# Install with BoxLite support
pip install ocaptain[boxlite]

# Or with uv
uv pip install ocaptain[boxlite]

# Set provider
export OCAPTAIN_PROVIDER="boxlite"
```

**Requirements:**
- macOS 12+ or Linux with KVM
- Tailscale running locally

### Tailscale Setup

1. Create an OAuth client in the Tailscale admin console with `devices:write` scope
2. Set `OCAPTAIN_TAILSCALE_OAUTH_SECRET` to the client secret
3. Ensure your laptop is connected to the tailnet

## Security

### Token Handling

- OAuth tokens passed via environment variables, not command args
- Tailscale ephemeral keys expire automatically when ships are destroyed

### Network Isolation

- Ships communicate only via Tailscale mesh (no public IPs)
- Each ship is a full Linux VM with its own filesystem
- Mutagen sync scoped to specific directories

### Repository Access

- Private repos require `GH_TOKEN`
- Token validity checked before VM provisioning
- Clone failures surface immediately, not after fleet deploys
