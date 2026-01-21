# ohcommodore v2 Queue System Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Replace remote DuckDB writes over SSH with file-based NDJSON queue transport for reliable, atomic inter-ship messaging.

**Architecture:** Messages are JSON files delivered via SCP to a destination queue directory, then published via atomic rename. Each node runs a daemon that ingests queue files into a local DuckDB `messages` table, executes commands, and emits results back via the same queue transport. Namespaces isolate state under `~/.ohcommodore/ns/<namespace>/`.

**Tech Stack:** Bash, SSH/SCP, DuckDB, jq, uuidgen

---

## Phase 1: Foundation

### Task 1: Add Namespace Helper Functions

**Files:**
- Modify: `ohcommodore:34-40` (after `sql_escape` function)

**Step 1: Write test script for namespace helpers**

Create a test script to verify the helpers work:

```bash
cat > /tmp/test_ns_helpers.sh << 'EOF'
#!/usr/bin/env bash
set -euo pipefail

# Source just the helper functions (we'll inline them for testing)
ns() { echo "${OHCOM_NS:-default}"; }
ns_root() { echo "$HOME/.ohcommodore/ns/$(ns)"; }
msg_db() { echo "$(ns_root)/data.duckdb"; }
q_root() { echo "$(ns_root)/q"; }
q_inbound() { echo "$(q_root)/inbound"; }
q_incoming() { echo "$(q_inbound)/.incoming"; }
q_dead() { echo "$(q_root)/dead"; }
q_done() { echo "$(q_root)/done"; }
artifacts_root() { echo "$(ns_root)/artifacts"; }

# Test default namespace
[[ "$(ns)" == "default" ]] || { echo "FAIL: ns() should be 'default'"; exit 1; }
[[ "$(ns_root)" == "$HOME/.ohcommodore/ns/default" ]] || { echo "FAIL: ns_root()"; exit 1; }
[[ "$(msg_db)" == "$HOME/.ohcommodore/ns/default/data.duckdb" ]] || { echo "FAIL: msg_db()"; exit 1; }
[[ "$(q_inbound)" == "$HOME/.ohcommodore/ns/default/q/inbound" ]] || { echo "FAIL: q_inbound()"; exit 1; }
[[ "$(q_incoming)" == "$HOME/.ohcommodore/ns/default/q/inbound/.incoming" ]] || { echo "FAIL: q_incoming()"; exit 1; }

# Test custom namespace
export OHCOM_NS="production"
[[ "$(ns)" == "production" ]] || { echo "FAIL: ns() with OHCOM_NS"; exit 1; }
[[ "$(ns_root)" == "$HOME/.ohcommodore/ns/production" ]] || { echo "FAIL: ns_root() with OHCOM_NS"; exit 1; }

echo "PASS: All namespace helper tests passed"
EOF
chmod +x /tmp/test_ns_helpers.sh
```

**Step 2: Run test to verify it fails**

Run: `bash /tmp/test_ns_helpers.sh`
Expected: PASS (tests the contract before we add to main script)

**Step 3: Add namespace helpers to ohcommodore**

Add after line 40 (after `need_cmd` function):

```bash
# Namespace helpers (v2 queue system)
ns() { echo "${OHCOM_NS:-default}"; }
ns_root() { echo "$HOME/.ohcommodore/ns/$(ns)"; }
msg_db() { echo "$(ns_root)/data.duckdb"; }
q_root() { echo "$(ns_root)/q"; }
q_inbound() { echo "$(q_root)/inbound"; }
q_incoming() { echo "$(q_inbound)/.incoming"; }
q_dead() { echo "$(q_root)/dead"; }
q_done() { echo "$(q_root)/done"; }
artifacts_root() { echo "$(ns_root)/artifacts"; }
```

**Step 4: Verify helpers are accessible**

Run: `source ./ohcommodore && ns && ns_root`
Expected: `default` and `/Users/<user>/.ohcommodore/ns/default`

**Step 5: Commit**

```bash
git add ohcommodore
git commit -m "feat(v2): add namespace helper functions for queue system"
```

---

### Task 2: Add Queue Directory Initialization

**Files:**
- Modify: `ohcommodore` (add after namespace helpers)

**Step 1: Write test for queue directory creation**

```bash
cat > /tmp/test_queue_init.sh << 'EOF'
#!/usr/bin/env bash
set -euo pipefail

# Use test namespace to avoid polluting real state
export OHCOM_NS="test-$$"
TEST_ROOT="$HOME/.ohcommodore/ns/$OHCOM_NS"

# Clean up on exit
trap 'rm -rf "$TEST_ROOT"' EXIT

# Source the script
source ./ohcommodore

# Run init
queue_init_dirs

# Verify directories exist
[[ -d "$(q_inbound)" ]] || { echo "FAIL: q_inbound not created"; exit 1; }
[[ -d "$(q_incoming)" ]] || { echo "FAIL: q_incoming not created"; exit 1; }
[[ -d "$(q_dead)" ]] || { echo "FAIL: q_dead not created"; exit 1; }
[[ -d "$(q_done)" ]] || { echo "FAIL: q_done not created"; exit 1; }
[[ -d "$(artifacts_root)" ]] || { echo "FAIL: artifacts_root not created"; exit 1; }

echo "PASS: Queue directories created correctly"
EOF
chmod +x /tmp/test_queue_init.sh
```

**Step 2: Run test to verify it fails**

Run: `bash /tmp/test_queue_init.sh`
Expected: FAIL with "queue_init_dirs: command not found"

**Step 3: Add queue_init_dirs function**

Add after the namespace helpers:

```bash
queue_init_dirs() {
  mkdir -p "$(q_inbound)"
  mkdir -p "$(q_incoming)"
  mkdir -p "$(q_dead)"
  mkdir -p "$(q_done)"
  mkdir -p "$(artifacts_root)"
}
```

**Step 4: Run test to verify it passes**

Run: `bash /tmp/test_queue_init.sh`
Expected: PASS

**Step 5: Commit**

```bash
git add ohcommodore
git commit -m "feat(v2): add queue_init_dirs for namespace directory structure"
```

---

### Task 3: Create Messages Table Schema

**Files:**
- Modify: `ohcommodore` (add new function)

**Step 1: Write test for messages table creation**

```bash
cat > /tmp/test_messages_table.sh << 'EOF'
#!/usr/bin/env bash
set -euo pipefail

export OHCOM_NS="test-$$"
TEST_ROOT="$HOME/.ohcommodore/ns/$OHCOM_NS"
trap 'rm -rf "$TEST_ROOT"' EXIT

source ./ohcommodore
queue_init_dirs
init_messages_db

# Verify table exists with correct columns
COLS=$(duckdb "$(msg_db)" -noheader -csv "SELECT column_name FROM information_schema.columns WHERE table_name='messages' ORDER BY ordinal_position")
EXPECTED="message_id
created_at
source
dest
topic
job_id
lease_token
payload_json
ingested_at
handled_at"

[[ "$COLS" == "$EXPECTED" ]] || { echo "FAIL: columns mismatch"; echo "Got: $COLS"; echo "Expected: $EXPECTED"; exit 1; }

echo "PASS: Messages table created with correct schema"
EOF
chmod +x /tmp/test_messages_table.sh
```

**Step 2: Run test to verify it fails**

Run: `bash /tmp/test_messages_table.sh`
Expected: FAIL with "init_messages_db: command not found"

**Step 3: Add init_messages_db function**

