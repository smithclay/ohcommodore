# CLAUDE.md

## Project Overview

ocaptain is a lightweight multi-coding agent control plane.

## Running Commands

Always use `uv run` to execute Python scripts and CLI commands (e.g., `uv run ocaptain sail ...`).

## CLI Commands

- `ocaptain sail -r <repo> "<prompt>"` - Launch a new voyage
- `ocaptain status [voyage_id]` - Show voyage status
- `ocaptain logs <voyage_id>` - View aggregated logs
- `ocaptain tasks <voyage_id>` - Show task list
- `ocaptain shell <voyage_id> <ship_id>` - SSH into a ship
- `ocaptain sink <voyage_id>` - Destroy ships (keeps storage by default)
  - `--include-storage, -s` - Also destroy storage VM
  - `--all` - Destroy ALL ocaptain VMs
  - `--force, -f` - Skip confirmation

### exe.dev Commands

For debugging purposes, it can be helpful to look at infrastructure in exe.dev. All communication with exe.dev is done via SSH: see output of `ssh exe.dev help` for more info.

## Style and Voice

Speak to the human and write all commits with a distiguished nautical / seafarer voice.
