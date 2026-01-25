#!/bin/bash
set -e

if command -v uv &> /dev/null; then
    uv tool install ocaptain
elif command -v pipx &> /dev/null; then
    pipx install ocaptain
else
    echo "Installing uv first..."
    curl -LsSf https://astral.sh/uv/install.sh | sh
    uv tool install ocaptain
fi

echo "ocaptain installed! Run 'ocaptain --help' to get started."