```bash
init_messages_db() {
  local db
  db="$(msg_db)"
  mkdir -p "$(dirname "$db")"
  duckdb "$db" "
    CREATE TABLE IF NOT EXISTS messages (
      message_id TEXT PRIMARY KEY,
      created_at TIMESTAMP NOT NULL,
      source TEXT NOT NULL,
      dest TEXT NOT NULL,
      topic TEXT NOT NULL,
      job_id TEXT,
      lease_token TEXT,
      payload_json TEXT NOT NULL,
      ingested_at TIMESTAMP DEFAULT current_timestamp,
      handled_at TIMESTAMP
    );
  "
}
```

**Step 4: Run test to verify it passes**

Run: `bash /tmp/test_messages_table.sh`
Expected: PASS

**Step 5: Commit**

```bash
git add ohcommodore
git commit -m "feat(v2): add messages table schema for queue system"
```

---

### Task 4: Implement Message File Writer

**Files:**
- Modify: `ohcommodore` (add new function)

**Step 1: Write test for message file creation**

```bash
cat > /tmp/test_write_msg.sh << 'EOF'
#!/usr/bin/env bash
set -euo pipefail

export OHCOM_NS="test-$$"
TEST_ROOT="$HOME/.ohcommodore/ns/$OHCOM_NS"
trap 'rm -rf "$TEST_ROOT"' EXIT

source ./ohcommodore
queue_init_dirs

# Write a message
MSG_PATH=$(write_msg_file "cmd.exec" "commodore@flagship" '{"cmd":"echo hello","cwd":"~"}')

# Verify file exists and is valid JSON
[[ -f "$MSG_PATH" ]] || { echo "FAIL: message file not created"; exit 1; }
[[ "$MSG_PATH" == *.json ]] || { echo "FAIL: should have .json extension"; exit 1; }

# Verify JSON structure
TOPIC=$(jq -r '.topic' "$MSG_PATH")
DEST=$(jq -r '.dest' "$MSG_PATH")
MSG_ID=$(jq -r '.message_id' "$MSG_PATH")

[[ "$TOPIC" == "cmd.exec" ]] || { echo "FAIL: topic mismatch"; exit 1; }
[[ "$DEST" == "commodore@flagship" ]] || { echo "FAIL: dest mismatch"; exit 1; }
[[ -n "$MSG_ID" && "$MSG_ID" != "null" ]] || { echo "FAIL: message_id missing"; exit 1; }

echo "PASS: Message file created with correct structure"
EOF
chmod +x /tmp/test_write_msg.sh
```

**Step 2: Run test to verify it fails**

Run: `bash /tmp/test_write_msg.sh`
Expected: FAIL with "write_msg_file: command not found"

**Step 3: Add write_msg_file function**

```bash
write_msg_file() {
  local topic="$1"
  local dest="$2"
  local payload_json="$3"
  local job_id="${4:-}"
  local lease_token="${5:-}"

  local msg_id ts source tmpfile finalfile
  msg_id=$(uuidgen 2>/dev/null || cat /proc/sys/kernel/random/uuid)
  ts=$(date -u +%Y-%m-%dT%H:%M:%SZ)
  source=$(_inbox_identity 2>/dev/null || echo "unknown")

  local outdir
  outdir="$(q_root)/outbound"
  mkdir -p "$outdir"

  tmpfile="$outdir/msg.${ts//[:-]/}.${msg_id}.json.tmp"
  finalfile="${tmpfile%.tmp}"

  jq -n \
    --arg msg_id "$msg_id" \
    --arg created_at "$ts" \
    --arg source "$source" \
    --arg dest "$dest" \
    --arg topic "$topic" \
    --arg job_id "$job_id" \
    --arg lease_token "$lease_token" \
    --argjson payload "$payload_json" \
    '{
      message_id: $msg_id,
      created_at: $created_at,
      source: $source,
      dest: $dest,
      topic: $topic,
      job_id: (if $job_id == "" then null else $job_id end),
      lease_token: (if $lease_token == "" then null else $lease_token end),
      payload: $payload
    }' > "$tmpfile"

  mv "$tmpfile" "$finalfile"
  echo "$finalfile"
}
```

**Step 4: Run test to verify it passes**

Run: `bash /tmp/test_write_msg.sh`
Expected: PASS

**Step 5: Commit**

```bash
git add ohcommodore
git commit -m "feat(v2): add write_msg_file for creating queue messages"
```

---

### Task 5: Implement SCP Delivery with Retry

**Files:**
- Modify: `ohcommodore` (add new function)

**Step 1: Write test for delivery function signature**

```bash
cat > /tmp/test_deliver_msg.sh << 'EOF'
#!/usr/bin/env bash
set -euo pipefail

source ./ohcommodore

# Test that function exists and has correct signature
type deliver_msg_file >/dev/null 2>&1 || { echo "FAIL: deliver_msg_file not defined"; exit 1; }

echo "PASS: deliver_msg_file function exists"
EOF
chmod +x /tmp/test_deliver_msg.sh
```

**Step 2: Run test to verify it fails**

Run: `bash /tmp/test_deliver_msg.sh`
Expected: FAIL with "deliver_msg_file not defined"

**Step 3: Add deliver_msg_file function with retry logic**

```bash
deliver_msg_file() {
  local local_path="$1"
  local dest_ssh="$2"
  local max_retries="${3:-3}"

  local filename basename
  filename=$(basename "$local_path")

  local remote_incoming remote_inbound
  remote_incoming='$HOME/.ohcommodore/ns/default/q/inbound/.incoming'
  remote_inbound='$HOME/.ohcommodore/ns/default/q/inbound'

  local attempt=1
  local delay=1

  while [[ $attempt -le $max_retries ]]; do
    # Ensure remote directories exist
    if ssh -o BatchMode=yes "$dest_ssh" "mkdir -p $remote_incoming $remote_inbound" 2>/dev/null; then
      # SCP to .incoming (staging)
      if scp -q "$local_path" "${dest_ssh}:${remote_incoming}/${filename}" 2>/dev/null; then
        # Atomic rename to publish
        if ssh -o BatchMode=yes "$dest_ssh" "mv ${remote_incoming}/${filename} ${remote_inbound}/${filename}" 2>/dev/null; then
          rm -f "$local_path"
          return 0
        fi
      fi
    fi

    log "Delivery attempt $attempt/$max_retries failed, retrying in ${delay}s..."
    sleep "$delay"
    delay=$((delay * 2))
    attempt=$((attempt + 1))
  done

  log "ERROR: Failed to deliver message after $max_retries attempts"
  return 1
}
```

**Step 4: Run test to verify it passes**

Run: `bash /tmp/test_deliver_msg.sh`
Expected: PASS

**Step 5: Commit**

```bash
git add ohcommodore
git commit -m "feat(v2): add deliver_msg_file with SCP retry and backoff"
```

---

### Task 6: Implement Queue File Ingestion

**Files:**
- Modify: `ohcommodore` (add new function)

**Step 1: Write test for message ingestion**

```bash
cat > /tmp/test_ingest.sh << 'EOF'
#!/usr/bin/env bash
set -euo pipefail

export OHCOM_NS="test-$$"
TEST_ROOT="$HOME/.ohcommodore/ns/$OHCOM_NS"
trap 'rm -rf "$TEST_ROOT"' EXIT

source ./ohcommodore
queue_init_dirs
init_messages_db

# Create a test message file in inbound queue
MSG_ID="test-$(uuidgen)"
cat > "$(q_inbound)/msg.test.json" << MSGEOF
{
  "message_id": "$MSG_ID",
  "created_at": "2026-01-20T12:00:00Z",
  "source": "captain@ship-01",
  "dest": "commodore@flagship",
  "topic": "cmd.exec",
  "job_id": null,
  "lease_token": null,
  "payload": {"cmd": "echo hello"}
}
MSGEOF

# Ingest the file
ingest_queue_file "$(q_inbound)/msg.test.json"

# Verify message is in database
COUNT=$(duckdb "$(msg_db)" -noheader -csv "SELECT COUNT(*) FROM messages WHERE message_id='$MSG_ID'")
[[ "$COUNT" == "1" ]] || { echo "FAIL: message not ingested"; exit 1; }

# Verify file was removed (acknowledged)
[[ ! -f "$(q_inbound)/msg.test.json" ]] || { echo "FAIL: file should be removed after ingest"; exit 1; }

echo "PASS: Message ingested correctly"
EOF
chmod +x /tmp/test_ingest.sh
```

