# ohcommodore

Lightweight multi-coding agent control plane built on exe.dev VMs.

## Architecture

```
┌─────────────────┐
│  Local Machine  │
│   (ohcommodore) │
└────────┬────────┘
         │ SSH
         ▼
┌─────────────────────────────────────────┐
│           Flagship (exe.dev VM)         │
│  ┌─────────────┐  ┌──────────────────┐  │
│  │ Fleet DB    │  │ v2 Queue System  │  │
│  │ (DuckDB)    │  │ ~/.ohcommodore/  │  │
│  └─────────────┘  └──────────────────┘  │
└────────┬────────────────────────────────┘
         │ SSH + SCP (message files)
         ▼
┌─────────────────┐     ┌─────────────────┐
│   Ship (VM 1)   │     │   Ship (VM 2)   │
│  repo: foo/bar  │ ... │  repo: baz/qux  │
│  v2 Queue       │     │  v2 Queue       │
└─────────────────┘     └─────────────────┘
```

## Quick Start

```bash
# Bootstrap flagship (requires GitHub PAT)
GH_TOKEN=ghp_xxx ./ohcommodore init

# Create a ship for a repository
GH_TOKEN=ghp_xxx ./ohcommodore ship create owner/repo

# Check fleet status
./ohcommodore fleet status

# SSH into a ship
./ohcommodore ship ssh reponame

# Send command to a ship
./ohcommodore inbox send captain@ship-hostname "cargo test"

# Check queue status
./ohcommodore queue status
```

## Inbox Commands

```bash
./ohcommodore inbox list
./ohcommodore inbox send captain@ship-hostname "cargo test"
./ohcommodore inbox read <id>
./ohcommodore inbox identity
```

## v2 Messaging

Messages are NDJSON files delivered via SCP with atomic rename:

1. Sender creates message file locally
2. SCP to recipient's `.incoming/` staging directory
3. Atomic rename publishes to `inbound/`
4. Daemon claims via rename to `.claimed.*`
5. Execute, emit result, ack (delete) or deadletter

No remote DuckDB writes. Single writer per node.

All ship state (fleet registry, config, messages) lives in `~/.ohcommodore/ns/<namespace>/data.duckdb`.
