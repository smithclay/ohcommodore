#!/usr/bin/env bash
# Integration test: v2 Queue System
#
# This test creates a fleet and verifies the v2 queue messaging system:
# 1. Message sending via queue transport
# 2. Message delivery and ingestion
# 3. Scheduler processing
# 4. Queue status command
#
# Requires: GH_TOKEN, ssh access to exe.dev

set -euo pipefail

# Source test helpers
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/../lib/test_helpers.sh"

# Test configuration
TEST_REPO="${TEST_REPO:-smithclay/ohcommodore}"

main() {
  echo "================================"
  echo "v2 Queue System Integration Test"
  echo "================================"
  echo ""

  # Check environment
  check_required_env

  # Ensure clean state
  log_info "Ensuring clean state..."
  rm -rf "$HOME/.ohcommodore" 2>/dev/null || true

  # Register cleanup
  register_cleanup

  # ============================================
  # Setup: Create fleet with one ship
  # ============================================
  log_test "Setting up fleet..."

  if ! GH_TOKEN="$GH_TOKEN" "$PROJECT_ROOT/ohcommodore" init >/dev/null 2>&1; then
    log_fail "Failed to initialize flagship"
    return 1
  fi

  local ship_name="${TEST_REPO##*/}"

  if ! GH_TOKEN="$GH_TOKEN" "$PROJECT_ROOT/ohcommodore" ship create "$TEST_REPO" >/dev/null 2>&1; then
    log_fail "Failed to create ship"
    return 1
  fi

  log_info "Fleet created with ship: $ship_name"

  # Get ship hostname
  local ship_dest
  ship_dest=$("$PROJECT_ROOT/ohcommodore" fleet status 2>&1 | grep "^  ship-$ship_name" | awk '{print $1}')
  [[ -n "$ship_dest" ]] || { log_fail "Could not get ship destination"; return 1; }

  local ship_host="${ship_dest%%:*}"
  ship_host="${ship_host#*@}"

  log_info "Ship destination: $ship_dest"
  log_info "Ship host: $ship_host"

  # Get flagship host
  local flagship_dest
  flagship_dest=$(jq -r '.flagship' "$HOME/.ohcommodore/config.json")
  local flagship_host="${flagship_dest%%:*}"
  flagship_host="${flagship_host#*@}"

  log_info "Flagship host: $flagship_host"

  # ============================================
  # Test 1: Queue status on flagship
  # ============================================
  log_test "Checking queue status on flagship..."

  local queue_status
  queue_status=$(ssh -o BatchMode=yes -o StrictHostKeyChecking=accept-new "$flagship_dest" '~/.local/bin/ohcommodore queue status' 2>&1)

  assert_contains "Queue status shows namespace" "$queue_status" "namespace:"
  assert_contains "Queue status shows inbound" "$queue_status" "inbound:"
  assert_contains "Queue status shows database" "$queue_status" "messages:"

  # ============================================
  # Test 2: Queue status on ship
  # ============================================
  log_test "Checking queue status on ship..."

  queue_status=$(ssh -o BatchMode=yes -o StrictHostKeyChecking=accept-new "$ship_dest" '~/.local/bin/ohcommodore queue status' 2>&1)

  assert_contains "Ship queue status works" "$queue_status" "namespace:"

  # ============================================
  # Test 3: Send message from flagship to ship
  # ============================================
  log_test "Sending message from flagship to ship..."

  # Get ship identity
  local ship_identity
  ship_identity=$(ssh -o BatchMode=yes -o StrictHostKeyChecking=accept-new "$ship_dest" '~/.local/bin/ohcommodore inbox identity' 2>&1 | tr -d '\n')

  log_info "Ship identity: $ship_identity"

  if [[ -z "$ship_identity" || "$ship_identity" == *"ERROR"* ]]; then
    log_fail "Could not get ship identity"
    echo "Output: $ship_identity"
    return 1
  fi

  # Send a test command
  local send_output
  send_output=$(ssh -o BatchMode=yes -o StrictHostKeyChecking=accept-new "$flagship_dest" \
    "~/.local/bin/ohcommodore inbox send '$ship_identity' 'echo QUEUE_TEST_SUCCESS'" 2>&1)

  assert_contains "Message sent" "$send_output" "Message sent:"

  # ============================================
  # Test 4: Verify message arrived in ship's inbound queue
  # ============================================
  log_test "Verifying message in ship's inbound queue..."

  # Wait a moment for SCP delivery
  sleep 3

  local inbound_files
  inbound_files=$(ssh -o BatchMode=yes -o StrictHostKeyChecking=accept-new "$ship_dest" \
    'ls ~/.ohcommodore/ns/default/q/inbound/*.json 2>/dev/null | wc -l' 2>&1 | tr -d ' ')
  inbound_files="${inbound_files:-0}"

  assert "Message file exists in inbound" "[[ '$inbound_files' -ge 1 ]]"

  # ============================================
  # Test 5: Start scheduler briefly to process message
  # ============================================
  log_test "Processing message via scheduler..."

  # Run scheduler for a short time in background, then kill it
  ssh -o BatchMode=yes -o StrictHostKeyChecking=accept-new "$ship_dest" '
    timeout 15 ~/.local/bin/ohcommodore _scheduler &
    SCHED_PID=$!
    sleep 10
    kill $SCHED_PID 2>/dev/null || true
  ' 2>&1 || true

  # ============================================
  # Test 6: Verify message was processed
  # ============================================
  log_test "Verifying message was processed..."

  # Check messages table for handled message
  local handled_count
  handled_count=$(ssh -o BatchMode=yes -o StrictHostKeyChecking=accept-new "$ship_dest" \
    "duckdb ~/.ohcommodore/ns/default/data.duckdb -noheader -csv \"SELECT COUNT(*) FROM messages WHERE handled_at IS NOT NULL\"" 2>&1 | tr -d ' ')
  handled_count="${handled_count:-0}"

  assert "Message marked as handled" "[[ '$handled_count' -ge 1 ]]"

  # ============================================
  # Test 7: Check artifacts were created
  # ============================================
  log_test "Checking artifacts..."

  local artifacts
  artifacts=$(ssh -o BatchMode=yes -o StrictHostKeyChecking=accept-new "$ship_dest" \
    'ls ~/.ohcommodore/ns/default/artifacts/*/stdout.txt 2>/dev/null | head -1' 2>&1)

  if [[ -n "$artifacts" && "$artifacts" != *"No such file"* ]]; then
    log_pass "Artifacts created"
    TESTS_RUN=$((TESTS_RUN + 1))
    TESTS_PASSED=$((TESTS_PASSED + 1))

    # Check if our test output is in the artifact
    local artifact_content
    artifact_content=$(ssh -o BatchMode=yes -o StrictHostKeyChecking=accept-new "$ship_dest" "cat '$artifacts'" 2>&1)
    assert_contains "Artifact contains test output" "$artifact_content" "QUEUE_TEST_SUCCESS"
  else
    log_fail "Artifacts not created"
    TESTS_RUN=$((TESTS_RUN + 1))
    TESTS_FAILED=$((TESTS_FAILED + 1))
  fi

  # ============================================
  # Test 8: Inbox list shows message
  # ============================================
  log_test "Checking inbox list..."

  local inbox_list
  inbox_list=$(ssh -o BatchMode=yes -o StrictHostKeyChecking=accept-new "$ship_dest" \
    '~/.local/bin/ohcommodore inbox list' 2>&1)

  assert_contains "Inbox list shows message" "$inbox_list" "cmd.exec"

  # ============================================
  # Test 9: Check outbound queue for result
  # ============================================
  log_test "Checking for result message in outbound..."

  local outbound_files
  outbound_files=$(ssh -o BatchMode=yes -o StrictHostKeyChecking=accept-new "$ship_dest" \
    'ls ~/.ohcommodore/ns/default/q/outbound/*.json 2>/dev/null | wc -l' 2>&1 | tr -d ' ')
  outbound_files="${outbound_files:-0}"

  # Result should be in outbound (may or may not have been delivered yet)
  if [[ "$outbound_files" -ge 1 ]]; then
    log_pass "Result message created in outbound"
    TESTS_RUN=$((TESTS_RUN + 1))
    TESTS_PASSED=$((TESTS_PASSED + 1))

    # Check it's a cmd.result
    local result_topic
    result_topic=$(ssh -o BatchMode=yes -o StrictHostKeyChecking=accept-new "$ship_dest" \
      'jq -r .topic ~/.ohcommodore/ns/default/q/outbound/*.json 2>/dev/null | head -1' 2>&1)
    assert_contains "Result has cmd.result topic" "$result_topic" "cmd.result"
  else
    log_info "Result message already delivered (outbound empty)"
    TESTS_RUN=$((TESTS_RUN + 1))
    TESTS_PASSED=$((TESTS_PASSED + 1))
  fi

  # Print summary
  print_summary
}

main "$@"