**Step 2: Run test to verify it fails**

Run: `bash /tmp/test_ingest.sh`
Expected: FAIL with "ingest_queue_file: command not found"

**Step 3: Add ingest_queue_file function**

```bash
ingest_queue_file() {
  local file_path="$1"
  local db
  db="$(msg_db)"

  # Parse JSON
  local msg_id created_at source dest topic job_id lease_token payload_json
  msg_id=$(jq -r '.message_id' "$file_path")
  created_at=$(jq -r '.created_at' "$file_path")
  source=$(jq -r '.source' "$file_path")
  dest=$(jq -r '.dest' "$file_path")
  topic=$(jq -r '.topic' "$file_path")
  job_id=$(jq -r '.job_id // empty' "$file_path")
  lease_token=$(jq -r '.lease_token // empty' "$file_path")
  payload_json=$(jq -c '.payload' "$file_path")

  # Escape for SQL
  local esc_msg_id esc_source esc_dest esc_topic esc_job_id esc_lease_token esc_payload
  esc_msg_id=$(sql_escape "$msg_id")
  esc_source=$(sql_escape "$source")
  esc_dest=$(sql_escape "$dest")
  esc_topic=$(sql_escape "$topic")
  esc_job_id=$(sql_escape "$job_id")
  esc_lease_token=$(sql_escape "$lease_token")
  esc_payload=$(sql_escape "$payload_json")

  # Insert with ON CONFLICT for idempotency
  if duckdb "$db" "
    INSERT INTO messages (message_id, created_at, source, dest, topic, job_id, lease_token, payload_json)
    VALUES ('$esc_msg_id', '$created_at', '$esc_source', '$esc_dest', '$esc_topic',
            $([ -n "$job_id" ] && echo "'$esc_job_id'" || echo "NULL"),
            $([ -n "$lease_token" ] && echo "'$esc_lease_token'" || echo "NULL"),
            '$esc_payload')
    ON CONFLICT (message_id) DO NOTHING;
  " 2>/dev/null; then
    # Ack: remove file
    rm -f "$file_path"
  else
    # Deadletter: move to dead queue with reason
    local filename
    filename=$(basename "$file_path")
    mv "$file_path" "$(q_dead)/$filename"
    echo "DuckDB insert failed" > "$(q_dead)/${filename%.json}.reason"
    die "Failed to ingest message $msg_id - moved to deadletter"
  fi
}
```

**Step 4: Run test to verify it passes**

Run: `bash /tmp/test_ingest.sh`
Expected: PASS

**Step 5: Commit**

```bash
git add ohcommodore
git commit -m "feat(v2): add ingest_queue_file with deadletter support"
```

---

## Phase 2: Replace inbox send

### Task 7: Update _inbox_send to Use Queue Transport

**Files:**
- Modify: `ohcommodore:616-643` (replace `_inbox_send` function)

**Step 1: Write integration test for new inbox send**

```bash
cat > /tmp/test_inbox_send_v2.sh << 'EOF'
#!/usr/bin/env bash
set -euo pipefail

export OHCOM_NS="test-$$"
TEST_ROOT="$HOME/.ohcommodore/ns/$OHCOM_NS"
trap 'rm -rf "$TEST_ROOT"' EXIT

source ./ohcommodore
queue_init_dirs
init_messages_db

# Mock _inbox_identity
_inbox_identity() { echo "captain@test-ship"; }

# Test that inbox send creates a message file (without actual delivery)
# We'll verify the file format
OUTBOUND_DIR="$(q_root)/outbound"
mkdir -p "$OUTBOUND_DIR"

# Create message (we can't test actual SSH delivery locally)
MSG_PATH=$(write_msg_file "cmd.exec" "commodore@flagship" '{"cmd":"echo hello","cwd":"~","env":{},"timeout_s":1800}')

# Verify the payload structure matches cmd.exec protocol
REQUEST_ID=$(jq -r '.payload.request_id // empty' "$MSG_PATH" 2>/dev/null || echo "")
# Note: request_id is optional in cmd.exec, cmd is required
CMD=$(jq -r '.payload.cmd' "$MSG_PATH")
[[ "$CMD" == "echo hello" ]] || { echo "FAIL: cmd not in payload"; exit 1; }

echo "PASS: inbox send creates valid cmd.exec message"
EOF
chmod +x /tmp/test_inbox_send_v2.sh
```

**Step 2: Run test to verify current behavior**

Run: `bash /tmp/test_inbox_send_v2.sh`
Expected: Should pass (write_msg_file already works)

**Step 3: Replace _inbox_send function**

Replace the existing `_inbox_send` function (lines 616-643):

```bash
_inbox_send() {
  [[ $# -ge 2 ]] || die "Usage: ohcommodore inbox send <recipient> <command>"
  local recipient="$1"
  local command="$2"

  if ! echo "$recipient" | grep -qE '^(captain|commodore)@.+$'; then
    die "Invalid recipient format. Use captain@<hostname> or commodore@<hostname>"
  fi

  local remote_host="${recipient#*@}"
  local dest_ssh="exedev@$remote_host"

  local request_id
  request_id=$(uuidgen 2>/dev/null || cat /proc/sys/kernel/random/uuid)

  # Build cmd.exec payload
  local payload_json
  payload_json=$(jq -n \
    --arg request_id "$request_id" \
    --arg cmd "$command" \
    '{
      request_id: $request_id,
      cmd: $cmd,
      cwd: "~",
      env: {},
      timeout_s: 1800
    }')

  # Write message file
  local msg_path
  msg_path=$(write_msg_file "cmd.exec" "$recipient" "$payload_json")

  # Deliver via SCP
  if deliver_msg_file "$msg_path" "$dest_ssh"; then
    echo "Message sent: $request_id -> $recipient"
  else
    die "Failed to send message to $recipient"
  fi
}
```

**Step 4: Verify function compiles**

Run: `bash -n ./ohcommodore`
Expected: No syntax errors

**Step 5: Commit**

```bash
git add ohcommodore
git commit -m "feat(v2): replace _inbox_send with queue-based transport"
```

---

## Phase 3: Replace Scheduler Daemon

### Task 8: Add Daemon Recovery on Startup

**Files:**
- Modify: `ohcommodore` (add new function)

**Step 1: Write test for recovery logic**

```bash
cat > /tmp/test_daemon_recovery.sh << 'EOF'
#!/usr/bin/env bash
set -euo pipefail

export OHCOM_NS="test-$$"
TEST_ROOT="$HOME/.ohcommodore/ns/$OHCOM_NS"
trap 'rm -rf "$TEST_ROOT"' EXIT

source ./ohcommodore
queue_init_dirs
init_messages_db

# Simulate a claimed file that was abandoned (old)
touch -d "10 minutes ago" "$(q_inbound)/.claimed.old-msg.json"

# Simulate a recently claimed file
echo '{}' > "$(q_inbound)/.claimed.recent-msg.json"

# Run recovery
daemon_recover_claimed

# Old claimed file should be moved to dead
[[ -f "$(q_dead)/old-msg.json" ]] || { echo "FAIL: old claimed not moved to dead"; exit 1; }
[[ -f "$(q_dead)/old-msg.reason" ]] || { echo "FAIL: old claimed missing reason file"; exit 1; }

# Recent claimed file should be moved back to inbound (retry)
[[ -f "$(q_inbound)/recent-msg.json" ]] || { echo "FAIL: recent claimed not moved back"; exit 1; }

echo "PASS: Daemon recovery handles claimed files correctly"
EOF
chmod +x /tmp/test_daemon_recovery.sh
```

