#!/bin/bash
set -e

CONFIG_DIR="$HOME/.config/ocaptain"
VOYAGES_DIR="$HOME/voyages"

# Find tailscale binary (handles macOS app bundle)
find_tailscale() {
    if command -v tailscale &>/dev/null; then
        echo "tailscale"
    elif [ -x "/Applications/Tailscale.app/Contents/MacOS/Tailscale" ]; then
        echo "/Applications/Tailscale.app/Contents/MacOS/Tailscale"
    else
        echo "ERROR: tailscale not found" >&2
        exit 1
    fi
}
TAILSCALE=$(find_tailscale)

echo "Starting ocaptain telemetry collector..."

# Create directories
mkdir -p "$CONFIG_DIR" "$VOYAGES_DIR/telemetry"

# Write otlp2parquet config
cat > "$CONFIG_DIR/otlp2parquet.toml" << EOF
[server]
listen_addr = "127.0.0.1:4318"
log_level = "info"

[storage]
backend = "fs"

[storage.fs]
path = "$VOYAGES_DIR/telemetry"

[batch]
max_rows = 10000
max_bytes = 67108864
max_age_secs = 30
enabled = true
EOF

# Check if already running
if [ -f "$CONFIG_DIR/otlp2parquet.pid" ]; then
    if kill -0 "$(cat "$CONFIG_DIR/otlp2parquet.pid")" 2>/dev/null; then
        echo "otlp2parquet already running"
        exit 0
    fi
fi

# Start otlp2parquet
echo "Starting otlp2parquet on 127.0.0.1:4318..."
nohup otlp2parquet --config "$CONFIG_DIR/otlp2parquet.toml" \
    > "$VOYAGES_DIR/telemetry/collector.log" 2>&1 &
echo $! > "$CONFIG_DIR/otlp2parquet.pid"

# Expose via Tailscale
echo "Exposing OTLP via Tailscale..."
$TAILSCALE serve --bg --tcp 4318 tcp://127.0.0.1:4318

# Get tailscale IP
TAILSCALE_IP=$($TAILSCALE ip -4)
echo ""
echo "=== Telemetry collector ready ==="
echo "OTLP endpoint: http://$TAILSCALE_IP:4318"
echo ""
