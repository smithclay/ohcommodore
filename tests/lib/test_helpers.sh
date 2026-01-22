#!/usr/bin/env bash
# Test helper functions for ohcommodore integration tests

set -euo pipefail

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

# Test counters
TESTS_RUN=0
TESTS_PASSED=0
TESTS_FAILED=0

# Get script directory
TEST_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PROJECT_ROOT="$(cd "$TEST_DIR/.." && pwd)"

# Load .env from project root if present (existing env vars take precedence)
if [[ -f "$PROJECT_ROOT/.env" ]]; then
    while IFS= read -r line || [[ -n "$line" ]]; do
        [[ -z "$line" || "$line" =~ ^[[:space:]]*# ]] && continue
        [[ "$line" != *=* ]] && continue
        key="${line%%=*}"
        [[ -z "$key" ]] && continue
        [[ -n "${!key+x}" ]] && continue
        value="${line#*=}"
        value="${value%\"}"
        value="${value#\"}"
        value="${value%\'}"
        value="${value#\'}"
        export "$key=$value"
    done < "$PROJECT_ROOT/.env"
fi

# Export paths for local deployment
export INIT_PATH="$PROJECT_ROOT/cloudinit/init.sh"
export DOTFILES_PATH="$PROJECT_ROOT/dotfiles"

# Cleanup tracking
CLEANUP_NEEDED=false
SCUTTLE_ON_SUCCESS=true

log_test() {
  echo -e "${YELLOW}[TEST]${NC} $*"
}

log_pass() {
  echo -e "${GREEN}[PASS]${NC} $*"
}

log_fail() {
  echo -e "${RED}[FAIL]${NC} $*"
}

log_info() {
  echo -e "[INFO] $*"
}

# Assert function - checks condition and logs result
assert() {
  local description="$1"
  local condition="$2"

  TESTS_RUN=$((TESTS_RUN + 1))

  if eval "$condition"; then
    TESTS_PASSED=$((TESTS_PASSED + 1))
    log_pass "$description"
    return 0
  else
    TESTS_FAILED=$((TESTS_FAILED + 1))
    log_fail "$description"
    return 1
  fi
}

# Assert command succeeds
assert_success() {
  local description="$1"
  shift
  local cmd="$*"

  TESTS_RUN=$((TESTS_RUN + 1))

  if eval "$cmd" >/dev/null 2>&1; then
    TESTS_PASSED=$((TESTS_PASSED + 1))
    log_pass "$description"
    return 0
  else
    TESTS_FAILED=$((TESTS_FAILED + 1))
    log_fail "$description (command: $cmd)"
    return 1
  fi
}

# Assert command fails
assert_failure() {
  local description="$1"
  shift
  local cmd="$*"

  TESTS_RUN=$((TESTS_RUN + 1))

  if ! eval "$cmd" >/dev/null 2>&1; then
    TESTS_PASSED=$((TESTS_PASSED + 1))
    log_pass "$description"
    return 0
  else
    TESTS_FAILED=$((TESTS_FAILED + 1))
    log_fail "$description (expected failure but succeeded)"
    return 1
  fi
}

# Assert output contains string
assert_contains() {
  local description="$1"
  local output="$2"
  local expected="$3"

  TESTS_RUN=$((TESTS_RUN + 1))

  if echo "$output" | grep -q "$expected"; then
    TESTS_PASSED=$((TESTS_PASSED + 1))
    log_pass "$description"
    return 0
  else
    TESTS_FAILED=$((TESTS_FAILED + 1))
    log_fail "$description (expected '$expected' in output)"
    return 1
  fi
}

# Check required environment
check_required_env() {
  if [[ -z "${GH_TOKEN:-}" ]]; then
    log_fail "GH_TOKEN environment variable required for integration tests"
    exit 1
  fi

  if ! command -v ssh >/dev/null 2>&1; then
    log_fail "ssh command not found"
    exit 1
  fi

  if ! ssh exe.dev whoami >/dev/null 2>&1; then
    log_fail "Cannot connect to exe.dev - check SSH configuration"
    exit 1
  fi

  log_info "Environment checks passed"
}

# Pre-test cleanup - remove any lingering VMs from previous runs
cleanup_lingering_vms() {
  log_info "Cleaning up any lingering VMs..."
  # Clean up any flagship VMs (they start with flagship-)
  for vm in $(ssh exe.dev ls 2>/dev/null | grep -oE 'flagship-[a-zA-Z0-9_-]+' || true); do
    ssh -n exe.dev rm "$vm" 2>/dev/null || true
  done
  # Clean up any ship VMs (they start with ship-)
  for vm in $(ssh exe.dev ls 2>/dev/null | grep -oE 'ship-[a-zA-Z0-9_-]+' || true); do
    ssh -n exe.dev rm "$vm" 2>/dev/null || true
  done
  rm -rf "$HOME/.ohcommodore" 2>/dev/null || true
}

# Cleanup function - scuttles the fleet
cleanup_fleet() {
  if [[ "$CLEANUP_NEEDED" == "true" ]]; then
    log_info "Cleaning up fleet..."
    "$PROJECT_ROOT/ohcommodore" fleet sink --force --scuttle 2>/dev/null || true
    rm -rf "$HOME/.ohcommodore" 2>/dev/null || true
    CLEANUP_NEEDED=false
  fi
}

# Register cleanup on exit (only on success if SCUTTLE_ON_SUCCESS is true)
register_cleanup() {
  CLEANUP_NEEDED=true
  trap 'if [[ $? -eq 0 ]] || [[ "$SCUTTLE_ON_SUCCESS" != "true" ]]; then cleanup_fleet; fi' EXIT
}

# Print test summary
print_summary() {
  echo ""
  echo "================================"
  echo "Test Summary"
  echo "================================"
  echo "Tests run:    $TESTS_RUN"
  echo -e "Tests passed: ${GREEN}$TESTS_PASSED${NC}"
  if [[ $TESTS_FAILED -gt 0 ]]; then
    echo -e "Tests failed: ${RED}$TESTS_FAILED${NC}"
  else
    echo "Tests failed: $TESTS_FAILED"
  fi
  echo "================================"

  if [[ $TESTS_FAILED -gt 0 ]]; then
    return 1
  fi
  return 0
}

# Wait for condition with timeout
wait_for() {
  local description="$1"
  local condition="$2"
  local timeout="${3:-60}"
  local interval="${4:-5}"

  local elapsed=0
  log_info "Waiting for: $description (timeout: ${timeout}s)"

  while [[ $elapsed -lt $timeout ]]; do
    if eval "$condition" 2>/dev/null; then
      log_info "$description - ready"
      return 0
    fi
    sleep "$interval"
    elapsed=$((elapsed + interval))
  done

  log_fail "Timeout waiting for: $description"
  return 1
}

# Assert value is not empty (and not an error message)
assert_not_empty() {
  local description="$1"
  local value="$2"

  TESTS_RUN=$((TESTS_RUN + 1))

  if [[ -n "$value" && "$value" != *"No such file"* ]]; then
    TESTS_PASSED=$((TESTS_PASSED + 1))
    log_pass "$description"
    return 0
  else
    TESTS_FAILED=$((TESTS_FAILED + 1))
    log_fail "$description (empty or missing)"
    return 1
  fi
}

# SSH with standard test options
test_ssh() {
  local dest="$1"
  shift
  ssh -o BatchMode=yes -o StrictHostKeyChecking=accept-new -o ConnectTimeout=10 "$dest" "$@"
}

# SSH with quiet options (no host key warnings)
test_ssh_quiet() {
  local dest="$1"
  shift
  ssh -o BatchMode=yes -o StrictHostKeyChecking=no -o LogLevel=ERROR -o ConnectTimeout=10 "$dest" "$@"
}