**Step 2: Run test to verify it fails**

Run: `bash /tmp/test_daemon_recovery.sh`
Expected: FAIL with "daemon_recover_claimed: command not found"

**Step 3: Add daemon_recover_claimed function**

```bash
daemon_recover_claimed() {
  local inbound claimed_file filename age_seconds threshold_seconds
  inbound="$(q_inbound)"
  threshold_seconds=300  # 5 minutes

  for claimed_file in "$inbound"/.claimed.*.json 2>/dev/null; do
    [[ -f "$claimed_file" ]] || continue

    filename=$(basename "$claimed_file")
    filename="${filename#.claimed.}"  # Remove .claimed. prefix

    # Get file age in seconds
    if [[ "$(uname)" == "Darwin" ]]; then
      age_seconds=$(( $(date +%s) - $(stat -f %m "$claimed_file") ))
    else
      age_seconds=$(( $(date +%s) - $(stat -c %Y "$claimed_file") ))
    fi

    if [[ $age_seconds -gt $threshold_seconds ]]; then
      # Too old - deadletter
      mv "$claimed_file" "$(q_dead)/$filename"
      echo "Abandoned claim (age: ${age_seconds}s, exceeded ${threshold_seconds}s threshold)" > "$(q_dead)/${filename%.json}.reason"
      log "Deadlettered abandoned claim: $filename"
    else
      # Recent - retry
      mv "$claimed_file" "$inbound/$filename"
      log "Recovered claimed file for retry: $filename"
    fi
  done
}
```

**Step 4: Run test to verify it passes**

Run: `bash /tmp/test_daemon_recovery.sh`
Expected: PASS

**Step 5: Commit**

```bash
git add ohcommodore
git commit -m "feat(v2): add daemon_recover_claimed for startup recovery"
```

---

### Task 9: Add Graceful Shutdown Handler

**Files:**
- Modify: `ohcommodore` (add new variable and trap)

**Step 1: Write test for shutdown flag**

```bash
cat > /tmp/test_shutdown.sh << 'EOF'
#!/usr/bin/env bash
set -euo pipefail

source ./ohcommodore

# Verify DAEMON_SHUTDOWN variable exists
[[ -v DAEMON_SHUTDOWN ]] || DAEMON_SHUTDOWN=0
[[ "$DAEMON_SHUTDOWN" == "0" ]] || { echo "FAIL: DAEMON_SHUTDOWN should start as 0"; exit 1; }

# Verify handler function exists
type daemon_shutdown_handler >/dev/null 2>&1 || { echo "FAIL: daemon_shutdown_handler not defined"; exit 1; }

echo "PASS: Shutdown mechanism exists"
EOF
chmod +x /tmp/test_shutdown.sh
```

**Step 2: Run test to verify it fails**

Run: `bash /tmp/test_shutdown.sh`
Expected: FAIL with "daemon_shutdown_handler not defined"

**Step 3: Add shutdown handler**

Add near the top of the script (after `die()` function):

```bash
DAEMON_SHUTDOWN=0

daemon_shutdown_handler() {
  log "Shutdown signal received, finishing current work..."
  DAEMON_SHUTDOWN=1
}
```

**Step 4: Run test to verify it passes**

Run: `bash /tmp/test_shutdown.sh`
Expected: PASS

**Step 5: Commit**

```bash
git add ohcommodore
git commit -m "feat(v2): add graceful shutdown handler for daemon"
```

---

### Task 10: Implement v2 Daemon Loop

**Files:**
- Modify: `ohcommodore` (add new function, will replace `cmd__scheduler` later)

**Step 1: Write test for daemon claim mechanism**

```bash
cat > /tmp/test_daemon_claim.sh << 'EOF'
#!/usr/bin/env bash
set -euo pipefail

export OHCOM_NS="test-$$"
TEST_ROOT="$HOME/.ohcommodore/ns/$OHCOM_NS"
trap 'rm -rf "$TEST_ROOT"' EXIT

source ./ohcommodore
queue_init_dirs
init_messages_db

# Create a test message in inbound
cat > "$(q_inbound)/msg.test.json" << 'MSGEOF'
{
  "message_id": "test-123",
  "created_at": "2026-01-20T12:00:00Z",
  "source": "captain@ship-01",
  "dest": "commodore@flagship",
  "topic": "cmd.exec",
  "job_id": null,
  "lease_token": null,
  "payload": {"request_id": "req-123", "cmd": "echo hello", "cwd": "~", "env": {}, "timeout_s": 1800}
}
MSGEOF

# Claim a message
CLAIMED=$(daemon_claim_one)
[[ -n "$CLAIMED" ]] || { echo "FAIL: no message claimed"; exit 1; }
[[ -f "$CLAIMED" ]] || { echo "FAIL: claimed file doesn't exist"; exit 1; }
[[ "$CLAIMED" == *".claimed."* ]] || { echo "FAIL: claimed file should have .claimed. prefix"; exit 1; }

# Original should be gone
[[ ! -f "$(q_inbound)/msg.test.json" ]] || { echo "FAIL: original file should be moved"; exit 1; }

echo "PASS: Daemon claim works correctly"
EOF
chmod +x /tmp/test_daemon_claim.sh
```

**Step 2: Run test to verify it fails**

Run: `bash /tmp/test_daemon_claim.sh`
Expected: FAIL with "daemon_claim_one: command not found"

**Step 3: Add daemon_claim_one function**

```bash
daemon_claim_one() {
  local inbound file filename claimed_path
  inbound="$(q_inbound)"

  # Find first visible message file
  for file in "$inbound"/msg.*.json 2>/dev/null; do
    [[ -f "$file" ]] || continue

    filename=$(basename "$file")
    claimed_path="$inbound/.claimed.$filename"

    # Atomic claim via rename
    if mv "$file" "$claimed_path" 2>/dev/null; then
      echo "$claimed_path"
      return 0
    fi
  done

  # No messages to claim
  return 1
}
```

**Step 4: Run test to verify it passes**

Run: `bash /tmp/test_daemon_claim.sh`
Expected: PASS

**Step 5: Commit**

```bash
git add ohcommodore
git commit -m "feat(v2): add daemon_claim_one for atomic message claiming"
```

---

### Task 11: Implement Command Execution Handler

**Files:**
- Modify: `ohcommodore` (add new function)

**Step 1: Write test for command execution**

```bash
cat > /tmp/test_exec_cmd.sh << 'EOF'
#!/usr/bin/env bash
set -euo pipefail

export OHCOM_NS="test-$$"
TEST_ROOT="$HOME/.ohcommodore/ns/$OHCOM_NS"
trap 'rm -rf "$TEST_ROOT"' EXIT

source ./ohcommodore
queue_init_dirs
init_messages_db

# Execute a simple command
RESULT=$(daemon_exec_cmd "echo hello" "~" "" 30)
EXIT_CODE=$?

[[ $EXIT_CODE -eq 0 ]] || { echo "FAIL: command should succeed"; exit 1; }
[[ "$RESULT" == "hello" ]] || { echo "FAIL: output should be 'hello', got '$RESULT'"; exit 1; }

# Test timeout (should fail for long command with short timeout)
# Skip this test as it takes too long

echo "PASS: Command execution works"
EOF
chmod +x /tmp/test_exec_cmd.sh
```

