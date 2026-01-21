# Container-Model Ships

## Overview

Ships adopt a Docker/Kubernetes-like model where multiple instances of the same repo can run simultaneously. Each ship gets a unique ID, enabling parallel work, isolation, and scaling.

## Naming Convention

Ships are identified by `{repo}-{6char}` where:
- `{repo}` is the repository name (e.g., `ohcommodore`)
- `{6char}` is a random hex suffix (e.g., `a1b2c3`)

Full example: `ohcommodore-a1b2c3`

This becomes:
- VM name at exe.dev: `ship-ohcommodore-a1b2c3`
- Messaging identity: `captain@ohcommodore-a1b2c3`
- Lookup key for commands

## Storage

Ships are registered in a DuckDB table on the flagship:

```sql
CREATE TABLE IF NOT EXISTS ships (
    id TEXT PRIMARY KEY,            -- 'ohcommodore-a1b2c3'
    repo TEXT,                      -- 'owner/ohcommodore' (nullable for no-repo ships)
    ssh_dest TEXT NOT NULL,         -- 'user@vm123.runexe.dev:2200'
    status TEXT DEFAULT 'creating', -- 'creating', 'ready', 'destroying'
    created_at TEXT DEFAULT (datetime('now'))
);
```

### Status Lifecycle

```
creating  -->  ready  -->  destroying  -->  (row deleted)
    |                           |
    +-- VM creation starts      +-- destroy command issued
                |
                +-- cloudinit completes, ship reachable
```

## Command Changes

### ship create

```bash
GH_TOKEN=... ./ohcommodore ship create owner/repo
```

Creates a new ship instance with unique ID. Running multiple times creates multiple instances:

```
$ GH_TOKEN=... ./ohcommodore ship create owner/ohcommodore
Created ship: ohcommodore-a1b2c3

$ GH_TOKEN=... ./ohcommodore ship create owner/ohcommodore
Created ship: ohcommodore-x7y8z9
```

### fleet status

Shows all ships with their full IDs:

```
SHIP                    REPO               SSH_DEST                         STATUS
ohcommodore-a1b2c3     owner/ohcommodore  user@vm123.runexe.dev:2200       ready
ohcommodore-x7y8z9     owner/ohcommodore  user@vm456.runexe.dev:2200       ready
other-repo-d4e5f6      owner/other-repo   user@vm789.runexe.dev:2200       creating
```

### ship ssh / ship destroy

Both commands use prefix matching:

```bash
# Unambiguous prefix - works
./ohcommodore ship ssh ohcommodore-a1

# Ambiguous prefix - errors with list of matches
./ohcommodore ship ssh ohcommodore
# Error: Multiple ships match 'ohcommodore':
#   ohcommodore-a1b2c3
#   ohcommodore-x7y8z9
```

Same behavior for `ship destroy`.

## Messaging

Messages use full ship ID for unambiguous routing:

```bash
ohcommodore inbox send captain@ohcommodore-a1b2c3 "cargo test"
```

## Migration

This is a breaking change. No backward compatibility with existing `ship-{repo}` naming.

Users should:
1. `./ohcommodore fleet sink --scuttle` to destroy existing fleet
2. `./ohcommodore init` to recreate flagship with new schema
3. Create ships with new naming convention

## Implementation Notes

### ID Generation

```bash
generate_ship_id() {
    local repo_name="$1"
    local suffix
    suffix=$(openssl rand -hex 3)  # 6 chars
    echo "${repo_name}-${suffix}"
}
```

### Prefix Matching

```bash
resolve_ship_prefix() {
    local prefix="$1"
    local matches
    matches=$(duckdb "$DB" -csv -noheader "SELECT id FROM ships WHERE id LIKE '${prefix}%'")
    local count
    count=$(echo "$matches" | grep -c . || echo 0)

    case "$count" in
        0) die "No ship matches '$prefix'" ;;
        1) echo "$matches" ;;
        *) die "Multiple ships match '$prefix':\n$matches" ;;
    esac
}
```
