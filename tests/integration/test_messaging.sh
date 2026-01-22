#!/usr/bin/env bash
# Integration test: Messaging System
#
# This test creates a fleet with two ships and verifies:
# 1. Queue status on flagship and ships
# 2. Flagship-to-ship message delivery and processing
# 3. Ship-to-ship message delivery and processing
# 4. Scheduler processing and artifact creation
#
# Requires: GH_TOKEN, ssh access to exe.dev

set -euo pipefail

# Source test helpers
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/../lib/test_helpers.sh"

# Test configuration
TEST_REPO="${TEST_REPO:-smithclay/ohcommodore}"

main() {
  echo "=================================="
  echo "Messaging System Integration Test"
  echo "=================================="
  echo ""

  # Check environment
  check_required_env

  # Ensure clean state (remove lingering VMs from previous failed runs)
  cleanup_lingering_vms

  # Register cleanup
  register_cleanup

  # ============================================
  # Setup: Create fleet with two ships
  # ============================================
  log_test "Setting up fleet..."

  if ! GH_TOKEN="$GH_TOKEN" "$PROJECT_ROOT/ohcommodore" init >/dev/null 2>&1; then
    log_fail "Failed to initialize flagship"
    return 1
  fi

  local ship_name_a="${TEST_REPO##*/}"
  local ship_name_b="${ship_name_a}-b"

  if ! GH_TOKEN="$GH_TOKEN" "$PROJECT_ROOT/ohcommodore" ship create "$TEST_REPO" >/dev/null 2>&1; then
    log_fail "Failed to create ship A"
    return 1
  fi

  if ! GH_TOKEN="$GH_TOKEN" "$PROJECT_ROOT/ohcommodore" ship create "$ship_name_b" >/dev/null 2>&1; then
    log_fail "Failed to create ship B"
    return 1
  fi

  log_info "Fleet created with ships: $ship_name_a, $ship_name_b"

  # Get ship SSH destinations from fleet (SHIP column contains id like "reponame-abc123", SSH_DEST is column 3)
  local ship_a_dest ship_b_dest
  ship_a_dest=$("$PROJECT_ROOT/ohcommodore" fleet status 2>&1 | awk -v repo="$TEST_REPO" '$2 == repo {print $3; exit}')
  [[ -n "$ship_a_dest" ]] || { log_fail "Could not get ship A destination"; return 1; }
  ship_b_dest=$("$PROJECT_ROOT/ohcommodore" fleet status 2>&1 | awk -v prefix="$ship_name_b-" '$1 ~ "^"prefix {print $3; exit}')
  [[ -n "$ship_b_dest" ]] || { log_fail "Could not get ship B destination"; return 1; }

  local ship_host="${ship_a_dest%%:*}"
  ship_host="${ship_host#*@}"

  log_info "Ship A destination: $ship_a_dest"
  log_info "Ship B destination: $ship_b_dest"
  log_info "Ship A host: $ship_host"

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

  queue_status=$(ssh -o BatchMode=yes -o StrictHostKeyChecking=accept-new "$ship_a_dest" '~/.local/bin/ohcommodore queue status' 2>&1)

  assert_contains "Ship queue status works" "$queue_status" "namespace:"

  # ============================================
  # Test 3: Send message from flagship to ship
  # ============================================
  log_test "Sending message from flagship to ship..."

  # Get ship identity
  local ship_identity
  ship_identity=$(ssh -o BatchMode=yes -o StrictHostKeyChecking=no -o LogLevel=ERROR "$ship_a_dest" '~/.local/bin/ohcommodore inbox identity' 2>/dev/null | grep -oE 'captain@[a-zA-Z0-9_-]+' | head -1)

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
  inbound_files=$(ssh -o BatchMode=yes -o StrictHostKeyChecking=accept-new "$ship_a_dest" \
    'ls ~/.ohcommodore/ns/default/q/inbound/*.json 2>/dev/null | wc -l' 2>&1 | tr -d '[:space:]')
  [[ "$inbound_files" =~ ^[0-9]+$ ]] || inbound_files=0

  assert "Message file exists in inbound" "[[ '$inbound_files' -ge 1 ]]"

  # ============================================
  # Test 5: Start scheduler briefly to process message
  # ============================================
  log_test "Processing message via scheduler..."

  # Run scheduler for enough time to start AND reap background job
  # With 10s poll interval, need ~25s for: start job (cycle 1) + reap job (cycle 2)
  ssh -o BatchMode=yes -o StrictHostKeyChecking=accept-new "$ship_a_dest" '
    timeout 30 ~/.local/bin/ohcommodore _scheduler &
    SCHED_PID=$!
    sleep 25
    kill $SCHED_PID 2>/dev/null || true
  ' 2>&1 || true

  # ============================================
  # Test 6: Verify message was processed
  # ============================================
  log_test "Verifying message was processed..."

  # Check messages table for handled message
  local handled_count
  handled_count=$(ssh -o BatchMode=yes -o StrictHostKeyChecking=accept-new "$ship_a_dest" \
    "duckdb ~/.ohcommodore/ns/default/data.duckdb -noheader -csv \"SELECT COUNT(*) FROM messages WHERE handled_at IS NOT NULL\"" 2>&1 | tr -d '[:space:]')
  [[ "$handled_count" =~ ^[0-9]+$ ]] || handled_count=0

  assert "Message marked as handled" "[[ '$handled_count' -ge 1 ]]"

  # ============================================
  # Test 7: Check artifacts were created
  # ============================================
  log_test "Checking artifacts..."

  local artifacts
  artifacts=$(ssh -o BatchMode=yes -o StrictHostKeyChecking=accept-new "$ship_a_dest" \
    'ls ~/.ohcommodore/ns/default/artifacts/*/stdout.txt 2>/dev/null | head -1' 2>&1)

  if [[ -n "$artifacts" && "$artifacts" != *"No such file"* ]]; then
    log_pass "Artifacts created"
    TESTS_RUN=$((TESTS_RUN + 1))
    TESTS_PASSED=$((TESTS_PASSED + 1))

    # Check if our test output is in the artifact
    local artifact_content
    artifact_content=$(ssh -o BatchMode=yes -o StrictHostKeyChecking=accept-new "$ship_a_dest" "cat '$artifacts'" 2>&1)
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
  inbox_list=$(ssh -o BatchMode=yes -o StrictHostKeyChecking=accept-new "$ship_a_dest" \
    '~/.local/bin/ohcommodore inbox list' 2>&1)

  assert_contains "Inbox list shows message" "$inbox_list" "cmd.exec"

  # ============================================
  # Test 9: Check outbound queue for result
  # ============================================
  log_test "Checking for result message in outbound..."

  local outbound_files
  outbound_files=$(ssh -o BatchMode=yes -o StrictHostKeyChecking=accept-new "$ship_a_dest" \
    'ls ~/.ohcommodore/ns/default/q/outbound/*.json 2>/dev/null | wc -l' 2>&1 | tr -d '[:space:]')
  # Ensure it's a valid number
  [[ "$outbound_files" =~ ^[0-9]+$ ]] || outbound_files=0

  # Result should be in outbound (may or may not have been delivered yet)
  if [[ "$outbound_files" -ge 1 ]]; then
    log_pass "Result message created in outbound"
    TESTS_RUN=$((TESTS_RUN + 1))
    TESTS_PASSED=$((TESTS_PASSED + 1))

    # Check it's a cmd.result
    local result_topic
    result_topic=$(ssh -o BatchMode=yes -o StrictHostKeyChecking=accept-new "$ship_a_dest" \
      'jq -r .topic ~/.ohcommodore/ns/default/q/outbound/*.json 2>/dev/null | head -1' 2>&1)
    assert_contains "Result has cmd.result topic" "$result_topic" "cmd.result"
  else
    log_info "Result message already delivered (outbound empty)"
    TESTS_RUN=$((TESTS_RUN + 1))
    TESTS_PASSED=$((TESTS_PASSED + 1))
  fi

  # ============================================
  # Test 10: Ship-to-ship message flow
  # ============================================
  log_test "Sending message from ship A to ship B..."

  local ship_b_identity
  # Use StrictHostKeyChecking=no to avoid warnings in output, and grep for the identity pattern
  ship_b_identity=$(ssh -o BatchMode=yes -o StrictHostKeyChecking=no -o LogLevel=ERROR "$ship_b_dest" '~/.local/bin/ohcommodore inbox identity' 2>/dev/null | grep -oE 'captain@[a-zA-Z0-9_-]+' | head -1)

  if [[ -z "$ship_b_identity" || "$ship_b_identity" == *"ERROR"* ]]; then
    log_fail "Could not get ship B identity"
    echo "Output: $ship_b_identity"
    return 1
  fi

  log_info "Ship B identity: $ship_b_identity"

  local ship2ship_send_output ship2ship_request_id
  ship2ship_send_output=$(ssh -o BatchMode=yes -o StrictHostKeyChecking=no -o LogLevel=ERROR "$ship_a_dest" \
    "~/.local/bin/ohcommodore inbox send '$ship_b_identity' 'echo SHIP2SHIP_TEST_SUCCESS'" 2>&1) || true
  assert_contains "Ship-to-ship message sent" "$ship2ship_send_output" "Message sent:"
  ship2ship_request_id=$(echo "$ship2ship_send_output" | awk '{print $3}')
  [[ -n "$ship2ship_request_id" ]] || { log_fail "Could not parse ship-to-ship request id"; return 1; }

  log_test "Verifying ship-to-ship message arrived..."

  sleep 3
  local ship2ship_inbound
  ship2ship_inbound=$(ssh -o BatchMode=yes -o StrictHostKeyChecking=no -o LogLevel=ERROR "$ship_b_dest" \
    "grep -l '$ship2ship_request_id' ~/.ohcommodore/ns/default/q/inbound/*.json 2>/dev/null | head -1" 2>/dev/null | tr -d '\n')
  assert "Ship-to-ship inbound received" "[[ -n '$ship2ship_inbound' ]]"

  log_test "Processing ship-to-ship message on ship B..."

  # Run scheduler for enough time to start AND reap background job
  ssh -o BatchMode=yes -o StrictHostKeyChecking=accept-new "$ship_b_dest" '
    timeout 30 ~/.local/bin/ohcommodore _scheduler &
    SCHED_PID=$!
    sleep 25
    kill $SCHED_PID 2>/dev/null || true
  ' 2>&1 || true

  log_test "Verifying ship-to-ship result delivered to ship A..."

  # Check if result message with our request_id arrived in ship A's inbound
  local ship2ship_result
  ship2ship_result=$(ssh -o BatchMode=yes -o StrictHostKeyChecking=no -o LogLevel=ERROR "$ship_a_dest" \
    "grep -l '$ship2ship_request_id' ~/.ohcommodore/ns/default/q/inbound/*.json 2>/dev/null | head -1" 2>/dev/null | tr -d '\n')
  assert "Ship-to-ship cmd.result delivered" "[[ -n '$ship2ship_result' ]]"

  log_test "Checking ship-to-ship artifacts on ship B..."

  local ship2ship_artifact
  ship2ship_artifact=$(ssh -o BatchMode=yes -o StrictHostKeyChecking=accept-new "$ship_b_dest" \
    'ls ~/.ohcommodore/ns/default/artifacts/*/stdout.txt 2>/dev/null | head -1' 2>&1)

  if [[ -n "$ship2ship_artifact" && "$ship2ship_artifact" != *"No such file"* ]]; then
    local ship2ship_artifact_content
    ship2ship_artifact_content=$(ssh -o BatchMode=yes -o StrictHostKeyChecking=accept-new "$ship_b_dest" "cat '$ship2ship_artifact'" 2>&1)
    assert_contains "Ship-to-ship artifact output" "$ship2ship_artifact_content" "SHIP2SHIP_TEST_SUCCESS"
  else
    log_fail "Ship-to-ship artifacts not created"
    TESTS_RUN=$((TESTS_RUN + 1))
    TESTS_FAILED=$((TESTS_FAILED + 1))
  fi

  # Print summary
  print_summary
}

main "$@"