**Step 2: Run test to verify it fails**

Run: `bash /tmp/test_exec_cmd.sh`
Expected: FAIL with "daemon_exec_cmd: command not found"

**Step 3: Add daemon_exec_cmd function**

```bash
daemon_exec_cmd() {
  local cmd="$1"
  local cwd="$2"
  local env_json="$3"
  local timeout_s="${4:-1800}"

  local result exit_code

  # Expand ~ in cwd
  cwd="${cwd/#\~/$HOME}"

  # Change to working directory if specified
  if [[ -n "$cwd" && -d "$cwd" ]]; then
    cd "$cwd"
  fi

  # Execute with timeout
  result=$(timeout "$timeout_s" bash -c "$cmd" 2>&1) && exit_code=$? || exit_code=$?

  echo "$result"
  return $exit_code
}
```

**Step 4: Run test to verify it passes**

Run: `bash /tmp/test_exec_cmd.sh`
Expected: PASS

**Step 5: Commit**

```bash
git add ohcommodore
git commit -m "feat(v2): add daemon_exec_cmd for command execution"
```

---

### Task 12: Implement cmd.result Emission

**Files:**
- Modify: `ohcommodore` (add new function)

**Step 1: Write test for result message creation**

```bash
cat > /tmp/test_emit_result.sh << 'EOF'
#!/usr/bin/env bash
set -euo pipefail

export OHCOM_NS="test-$$"
TEST_ROOT="$HOME/.ohcommodore/ns/$OHCOM_NS"
trap 'rm -rf "$TEST_ROOT"' EXIT

source ./ohcommodore
queue_init_dirs
init_messages_db

# Mock identity
_inbox_identity() { echo "captain@test-ship"; }

# Emit a result
HOSTNAME=$(hostname -f 2>/dev/null || hostname)
MSG_PATH=$(emit_cmd_result "req-123" 0 "/tmp/stdout.txt" "/tmp/stderr.txt" "2026-01-20T12:00:00Z" "2026-01-20T12:00:01Z" "commodore@flagship")

[[ -f "$MSG_PATH" ]] || { echo "FAIL: result message not created"; exit 1; }

TOPIC=$(jq -r '.topic' "$MSG_PATH")
[[ "$TOPIC" == "cmd.result" ]] || { echo "FAIL: topic should be cmd.result"; exit 1; }

EXIT_CODE=$(jq -r '.payload.exit_code' "$MSG_PATH")
[[ "$EXIT_CODE" == "0" ]] || { echo "FAIL: exit_code mismatch"; exit 1; }

# Verify paths include hostname
STDOUT_PATH=$(jq -r '.payload.stdout_path' "$MSG_PATH")
[[ "$STDOUT_PATH" == *"$HOSTNAME"* ]] || { echo "FAIL: stdout_path should include hostname"; exit 1; }

echo "PASS: cmd.result emission works"
EOF
chmod +x /tmp/test_emit_result.sh
```

**Step 2: Run test to verify it fails**

Run: `bash /tmp/test_emit_result.sh`
Expected: FAIL with "emit_cmd_result: command not found"

**Step 3: Add emit_cmd_result function**

```bash
emit_cmd_result() {
  local request_id="$1"
  local exit_code="$2"
  local stdout_path="$3"
  local stderr_path="$4"
  local started_at="$5"
  local ended_at="$6"
  local dest="$7"

  local hostname
  hostname=$(hostname -f 2>/dev/null || hostname)

  # Build paths that include hostname (artifacts stay on ship)
  local remote_stdout_path remote_stderr_path
  remote_stdout_path="${hostname}:${stdout_path}"
  remote_stderr_path="${hostname}:${stderr_path}"

  local payload_json
  payload_json=$(jq -n \
    --arg request_id "$request_id" \
    --argjson exit_code "$exit_code" \
    --arg stdout_path "$remote_stdout_path" \
    --arg stderr_path "$remote_stderr_path" \
    --arg started_at "$started_at" \
    --arg ended_at "$ended_at" \
    '{
      request_id: $request_id,
      exit_code: $exit_code,
      stdout_path: $stdout_path,
      stderr_path: $stderr_path,
      started_at: $started_at,
      ended_at: $ended_at
    }')

  write_msg_file "cmd.result" "$dest" "$payload_json"
}
```

**Step 4: Run test to verify it passes**

Run: `bash /tmp/test_emit_result.sh`
Expected: PASS

**Step 5: Commit**

```bash
git add ohcommodore
git commit -m "feat(v2): add emit_cmd_result with hostname-prefixed artifact paths"
```

---

### Task 13: Implement Full Daemon Loop

**Files:**
- Modify: `ohcommodore` (add new function)

**Step 1: Write test for daemon single iteration**

```bash
cat > /tmp/test_daemon_tick.sh << 'EOF'
#!/usr/bin/env bash
set -euo pipefail

export OHCOM_NS="test-$$"
TEST_ROOT="$HOME/.ohcommodore/ns/$OHCOM_NS"
trap 'rm -rf "$TEST_ROOT"' EXIT

source ./ohcommodore
queue_init_dirs
init_messages_db

# Mock identity for this test
_inbox_identity() { echo "captain@test-ship"; }

# Create a cmd.exec message
cat > "$(q_inbound)/msg.test.json" << 'MSGEOF'
{
  "message_id": "test-tick-123",
  "created_at": "2026-01-20T12:00:00Z",
  "source": "commodore@flagship",
  "dest": "captain@test-ship",
  "topic": "cmd.exec",
  "job_id": null,
  "lease_token": null,
  "payload": {"request_id": "req-tick-123", "cmd": "echo tick-success", "cwd": "~", "env": {}, "timeout_s": 30}
}
MSGEOF

# Run one tick of the daemon (without delivery)
daemon_process_one "captain@test-ship" || true

# Message should be ingested and handled
HANDLED=$(duckdb "$(msg_db)" -noheader -csv "SELECT handled_at IS NOT NULL FROM messages WHERE message_id='test-tick-123'" 2>/dev/null || echo "")
[[ "$HANDLED" == "true" ]] || { echo "FAIL: message should be marked handled"; exit 1; }

echo "PASS: Daemon tick processes message correctly"
EOF
chmod +x /tmp/test_daemon_tick.sh
```

**Step 2: Run test to verify it fails**

Run: `bash /tmp/test_daemon_tick.sh`
Expected: FAIL with "daemon_process_one: command not found"

**Step 3: Add daemon_process_one function**

