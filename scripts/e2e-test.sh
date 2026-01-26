#!/bin/bash
set -euo pipefail

# E2E test for ocaptain local storage mode
# Requires: OCAPTAIN_TAILSCALE_OAUTH_SECRET, CLAUDE_CODE_OAUTH_TOKEN, GH_TOKEN

echo "=== ocaptain E2E Test ==="

# Check prerequisites
check_prereqs() {
    echo "Checking prerequisites..."

    command -v tailscale >/dev/null || { echo "ERROR: tailscale not installed"; exit 1; }
    command -v mutagen >/dev/null || { echo "ERROR: mutagen not installed"; exit 1; }
    command -v otlp2parquet >/dev/null || { echo "ERROR: otlp2parquet not installed"; exit 1; }

    [[ -n "${OCAPTAIN_TAILSCALE_OAUTH_SECRET:-}" ]] || { echo "ERROR: OCAPTAIN_TAILSCALE_OAUTH_SECRET not set"; exit 1; }
    [[ -n "${CLAUDE_CODE_OAUTH_TOKEN:-}" ]] || { echo "ERROR: CLAUDE_CODE_OAUTH_TOKEN not set"; exit 1; }

    # Check tailscale is running and laptop is tagged
    tailscale status >/dev/null || { echo "ERROR: tailscale not running"; exit 1; }

    echo "✓ Prerequisites OK"
}

# Start telemetry
start_telemetry() {
    echo "Starting telemetry collector..."
    ./scripts/start-telemetry.sh
    sleep 2

    # Verify it's running
    tailscale serve status | grep -q "4318" || { echo "ERROR: telemetry not exposed"; exit 1; }
    echo "✓ Telemetry collector running"
}

# Launch test voyage
launch_voyage() {
    echo "Launching test voyage (1 ship)..."

    # Use a simple test plan or create minimal one
    VOYAGE_OUTPUT=$(uv run ocaptain sail examples/generated-plans/multilingual-readme -n 1 2>&1)
    VOYAGE_ID=$(echo "$VOYAGE_OUTPUT" | grep -oE 'voyage-[a-f0-9]+' | head -1)

    if [[ -z "$VOYAGE_ID" ]]; then
        echo "ERROR: Failed to launch voyage"
        echo "$VOYAGE_OUTPUT"
        exit 1
    fi

    echo "✓ Voyage launched: $VOYAGE_ID"
    echo "$VOYAGE_ID"
}

# Verify voyage setup
verify_voyage() {
    local voyage_id=$1
    echo "Verifying voyage setup..."

    # Check local directory
    [[ -d "$HOME/voyages/$voyage_id" ]] || { echo "ERROR: Local voyage dir not created"; return 1; }
    echo "  ✓ Local directory exists"

    # Check Tailscale status (ship should be visible)
    sleep 5  # Give ship time to join
    if tailscale status | grep -q "$voyage_id"; then
        echo "  ✓ Ship joined Tailscale"
    else
        echo "  ⚠ Ship not visible in tailscale status (may still be joining)"
    fi

    # Check Mutagen sync
    if mutagen sync list | grep -q "$voyage_id"; then
        echo "  ✓ Mutagen sync sessions active"
    else
        echo "  ERROR: Mutagen sync not found"
        return 1
    fi

    # Check tmux session
    if tmux has-session -t "$voyage_id" 2>/dev/null; then
        echo "  ✓ tmux session running"
    else
        echo "  ERROR: tmux session not found"
        return 1
    fi

    echo "✓ Voyage verification passed"
}

# Cleanup
cleanup() {
    local voyage_id=$1
    echo "Cleaning up..."

    uv run ocaptain sink "$voyage_id" --force 2>/dev/null || true
    ./scripts/stop-telemetry.sh 2>/dev/null || true

    # Verify ephemeral cleanup (ship should be removed)
    sleep 3
    if tailscale status | grep -q "$voyage_id"; then
        echo "  ⚠ Ship still in tailscale (ephemeral cleanup may be delayed)"
    else
        echo "  ✓ Ship removed from Tailscale (ephemeral)"
    fi

    echo "✓ Cleanup complete"
}

# Main
main() {
    local voyage_id=""

    trap 'cleanup "${voyage_id:-}"' EXIT

    check_prereqs
    start_telemetry
    voyage_id=$(launch_voyage)
    verify_voyage "$voyage_id"

    echo ""
    echo "=== E2E Test PASSED ==="
}

main "$@"
