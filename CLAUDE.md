# CLAUDE.md

## Project Overview

ocaptain is a lightweight multi-coding agent control plane using Tailscale mesh networking and Mutagen file sync. Ships (VMs running Claude Code) can be provisioned on multiple backends:
- **sprites.dev** — Cloud VMs (default)
- **exe.dev** — Alternative cloud provider
- **boxlite** — Local micro-VMs for development

## Running Commands

Always use `uv run` to execute Python scripts and CLI commands (e.g., `uv run ocaptain sail ...`).

## CLI Commands

- `ocaptain sail <plan-dir>` - Launch a voyage from a plan directory
  - `--ships, -n` - Override ship count
  - `--no-telemetry` - Disable OTLP telemetry
- `ocaptain status [voyage_id]` - Show voyage status
- `ocaptain logs <voyage_id>` - View aggregated logs
- `ocaptain tasks <voyage_id>` - Show task list
- `ocaptain shell <voyage_id> [ship_id]` - Attach to ship's tmux session
  - `--raw, -r` - Direct SSH instead of tmux
- `ocaptain clone [voyage_id]` - Clone workspace from local voyage storage
- `ocaptain sink <voyage_id>` - Destroy voyage VMs
  - `--all` - Destroy ALL ocaptain VMs
  - `--force, -f` - Skip confirmation
- `ocaptain doctor` - Check system prerequisites
- `ocaptain telemetry-start` - Start OTLP collector
- `ocaptain telemetry-stop` - Stop OTLP collector

### Provider Commands

Ships can run on sprites.dev, exe.dev, or locally via boxlite.

**sprites.dev** (use `sprite` CLI for debugging):
```bash
sprite list -o <org>           # List sprites
sprite exec -o <org> -s <name> # Run command on sprite
```

**boxlite** (local micro-VMs):
```bash
# Requires: pip install ocaptain[boxlite]
export OCAPTAIN_PROVIDER="boxlite"
```

## Architecture

- **Local Storage**: Voyages stored at `~/voyages/<voyage-id>/`
- **Tailscale Mesh**: Ships join tailnet with ephemeral keys
- **Mutagen Sync**: Two-way file sync between laptop and ships
- **Autonomous Ships**: Claude runs in tmux on each ship

## Style and Voice

Speak to the human and write all commits with a distinguished nautical / seafarer voice.