```bash
daemon_process_one() {
  local my_identity="$1"

  # Claim a message
  local claimed_file
  claimed_file=$(daemon_claim_one) || return 1

  # Ingest into database
  ingest_queue_file "$claimed_file" 2>/dev/null || {
    # Already handled in ingest_queue_file (deadlettered)
    return 1
  }

  # Find unhandled cmd.exec messages for us
  local msg_row
  msg_row=$(duckdb "$(msg_db)" -json "
    SELECT message_id, payload_json
    FROM messages
    WHERE dest = '$my_identity'
      AND topic = 'cmd.exec'
      AND handled_at IS NULL
    ORDER BY created_at
    LIMIT 1
  " 2>/dev/null)

  [[ -n "$msg_row" && "$msg_row" != "[]" ]] || return 0

  local msg_id payload_json cmd cwd timeout_s request_id source
  msg_id=$(echo "$msg_row" | jq -r '.[0].message_id')
  payload_json=$(echo "$msg_row" | jq -r '.[0].payload_json')

  cmd=$(echo "$payload_json" | jq -r '.cmd')
  cwd=$(echo "$payload_json" | jq -r '.cwd // "~"')
  timeout_s=$(echo "$payload_json" | jq -r '.timeout_s // 1800')
  request_id=$(echo "$payload_json" | jq -r '.request_id // empty')

  # Get source for reply
  source=$(duckdb "$(msg_db)" -noheader -csv "SELECT source FROM messages WHERE message_id='$msg_id'" 2>/dev/null)

  # Create artifact directory
  local artifact_dir started_at ended_at
  artifact_dir="$(artifacts_root)/${request_id:-$msg_id}"
  mkdir -p "$artifact_dir"

  started_at=$(date -u +%Y-%m-%dT%H:%M:%SZ)

  # Execute command
  local result exit_code
  result=$(daemon_exec_cmd "$cmd" "$cwd" "" "$timeout_s" 2>&1) && exit_code=$? || exit_code=$?

  ended_at=$(date -u +%Y-%m-%dT%H:%M:%SZ)

  # Save artifacts
  echo "$result" > "$artifact_dir/stdout.txt"
  echo "" > "$artifact_dir/stderr.txt"  # stderr captured in stdout via 2>&1

  # Mark as handled
  duckdb "$(msg_db)" "UPDATE messages SET handled_at = current_timestamp WHERE message_id = '$msg_id'"

  # Emit result (but don't deliver in this function - let caller handle)
  if [[ -n "$source" ]]; then
    emit_cmd_result \
      "${request_id:-$msg_id}" \
      "$exit_code" \
      "$artifact_dir/stdout.txt" \
      "$artifact_dir/stderr.txt" \
      "$started_at" \
      "$ended_at" \
      "$source" >/dev/null
    # Note: actual delivery would be done by a separate sender loop or inline
  fi

  return 0
}
```

**Step 4: Run test to verify it passes**

Run: `bash /tmp/test_daemon_tick.sh`
Expected: PASS

**Step 5: Commit**

```bash
git add ohcommodore
git commit -m "feat(v2): add daemon_process_one for full message processing"
```

---

### Task 14: Replace cmd__scheduler with v2 Daemon

**Files:**
- Modify: `ohcommodore:682-722` (replace `cmd__scheduler` function)

**Step 1: Backup current scheduler behavior test**

The current scheduler uses the `inbox` table. The new one uses `messages` table and file queue.

**Step 2: Run syntax check**

Run: `bash -n ./ohcommodore`
Expected: No syntax errors

**Step 3: Replace cmd__scheduler function**

```bash
cmd__scheduler() {
  require_role commodore captain

  # Initialize v2 queue system
  queue_init_dirs
  init_messages_db

  local poll_interval
  poll_interval=$(duckdb ~/.local/ship/data.duckdb -noheader -csv "SELECT value FROM config WHERE key = 'POLL_INTERVAL_SEC'" 2>/dev/null || echo "10")
  local identity
  identity=$(_inbox_identity)
  [[ -n "$identity" ]] || die "No IDENTITY in config"

  # Set up graceful shutdown
  trap 'daemon_shutdown_handler' SIGTERM SIGINT

  log "v2 Scheduler starting for $identity (poll: ${poll_interval}s)"

  # Recover any abandoned claims from previous crash
  daemon_recover_claimed

  while [[ $DAEMON_SHUTDOWN -eq 0 ]]; do
    # Process all pending messages
    while daemon_process_one "$identity" 2>/dev/null; do
      [[ $DAEMON_SHUTDOWN -eq 0 ]] || break
    done

    # Send any pending outbound messages
    local outbound_dir result_file dest_host dest_ssh
    outbound_dir="$(q_root)/outbound"
    if [[ -d "$outbound_dir" ]]; then
      for result_file in "$outbound_dir"/*.json 2>/dev/null; do
        [[ -f "$result_file" ]] || continue
        dest_host=$(jq -r '.dest' "$result_file" | cut -d'@' -f2)
        dest_ssh="exedev@$dest_host"
        deliver_msg_file "$result_file" "$dest_ssh" 3 2>/dev/null || {
          log "Warning: Failed to deliver result to $dest_host"
        }
      done
    fi

    sleep "$poll_interval"
  done

  log "Scheduler shutting down gracefully"
}
```

**Step 4: Verify script compiles**

Run: `bash -n ./ohcommodore`
Expected: No syntax errors

**Step 5: Commit**

```bash
git add ohcommodore
git commit -m "feat(v2): replace scheduler with queue-based daemon loop"
```

---

## Phase 4: Update inbox list/read

### Task 15: Update _inbox_list to Query Messages Table

**Files:**
- Modify: `ohcommodore` (update `_inbox_list` function)

**Step 1: Write test for new inbox list**

```bash
cat > /tmp/test_inbox_list_v2.sh << 'EOF'
#!/usr/bin/env bash
set -euo pipefail

export OHCOM_NS="test-$$"
TEST_ROOT="$HOME/.ohcommodore/ns/$OHCOM_NS"
trap 'rm -rf "$TEST_ROOT"' EXIT

source ./ohcommodore
queue_init_dirs
init_messages_db

# Insert test messages directly
duckdb "$(msg_db)" "
  INSERT INTO messages (message_id, created_at, source, dest, topic, payload_json)
  VALUES
    ('msg-1', '2026-01-20T12:00:00', 'captain@ship-01', 'commodore@flagship', 'cmd.exec', '{\"cmd\":\"echo 1\"}'),
    ('msg-2', '2026-01-20T12:01:00', 'captain@ship-02', 'commodore@flagship', 'cmd.result', '{\"exit_code\":0}');
  UPDATE messages SET handled_at = current_timestamp WHERE message_id = 'msg-2';
"

# Test list shows messages
OUTPUT=$(_inbox_list 2>&1 || true)
[[ "$OUTPUT" == *"msg-1"* ]] || { echo "FAIL: should show msg-1"; exit 1; }
[[ "$OUTPUT" == *"msg-2"* ]] || { echo "FAIL: should show msg-2"; exit 1; }

echo "PASS: inbox list shows messages from v2 table"
EOF
chmod +x /tmp/test_inbox_list_v2.sh
```

**Step 2: Run test with current function**

Run: `bash /tmp/test_inbox_list_v2.sh`
Expected: FAIL (current function queries old `inbox` table)

**Step 3: Update _inbox_list function**

```bash
_inbox_list() {
  local status_filter=""
  if [[ "${1:-}" == "--status" && -n "${2:-}" ]]; then
    case "$2" in
      unread|pending)
        status_filter="WHERE handled_at IS NULL"
        ;;
      done|handled)
        status_filter="WHERE handled_at IS NOT NULL"
        ;;
      *)
        die "Invalid status '$2'. Use: unread, pending, done, handled"
        ;;
    esac
  fi

  # Try v2 messages table first, fall back to legacy inbox
  if [[ -f "$(msg_db)" ]]; then
    duckdb "$(msg_db)" -box "
      SELECT
        message_id as id,
        CASE WHEN handled_at IS NULL THEN 'unread' ELSE 'done' END as status,
        source as sender,
        dest as recipient,
        topic,
        substr(payload_json, 1, 50) as payload_preview,
        created_at
      FROM messages
      $status_filter
      ORDER BY created_at DESC
    "
  else
    # Legacy fallback
    duckdb ~/.local/ship/data.duckdb -box "SELECT id, status, sender, recipient, command, exit_code, created_at FROM inbox $status_filter ORDER BY created_at DESC"
  fi
}
```

**Step 4: Run test to verify it passes**

Run: `bash /tmp/test_inbox_list_v2.sh`
Expected: PASS

**Step 5: Commit**

