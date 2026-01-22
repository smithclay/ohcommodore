#!/usr/bin/env bash
# Integration test runner for ohcommodore
#
# Usage:
#   ./tests/run_integration.sh           # Run all tests (loads .env automatically)
#   ./tests/run_integration.sh fleet      # Run fleet lifecycle test
#   ./tests/run_integration.sh messaging  # Run messaging test
#
# Environment (auto-loaded from .env if present):
#   GH_TOKEN    - Required: GitHub PAT for repo access
#   TEST_REPO   - Optional: Repository to use for ship creation (default: smithclay/ohcommodore)
#   KEEP_FLEET  - Optional: Set to "true" to skip cleanup on success (for debugging)

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# Source test helpers (loads .env and sets PROJECT_ROOT)
source "$SCRIPT_DIR/lib/test_helpers.sh"

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
CYAN='\033[0;36m'
NC='\033[0m'

usage() {
  cat <<EOF
ohcommodore Integration Test Runner

Usage:
  $0 [test_name]

Tests:
  fleet      Run fleet lifecycle test (init, ship create/destroy, scuttle)
  messaging  Run messaging test (flagship-to-ship and ship-to-ship)
  all        Run all tests (default)

Environment Variables:
  GH_TOKEN    Required: GitHub PAT for repo access
  TEST_REPO   Optional: Repository for ship creation (default: smithclay/ohcommodore)
  KEEP_FLEET  Optional: Set to "true" to skip cleanup on success

Examples:
  GH_TOKEN=ghp_xxx $0
  GH_TOKEN=ghp_xxx $0 fleet
  GH_TOKEN=ghp_xxx TEST_REPO=myorg/myrepo $0 messaging
  GH_TOKEN=ghp_xxx KEEP_FLEET=true $0 fleet
EOF
}

check_env() {
  if [[ -z "${GH_TOKEN:-}" ]]; then
    echo -e "${RED}ERROR: GH_TOKEN environment variable is required${NC}"
    echo ""
    usage
    exit 1
  fi

  # Verify exe.dev access
  echo -e "${CYAN}Checking exe.dev access...${NC}"
  if ! ssh exe.dev whoami >/dev/null 2>&1; then
    echo -e "${RED}ERROR: Cannot connect to exe.dev${NC}"
    echo "Please ensure you have SSH access configured for exe.dev"
    exit 1
  fi
  echo -e "${GREEN}exe.dev access OK${NC}"

  # Verify local paths
  if [[ ! -f "$PROJECT_ROOT/cloudinit/init.sh" ]]; then
    echo -e "${RED}ERROR: cloudinit/init.sh not found${NC}"
    exit 1
  fi

  if [[ ! -d "$PROJECT_ROOT/dotfiles" ]]; then
    echo -e "${RED}ERROR: dotfiles/ directory not found${NC}"
    exit 1
  fi

  echo -e "${GREEN}Local paths OK${NC}"
}

run_test() {
  local test_name="$1"
  local test_file="$SCRIPT_DIR/integration/test_${test_name}.sh"

  if [[ ! -f "$test_file" ]]; then
    echo -e "${RED}ERROR: Test file not found: $test_file${NC}"
    return 1
  fi

  echo ""
  echo -e "${CYAN}========================================${NC}"
  echo -e "${CYAN}Running: $test_name${NC}"
  echo -e "${CYAN}========================================${NC}"
  echo ""

  chmod +x "$test_file"

  # Export KEEP_FLEET handling
  if [[ "${KEEP_FLEET:-}" == "true" ]]; then
    export SCUTTLE_ON_SUCCESS=false
  fi

  if bash "$test_file"; then
    echo -e "${GREEN}Test $test_name: PASSED${NC}"
    return 0
  else
    echo -e "${RED}Test $test_name: FAILED${NC}"
    return 1
  fi
}

main() {
  local test_to_run="${1:-all}"

  case "$test_to_run" in
    -h|--help|help)
      usage
      exit 0
      ;;
    fleet|fleet_lifecycle)
      check_env
      run_test "fleet_lifecycle"
      ;;
    messaging)
      check_env
      run_test "messaging"
      ;;
    all)
      check_env
      local failed=0

      # Run tests sequentially (each creates its own fleet)
      # For a full integration test, run messaging test which includes fleet setup
      run_test "messaging" || failed=1

      echo ""
      echo -e "${CYAN}========================================${NC}"
      if [[ $failed -eq 0 ]]; then
        echo -e "${GREEN}All tests passed!${NC}"
      else
        echo -e "${RED}Some tests failed${NC}"
        exit 1
      fi
      ;;
    *)
      echo -e "${RED}Unknown test: $test_to_run${NC}"
      usage
      exit 1
      ;;
  esac
}

main "$@"
