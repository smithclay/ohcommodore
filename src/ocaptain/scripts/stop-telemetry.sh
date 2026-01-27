#!/bin/bash
set -e

CONFIG_DIR="$HOME/.config/ocaptain"

# Find tailscale binary (handles macOS app bundle)
find_tailscale() {
    if command -v tailscale &>/dev/null; then
        echo "tailscale"
    elif [ -x "/Applications/Tailscale.app/Contents/MacOS/Tailscale" ]; then
        echo "/Applications/Tailscale.app/Contents/MacOS/Tailscale"
    else
        echo "tailscale"  # Let it fail naturally if not found
    fi
}
TAILSCALE=$(find_tailscale)

echo "Stopping ocaptain telemetry collector..."

# Stop tailscale serve
$TAILSCALE serve --tcp 4318 off 2>/dev/null || true

# Stop otlp2parquet
if [ -f "$CONFIG_DIR/otlp2parquet.pid" ]; then
    kill "$(cat "$CONFIG_DIR/otlp2parquet.pid")" 2>/dev/null || true
    rm "$CONFIG_DIR/otlp2parquet.pid"
fi

echo "Telemetry collector stopped."