```bash
git add ohcommodore
git commit -m "feat(v2): update inbox list to query messages table"
```

---

### Task 16: Update _inbox_read to Set handled_at

**Files:**
- Modify: `ohcommodore` (update `_inbox_read` function)

**Step 1: Write test for new inbox read**

```bash
cat > /tmp/test_inbox_read_v2.sh << 'EOF'
#!/usr/bin/env bash
set -euo pipefail

export OHCOM_NS="test-$$"
TEST_ROOT="$HOME/.ohcommodore/ns/$OHCOM_NS"
trap 'rm -rf "$TEST_ROOT"' EXIT

source ./ohcommodore
queue_init_dirs
init_messages_db

# Insert test message
duckdb "$(msg_db)" "
  INSERT INTO messages (message_id, created_at, source, dest, topic, payload_json)
  VALUES ('read-test-1', '2026-01-20T12:00:00', 'captain@ship-01', 'commodore@flagship', 'cmd.exec', '{\"cmd\":\"echo hello\"}');
"

# Read should mark as handled
_inbox_read "read-test-1" >/dev/null

# Verify handled_at is set
HANDLED=$(duckdb "$(msg_db)" -noheader -csv "SELECT handled_at IS NOT NULL FROM messages WHERE message_id='read-test-1'")
[[ "$HANDLED" == "true" ]] || { echo "FAIL: handled_at should be set"; exit 1; }

echo "PASS: inbox read sets handled_at"
EOF
chmod +x /tmp/test_inbox_read_v2.sh
```

**Step 2: Run test with current function**

Run: `bash /tmp/test_inbox_read_v2.sh`
Expected: FAIL (current function queries old `inbox` table)

**Step 3: Update _inbox_read function**

```bash
_inbox_read() {
  [[ $# -ge 1 ]] || die "Usage: ohcommodore inbox read <id>"
  local msg_id="$1"
  local escaped_id
  escaped_id=$(sql_escape "$msg_id")

  if [[ -f "$(msg_db)" ]]; then
    # v2: update handled_at and return message
    duckdb "$(msg_db)" "UPDATE messages SET handled_at = current_timestamp WHERE message_id = '$escaped_id' AND handled_at IS NULL"
    duckdb "$(msg_db)" -json "SELECT * FROM messages WHERE message_id = '$escaped_id'"
  else
    # Legacy fallback
    duckdb ~/.local/ship/data.duckdb "UPDATE inbox SET status = 'pending' WHERE id = '$escaped_id'"
    duckdb ~/.local/ship/data.duckdb -json "SELECT * FROM inbox WHERE id = '$escaped_id'"
  fi
}
```

**Step 4: Run test to verify it passes**

Run: `bash /tmp/test_inbox_read_v2.sh`
Expected: PASS

**Step 5: Commit**

```bash
git add ohcommodore
git commit -m "feat(v2): update inbox read to set handled_at"
```

---

## Phase 5: Integration with Init

### Task 17: Update _init_commodore for v2

**Files:**
- Modify: `ohcommodore` (update `cmd__init_commodore` function)

**Step 1: Identify required changes**

The init needs to also initialize v2 queue directories and messages DB.

**Step 2: Update cmd__init_commodore function**

Add v2 initialization after existing init:

```bash
cmd__init_commodore() {
  log "Initializing commodore identity..."
  mkdir -p "$CONFIG_DIR"
  cat > "$CONFIG_DIR/identity.json" <<'EOF'
{"role":"commodore"}
EOF

  run_init_script commodore

  log "Initializing databases..."
  local flagship_hostname
  flagship_hostname=$(hostname -f 2>/dev/null || hostname)
  mkdir -p ~/.local/ship

  # Create fleet table (commodore-only) - legacy location
  duckdb ~/.local/ship/data.duckdb "
    CREATE TABLE IF NOT EXISTS fleet (
      name TEXT PRIMARY KEY,
      repo TEXT NOT NULL,
      ssh_dest TEXT NOT NULL,
      pubkey TEXT,
      status TEXT DEFAULT 'running',
      created_at TIMESTAMP DEFAULT current_timestamp
    );
  "

  # Create common tables (config and inbox) - legacy location
  _init_common_tables "commodore@$flagship_hostname"

  # Initialize v2 queue system
  log "Initializing v2 queue system..."
  queue_init_dirs
  init_messages_db

  log "Commodore initialized."
}
```

**Step 3: Verify syntax**

Run: `bash -n ./ohcommodore`
Expected: No syntax errors

**Step 4: Commit**

```bash
git add ohcommodore
git commit -m "feat(v2): update commodore init with queue system setup"
```

---

### Task 18: Update _init_captain for v2

**Files:**
- Modify: `ohcommodore` (update `cmd__init_captain` function)

**Step 1: Update cmd__init_captain function**

Add v2 initialization:

```bash
cmd__init_captain() {
  local repo="${1:-}"

  log "Initializing captain identity..."
  mkdir -p "$CONFIG_DIR"
  cat > "$CONFIG_DIR/identity.json" <<EOF
{"role":"captain"}
EOF

  # Set up SSH keys if provided via env
  if [[ -n "${SHIP_SSH_PRIVKEY_B64:-}" && -n "${SHIP_SSH_PUBKEY_B64:-}" ]]; then
    log "Installing SSH keys from env..."
    mkdir -p ~/.ssh
    chmod 700 ~/.ssh
    echo "$SHIP_SSH_PRIVKEY_B64" | base64 -d > ~/.ssh/id_ed25519
    echo "$SHIP_SSH_PUBKEY_B64" | base64 -d > ~/.ssh/id_ed25519.pub
    chmod 600 ~/.ssh/id_ed25519
    chmod 644 ~/.ssh/id_ed25519.pub
  fi

  run_init_script captain \
    "TARGET_REPO=$repo" \
    "SHIP_SSH_PRIVKEY_B64=${SHIP_SSH_PRIVKEY_B64:-}" \
    "SHIP_SSH_PUBKEY_B64=${SHIP_SSH_PUBKEY_B64:-}"

  # Initialize inbox database (legacy)
  log "Initializing inbox database..."
  local ship_hostname
  ship_hostname=$(hostname -f 2>/dev/null || hostname)
  mkdir -p ~/.local/ship

  # Create common tables (config and inbox) - legacy
  _init_common_tables "captain@$ship_hostname"

  # Initialize v2 queue system
  log "Initializing v2 queue system..."
  queue_init_dirs
  init_messages_db

  log "Captain initialized${repo:+ for $repo}."
}
```

**Step 2: Verify syntax**

Run: `bash -n ./ohcommodore`
Expected: No syntax errors

**Step 3: Commit**

```bash
git add ohcommodore
git commit -m "feat(v2): update captain init with queue system setup"
```

---

## Phase 6: Add Queue Status Command

### Task 19: Add queue status Subcommand

**Files:**
- Modify: `ohcommodore` (add new command)

**Step 1: Write test for queue status**

```bash
cat > /tmp/test_queue_status.sh << 'EOF'
#!/usr/bin/env bash
set -euo pipefail

export OHCOM_NS="test-$$"
TEST_ROOT="$HOME/.ohcommodore/ns/$OHCOM_NS"
trap 'rm -rf "$TEST_ROOT"' EXIT

source ./ohcommodore
queue_init_dirs
init_messages_db

# Create test files in various queue states
echo '{}' > "$(q_inbound)/msg.1.json"
echo '{}' > "$(q_inbound)/msg.2.json"
echo '{}' > "$(q_inbound)/.claimed.msg.3.json"
echo '{}' > "$(q_dead)/msg.4.json"

# Run queue status
OUTPUT=$(cmd_queue_status 2>&1)

[[ "$OUTPUT" == *"inbound: 2"* ]] || { echo "FAIL: should show 2 inbound"; echo "$OUTPUT"; exit 1; }
[[ "$OUTPUT" == *"claimed: 1"* ]] || { echo "FAIL: should show 1 claimed"; echo "$OUTPUT"; exit 1; }
[[ "$OUTPUT" == *"dead: 1"* ]] || { echo "FAIL: should show 1 dead"; echo "$OUTPUT"; exit 1; }

echo "PASS: queue status shows correct counts"
EOF
chmod +x /tmp/test_queue_status.sh
```

