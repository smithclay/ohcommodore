#!/usr/bin/env bash
# Integration test runner for ohcommodore
#
# Usage:
#   ./tests/run_integration.sh              # Run messaging test
#   ./tests/run_integration.sh --setup      # Create fleet first, then test
#
# Prerequisites:
#   - SSH access to exe.dev
#   - For --setup: GH_TOKEN environment variable

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

# Load .env if present
if [[ -f "$PROJECT_ROOT/.env" ]]; then
  set -a; . "$PROJECT_ROOT/.env"; set +a
fi

export INIT_PATH="$PROJECT_ROOT/cloudinit/init.sh"
export DOTFILES_PATH="$PROJECT_ROOT/dotfiles"

RED='\033[0;31m'
GREEN='\033[0;32m'
CYAN='\033[0;36m'
NC='\033[0m'

setup_fleet() {
  echo -e "${CYAN}Setting up fleet...${NC}"

  [[ -n "${GH_TOKEN:-}" ]] || { echo "GH_TOKEN required for setup"; exit 1; }

  # Clean existing
  if [[ -f "$HOME/.ohcommodore/config.json" ]]; then
    echo "Cleaning existing fleet..."
    "$PROJECT_ROOT/ohcommodore" fleet sink --force --scuttle 2>/dev/null || true
    rm -rf "$HOME/.ohcommodore"
  fi

  # Create flagship
  echo "Creating flagship..."
  "$PROJECT_ROOT/ohcommodore" init

  # Create one ship
  echo "Creating ship..."
  "$PROJECT_ROOT/ohcommodore" ship create test-ship

  echo -e "${GREEN}Fleet ready${NC}"
  "$PROJECT_ROOT/ohcommodore" fleet status
}

run_test() {
  echo ""
  echo -e "${CYAN}Running messaging test...${NC}"
  echo ""

  if bash "$SCRIPT_DIR/integration/test_messaging.sh"; then
    echo ""
    echo -e "${GREEN}All tests passed!${NC}"
  else
    echo ""
    echo -e "${RED}Tests failed${NC}"
    exit 1
  fi
}

main() {
  # Check exe.dev access
  echo -e "${CYAN}Checking exe.dev access...${NC}"
  ssh exe.dev whoami >/dev/null 2>&1 || { echo "Cannot connect to exe.dev"; exit 1; }
  echo -e "${GREEN}OK${NC}"

  case "${1:-}" in
    --setup)
      setup_fleet
      run_test
      ;;
    *)
      run_test
      ;;
  esac
}

main "$@"
