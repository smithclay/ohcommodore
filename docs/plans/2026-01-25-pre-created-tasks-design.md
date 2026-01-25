# Pre-Created Task System for Voyage Plans

## Problem

Claude Code ships ignore task creation instructions and proceed directly to implementation, even when system prompts explicitly require task creation first. The task directory remains empty and voyage status stays in "planning" forever.

See: `TASK_CREATION_ISSUE.md`

## Solution

Pre-create tasks in the voyage-plan skill before ships launch. Ships only need to claim and execute - no task creation required.

## Artifact Structure

The voyage-plan skill outputs four artifacts to `.claude/plans/{feature-slug}/`:

```
.claude/plans/{feature-slug}/
├── spec.md              # Design document - what to build and why
├── verify.sh            # Exit criteria - deterministic pass/fail script
├── voyage.json          # Configuration with recommended ship count
└── tasks/
    ├── 1.json           # Pre-created task files
    ├── 2.json
    └── ...
```

### spec.md - Design Document

Human-readable specification containing:
- Objective and context
- Architecture decisions
- Key abstractions and data flow
- Error handling approach
- Testing strategy
- Exit criteria (commands that must pass)

No task numbers or execution ordering - that belongs in `tasks/`.

### verify.sh - Exit Criteria Script

Executable script returning 0 on success, non-zero on failure:

```bash
#!/bin/bash
set -e
cd ~/voyage/workspace
npm run typecheck
npm run lint
npm run test
echo "All exit criteria passed!"
```

Ships run this before declaring the voyage complete.

### voyage.json - Configuration

```json
{
  "recommended_ships": 3,
  "max_parallel_width": 3,
  "total_tasks": 12,
  "repo": "owner/repo",
  "created": "2026-01-25T12:00:00Z"
}
```

The `recommended_ships` value is computed by analyzing the task dependency DAG.

### tasks/*.json - Pre-Created Tasks

Each task file follows Claude Code's expected format:

```json
{
  "id": "1",
  "title": "Implement JWT token generation",
  "description": "Create token generation in auth/tokens.py\n\nFiles: auth/tokens.py\n\nAcceptance: Token generation works with tests passing",
  "status": "pending",
  "blockedBy": [],
  "blocks": ["4", "5"],
  "created": "2026-01-25T12:00:00Z",
  "updated": "2026-01-25T12:00:00Z",
  "metadata": {
    "voyage": "voyage-abc123",
    "ship": "ship-0",
    "repo": "owner/repo-name"
  }
}
```

Metadata fields:
- `voyage` - Voyage ID for traceability
- `ship` - Pre-assigned ship (round-robin based on dependencies)
- `repo` - Repository being modified

## DAG Analysis Script

The skill uses a Python script to compute optimal ship count:

**`.claude/skills/voyage-plan/dag_analyzer.py`:**

```python
#!/usr/bin/env python3
"""Analyze task DAG and compute max parallel width."""
import json
import sys
from pathlib import Path

def load_tasks(tasks_dir: Path) -> dict:
    tasks = {}
    for f in tasks_dir.glob("*.json"):
        task = json.loads(f.read_text())
        tasks[task["id"]] = task
    return tasks

def compute_levels(tasks: dict) -> dict[str, int]:
    """Assign each task to a level (0 = no deps)."""
    levels = {}
    def get_level(tid):
        if tid in levels:
            return levels[tid]
        blocked_by = tasks[tid].get("blockedBy", [])
        if not blocked_by:
            levels[tid] = 0
        else:
            levels[tid] = 1 + max(get_level(b) for b in blocked_by)
        return levels[tid]
    for tid in tasks:
        get_level(tid)
    return levels

def max_parallel_width(levels: dict[str, int]) -> int:
    """Count max tasks at any single level."""
    from collections import Counter
    counts = Counter(levels.values())
    return max(counts.values()) if counts else 0

if __name__ == "__main__":
    tasks_dir = Path(sys.argv[1])
    tasks = load_tasks(tasks_dir)
    levels = compute_levels(tasks)
    width = max_parallel_width(levels)
    print(json.dumps({
        "total_tasks": len(tasks),
        "max_parallel_width": width,
        "recommended_ships": width,
        "levels": {tid: lvl for tid, lvl in sorted(levels.items(), key=lambda x: x[1])}
    }))
```

Usage: `python3 dag_analyzer.py .claude/plans/my-feature/tasks/`

## CLI Integration

### Current Flow
```
ocaptain sail -r owner/repo "prompt"
```

### New Flow
```
ocaptain sail --plan .claude/plans/user-auth/
```

The CLI:
1. Validates all four artifacts exist
2. Reads `voyage.json` for recommended ship count (overridable with `--ships`)
3. Creates storage VM
4. Copies artifacts:
   - `spec.md` → `~/voyage/artifacts/spec.md`
   - `verify.sh` → `~/voyage/artifacts/verify.sh` (chmod +x)
   - `tasks/*.json` → `~/.claude/tasks/{task_list_id}/`
5. Updates task metadata with actual voyage ID
6. Writes simplified ship prompt
7. Bootstraps ships

### Simplified Ship Prompt

```markdown
You are ship-N on voyage-xyz.

Run TaskList(). Find tasks assigned to you (metadata.ship == "ship-N").
Claim with TaskUpdate(status="in_progress"). Do the work. Mark complete.

Before stopping, run ~/voyage/artifacts/verify.sh.
```

No task creation instructions needed - tasks already exist.

## Skill Workflow

Updated voyage-plan skill steps:

1. **Gather inputs**
   - Feature description from user
   - Repo (owner/repo format)

2. **Explore codebase**
   - Find source dirs, test infrastructure, build tools
   - Identify patterns and conventions

3. **Generate spec.md**
   - Write design document with architecture decisions
   - Include exit criteria commands

4. **Generate verify.sh**
   - Extract exit criteria commands from spec
   - Create executable script

5. **Generate tasks/**
   - Break spec into 10-30 concrete tasks
   - Set dependencies (blockedBy/blocks)
   - Assign ships round-robin respecting dependencies

6. **Run DAG analyzer**
   - Compute max parallel width
   - Generate voyage.json with recommended_ships

7. **Report output**
   - Plan directory location
   - Task count and ship recommendation
   - Ready for `ocaptain sail --plan`

## Migration

- Existing `plan.md` format deprecated in favor of `spec.md`
- CLI gains `--plan` flag pointing to plan directory
- Ship prompt template simplified
- `render_verify_script` in voyage.py remains compatible

## Files to Modify

1. `.claude/skills/voyage-plan/SKILL.md` - Updated skill instructions
2. `.claude/skills/voyage-plan/dag_analyzer.py` - New DAG analysis script
3. `src/ocaptain/voyage.py` - Add `--plan` directory support
4. `src/ocaptain/cli.py` - Add `--plan` flag to sail command
5. `src/ocaptain/templates/ship_prompt.md` - Simplify for pre-created tasks