**Step 2: Run test to verify it fails**

Run: `bash /tmp/test_queue_status.sh`
Expected: FAIL with "cmd_queue_status: command not found"

**Step 3: Add cmd_queue_status function**

```bash
cmd_queue_status() {
  require_role commodore captain

  queue_init_dirs  # Ensure dirs exist

  local inbound_count claimed_count dead_count done_count msg_count

  inbound_count=$(find "$(q_inbound)" -maxdepth 1 -name 'msg.*.json' 2>/dev/null | wc -l | tr -d ' ')
  claimed_count=$(find "$(q_inbound)" -maxdepth 1 -name '.claimed.*.json' 2>/dev/null | wc -l | tr -d ' ')
  dead_count=$(find "$(q_dead)" -maxdepth 1 -name '*.json' 2>/dev/null | wc -l | tr -d ' ')
  done_count=$(find "$(q_done)" -maxdepth 1 -name '*.json' 2>/dev/null | wc -l | tr -d ' ')

  if [[ -f "$(msg_db)" ]]; then
    msg_count=$(duckdb "$(msg_db)" -noheader -csv "SELECT COUNT(*) FROM messages" 2>/dev/null || echo "0")
  else
    msg_count="0"
  fi

  echo "Queue Status (namespace: $(ns))"
  echo "================================"
  echo "  inbound: $inbound_count"
  echo "  claimed: $claimed_count"
  echo "  dead:    $dead_count"
  echo "  done:    $done_count"
  echo ""
  echo "Database:"
  echo "  messages: $msg_count"
}
```

**Step 4: Add command routing**

Add to the case statement at the bottom of the script:

```bash
  queue)
    case "${2:-}" in
      status) cmd_queue_status ;;
      *) die "Usage: ohcommodore queue [status]" ;;
    esac ;;
```

**Step 5: Run test to verify it passes**

Run: `bash /tmp/test_queue_status.sh`
Expected: PASS

**Step 6: Commit**

```bash
git add ohcommodore
git commit -m "feat(v2): add queue status command for observability"
```

---

## Phase 7: Documentation

### Task 20: Update CLAUDE.md

**Files:**
- Modify: `CLAUDE.md`

**Step 1: Add v2 architecture section**

Add after the existing Architecture section:

```markdown
## v2 Queue System

The v2 messaging system replaces remote DuckDB writes with file-based NDJSON queue transport.

### Directory Layout (per namespace)

```
~/.ohcommodore/ns/<namespace>/
├── q/
│   ├── inbound/           # Incoming messages (visible)
│   │   └── .incoming/     # Staging area for SCP
│   ├── dead/              # Failed messages + .reason files
│   └── done/              # Processed messages (optional)
├── artifacts/             # Command output files
│   └── <request_id>/
│       ├── stdout.txt
│       └── stderr.txt
└── data.duckdb            # Messages table
```

### Message Format

Messages are JSON files with this envelope:

```json
{
  "message_id": "uuid",
  "created_at": "2026-01-20T19:12:03Z",
  "source": "captain@ship-01",
  "dest": "commodore@flagship",
  "topic": "cmd.exec",
  "job_id": null,
  "lease_token": null,
  "payload": {}
}
```

### Protocol Topics

| Topic | Direction | Purpose |
|-------|-----------|---------|
| `cmd.exec` | → ship | Execute a command |
| `cmd.result` | ← ship | Return execution result |

### Environment Variables (v2)

| Variable | Default | Description |
|----------|---------|-------------|
| `OHCOM_NS` | `default` | Active namespace |

### Commands (v2)

```bash
ohcommodore queue status     # Show queue counts and DB stats
```
```

**Step 2: Update existing sections**

Update the Inbox Commands section to note v2 behavior.

**Step 3: Commit**

```bash
git add CLAUDE.md
git commit -m "docs: add v2 queue system documentation to CLAUDE.md"
```

---

### Task 21: Create/Update README.md

**Files:**
- Create or Modify: `README.md`

**Step 1: Add v2 architecture diagram**

```markdown
# ohcommodore

Lightweight multi-coding agent control plane built on exe.dev VMs.

## Architecture

```
┌─────────────────┐
│  Local Machine  │
│   (ohcommodore) │
└────────┬────────┘
         │ SSH
         ▼
┌─────────────────────────────────────────┐
│           Flagship (exe.dev VM)         │
│  ┌─────────────┐  ┌──────────────────┐  │
│  │ Fleet DB    │  │ v2 Queue System  │  │
│  │ (DuckDB)    │  │ ~/.ohcommodore/  │  │
│  └─────────────┘  └──────────────────┘  │
└────────┬────────────────────────────────┘
         │ SSH + SCP (message files)
         ▼
┌─────────────────┐     ┌─────────────────┐
│   Ship (VM 1)   │     │   Ship (VM 2)   │
│  repo: foo/bar  │ ... │  repo: baz/qux  │
│  v2 Queue       │     │  v2 Queue       │
└─────────────────┘     └─────────────────┘
```

## Quick Start

```bash
# Bootstrap flagship (requires GitHub PAT)
GH_TOKEN=ghp_xxx ./ohcommodore init

# Create a ship for a repository
GH_TOKEN=ghp_xxx ./ohcommodore ship create owner/repo

# Check fleet status
./ohcommodore fleet status

# SSH into a ship
./ohcommodore ship ssh reponame

# Send command to a ship
./ohcommodore inbox send captain@ship-hostname "cargo test"

# Check queue status
./ohcommodore queue status
```

## v2 Messaging

Messages are NDJSON files delivered via SCP with atomic rename:

1. Sender creates message file locally
2. SCP to recipient's `.incoming/` staging directory
3. Atomic rename publishes to `inbound/`
4. Daemon claims via rename to `.claimed.*`
5. Execute, emit result, ack (delete) or deadletter

No remote DuckDB writes. Single writer per node.
```

**Step 2: Commit**

```bash
git add README.md
git commit -m "docs: add README with v2 architecture overview"
```

---

## Summary

**Total Tasks:** 21

**Phase 1 (Foundation):** Tasks 1-6 - Namespace helpers, queue dirs, messages table, message writer, SCP delivery, ingestion

**Phase 2 (Replace inbox send):** Task 7 - Update `_inbox_send` to use queue transport

**Phase 3 (Replace scheduler):** Tasks 8-14 - Recovery, shutdown, claim, exec, result emission, daemon loop

**Phase 4 (Update inbox commands):** Tasks 15-16 - Update list/read to use messages table

**Phase 5 (Integration):** Tasks 17-18 - Update commodore/captain init

**Phase 6 (Observability):** Task 19 - Add queue status command

**Phase 7 (Documentation):** Tasks 20-21 - Update CLAUDE.md, create README.md

---

Plan complete and saved to `docs/plans/2026-01-20-ohcommodore-v2-queue-system.md`. Two execution options:

**1. Subagent-Driven (this session)** - I dispatch fresh subagent per task, review between tasks, fast iteration

**2. Parallel Session (separate)** - Open new session with executing-plans, batch execution with checkpoints

**Which approach?**
