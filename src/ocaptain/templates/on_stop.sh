#!/bin/bash
# Ship stop hook - commits work, marks completion when tasks cleared

set -e

SHIP_ID=$(cat ~/.ocaptain/ship_id 2>/dev/null || echo "unknown")
VOYAGE_ID=$(cat ~/.ocaptain/voyage_id 2>/dev/null || echo "")
TIMESTAMP=$(date -Iseconds)

cd ~/voyage/workspace 2>/dev/null || exit 0

# Commit any uncommitted work
if [[ -n $(git status --porcelain 2>/dev/null) ]]; then
    git add -A
    git commit -m "[$SHIP_ID] Work in progress - $TIMESTAMP" || true
fi

# Check if task list is empty (all tasks cleared = voyage complete)
TASK_DIR="$HOME/.claude/tasks/${VOYAGE_ID}-tasks"
if [[ -n "$VOYAGE_ID" && -d "$TASK_DIR" ]]; then
    TASK_COUNT=$(find "$TASK_DIR" -name "*.json" 2>/dev/null | wc -l | tr -d ' ')

    if [[ "$TASK_COUNT" -eq 0 && ! -f ~/voyage/artifacts/voyage-complete.marker ]]; then
        echo "$TIMESTAMP" > ~/voyage/artifacts/voyage-complete.marker
        echo "" >> ~/voyage/artifacts/progress.txt
        echo "## $TIMESTAMP - Voyage complete ($SHIP_ID)" >> ~/voyage/artifacts/progress.txt
    fi
fi

exit 0
