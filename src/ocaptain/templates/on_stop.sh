#!/bin/bash
# Ship stop hook - commits work and exits cleanly

set -e

SHIP_ID=$(cat ~/.ocaptain/ship_id 2>/dev/null || echo "unknown")
TIMESTAMP=$(date -Iseconds)

# Commit any uncommitted work
cd ~/voyage/workspace 2>/dev/null || exit 0

if [[ -n $(git status --porcelain 2>/dev/null) ]]; then
    git add -A
    git commit -m "[$SHIP_ID] Final commit on exit - $TIMESTAMP" || true
fi

# Append to progress.txt
if [[ -f ~/voyage/artifacts/progress.txt ]]; then
    echo "" >> ~/voyage/artifacts/progress.txt
    echo "## $TIMESTAMP - $SHIP_ID exited" >> ~/voyage/artifacts/progress.txt
    echo "" >> ~/voyage/artifacts/progress.txt
fi

exit 0
