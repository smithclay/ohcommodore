# Email-Based Messaging Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Replace SCP-based file passing with email over SSH tunnels, reducing ~300 lines of queue machinery.

**Architecture:** OpenSMTPD on flagship delivers to per-identity Maildirs. Ships tunnel port 25 via autossh and send mail via sendmail. Scheduler polls Maildir/new/ instead of custom queue directories.

**Tech Stack:** OpenSMTPD, autossh, sendmail, Maildir format, systemd

---

## Task 1: Add OpenSMTPD Setup to Flagship Init

**Files:**
- Modify: `cloudinit/init.sh:125-140` (after DuckDB install section)

**Step 1: Add OpenSMTPD installation and configuration**

Add this block after the DuckDB installation section (around line 139):

```bash
# ──────────────────────────────────────────────────────────
# Email infrastructure (commodore only)
# ──────────────────────────────────────────────────────────

if [[ "${ROLE:-}" == "commodore" ]]; then
  log "Installing OpenSMTPD..."
  if ! need_cmd smtpd; then
    sudo apt-get update -qq
    sudo apt-get install -y opensmtpd
  fi

  log "Configuring OpenSMTPD for local Maildir delivery..."
  sudo tee /etc/smtpd.conf > /dev/null << 'SMTPD_CONF'
# ohcommodore mail configuration
# Listen only on localhost (ships tunnel in via SSH)
listen on lo

# Deliver all mail to user's Maildir tree, organized by recipient
action "deliver" maildir "/home/exedev/Maildir/%{rcpt.user}"

# Accept mail for "flagship" domain and localhost
match from local for domain "flagship" action "deliver"
match from local for local action "deliver"
SMTPD_CONF

  log "Creating base Maildir structure..."
  mkdir -p ~/Maildir/commodore/{new,cur,tmp}

  log "Starting OpenSMTPD..."
  sudo systemctl enable --now opensmtpd
fi
```

**Step 2: Test the change locally (dry run)**

Run: `bash -n cloudinit/init.sh`
Expected: No syntax errors

**Step 3: Commit**

```bash
git add cloudinit/init.sh
git commit -m "feat(init): add OpenSMTPD setup for flagship email infrastructure"
```

---

## Task 2: Add autossh Tunnel Setup for Ships

**Files:**
- Modify: `cloudinit/init.sh` (after SSH keys section, around line 195)

**Step 1: Add autossh installation and tunnel service**

Add this block after the SSH config section:

```bash
# ──────────────────────────────────────────────────────────
# Email infrastructure (captain/ships only)
# ──────────────────────────────────────────────────────────

if [[ "${ROLE:-}" == "captain" && -n "${FLAGSHIP_SSH_DEST:-}" ]]; then
  log "Installing autossh for SMTP tunnel..."
  if ! need_cmd autossh; then
    sudo apt-get update -qq
    sudo apt-get install -y autossh
  fi

  log "Adding flagship to /etc/hosts..."
  if ! grep -q '^127\.0\.0\.1.*flagship' /etc/hosts; then
    echo "127.0.0.1 flagship" | sudo tee -a /etc/hosts > /dev/null
  fi

  log "Creating Maildir for ship identity..."
  mkdir -p ~/Maildir/"${SHIP_ID:-captain}"/{new,cur,tmp}

  log "Creating autossh tunnel service..."
  sudo tee /etc/systemd/system/ohcom-tunnel.service > /dev/null << TUNNEL_EOF
[Unit]
Description=ohcommodore SMTP tunnel to flagship
After=network.target

[Service]
User=exedev
ExecStart=/usr/bin/autossh -M 0 -N -o "ServerAliveInterval 30" -o "ServerAliveCountMax 3" -L 25:localhost:25 ${FLAGSHIP_SSH_DEST}
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
TUNNEL_EOF

  log "Starting SMTP tunnel..."
  sudo systemctl daemon-reload
  sudo systemctl enable --now ohcom-tunnel
fi
```

**Step 2: Test syntax**

Run: `bash -n cloudinit/init.sh`
Expected: No syntax errors

**Step 3: Commit**

```bash
git add cloudinit/init.sh
git commit -m "feat(init): add autossh SMTP tunnel for ships"
```

---

## Task 3: Add Email Helper Functions to ohcommodore

**Files:**
- Modify: `ohcommodore` (add after `uuid_gen()` function, around line 32)

**Step 1: Add Maildir helper functions**

Add these functions after `uuid_gen()`:

```bash
# Maildir helpers
maildir_root() { echo "$HOME/Maildir"; }

maildir_for_identity() {
  local identity="$1"
  # Extract local part from identity (e.g., "captain@ship-abc123" -> "ship-abc123")
  local local_part="${identity#*@}"
  echo "$(maildir_root)/$local_part"
}

ensure_maildir() {
  local maildir="$1"
  mkdir -p "$maildir"/{new,cur,tmp}
}

# Email composition and sending
send_email() {
  local from="$1" to="$2" subject="$3" body="$4"
  shift 4
  # Remaining args are extra headers (key: value format)

  local msg_id="<$(uuid_gen)@${from#*@}>"
  local date_header
  date_header=$(date -R)

  {
    echo "From: $from"
    echo "To: $to"
    echo "Subject: $subject"
    echo "Message-ID: $msg_id"
    echo "Date: $date_header"
    # Add extra headers
    for header in "$@"; do
      echo "$header"
    done
    echo ""
    echo "$body"
  } | sendmail -t

  echo "$msg_id"
}

send_reply() {
  local orig_msg="$1" from="$2" subject="$3" body="$4"
  shift 4

  local orig_msgid orig_from
  orig_msgid=$(grep -i '^Message-ID:' "$orig_msg" | head -1 | sed 's/^[^:]*: *//')
  orig_from=$(grep -i '^From:' "$orig_msg" | head -1 | sed 's/^[^:]*: *//')

  send_email "$from" "$orig_from" "$subject" "$body" \
    "In-Reply-To: $orig_msgid" \
    "References: $orig_msgid" \
    "$@"
}

# Parse email message
email_get_header() {
  local msg="$1" header="$2"
  grep -i "^${header}:" "$msg" | head -1 | sed 's/^[^:]*: *//'
}

email_get_body() {
  local msg="$1"
  # Body starts after first blank line
  sed -n '/^$/,$p' "$msg" | tail -n +2
}
```

**Step 2: Test syntax**

Run: `bash -n ohcommodore`
Expected: No syntax errors

**Step 3: Commit**

```bash
git add ohcommodore
git commit -m "feat: add email helper functions for Maildir and sendmail"
```

---

## Task 4: Rewrite _inbox_send to Use Email

**Files:**
- Modify: `ohcommodore:1265-1313` (replace `_inbox_send` function)

**Step 1: Replace the _inbox_send function**

Replace the existing `_inbox_send` function with:

```bash
_inbox_send() {
  [[ $# -ge 2 ]] || die "Usage: ohcommodore inbox send <recipient> <command>"
  local recipient="$1"
  local command="$2"

  if ! echo "$recipient" | grep -qE '^(captain|commodore)@.+$'; then
    die "Invalid recipient format. Use captain@<ship-id> or commodore@<hostname>"
  fi

  local my_identity request_id
  my_identity=$(_inbox_identity)
  [[ -n "$my_identity" ]] || die "Cannot determine identity"
  request_id=$(uuid_gen)

  local msg_id
  msg_id=$(send_email \
    "$my_identity" \
    "$recipient" \
    "cmd.exec" \
    "$command" \
    "X-Ohcom-Topic: cmd.exec" \
    "X-Ohcom-Request-ID: $request_id")

  echo "Message sent: $request_id -> $recipient"
}
```

**Step 2: Test syntax**

Run: `bash -n ohcommodore`
Expected: No syntax errors

**Step 3: Commit**

```bash
git add ohcommodore
git commit -m "feat: rewrite inbox send to use email via sendmail"
```

---

## Task 5: Rewrite _inbox_list to Read from Maildir

**Files:**
- Modify: `ohcommodore:1233-1263` (replace `_inbox_list` function)

**Step 1: Replace the _inbox_list function**

Replace with:

```bash
_inbox_list() {
  local status_filter="all"
  if [[ "${1:-}" == "--status" && -n "${2:-}" ]]; then
    status_filter="$2"
  fi

  local my_identity maildir
  my_identity=$(_inbox_identity)
  [[ -n "$my_identity" ]] || die "Cannot determine identity"
  maildir=$(maildir_for_identity "$my_identity")

  printf "%-8s %-40s %-25s %-15s %s\n" "STATUS" "ID" "FROM" "TOPIC" "DATE"
  echo "-------- ---------------------------------------- ------------------------- --------------- --------------------"

  local dir status
  for dir in "$maildir/new" "$maildir/cur"; do
    [[ -d "$dir" ]] || continue

    if [[ "$dir" == */new ]]; then
      status="unread"
      [[ "$status_filter" == "done" || "$status_filter" == "handled" ]] && continue
    else
      status="done"
      [[ "$status_filter" == "unread" || "$status_filter" == "pending" ]] && continue
    fi

    for msg in "$dir"/*; do
      [[ -f "$msg" ]] || continue

      local msg_id from topic date_hdr
      msg_id=$(email_get_header "$msg" "Message-ID" | tr -d '<>' | cut -c1-38)
      from=$(email_get_header "$msg" "From" | cut -c1-25)
      topic=$(email_get_header "$msg" "X-Ohcom-Topic")
      date_hdr=$(email_get_header "$msg" "Date" | cut -c1-20)

      printf "%-8s %-40s %-25s %-15s %s\n" "$status" "$msg_id" "$from" "$topic" "$date_hdr"
    done
  done
}
```

**Step 2: Test syntax**

Run: `bash -n ohcommodore`
Expected: No syntax errors

**Step 3: Commit**

```bash
git add ohcommodore
git commit -m "feat: rewrite inbox list to read from Maildir"
```

---

## Task 6: Rewrite _inbox_read to Use Maildir

**Files:**
- Modify: `ohcommodore:1315-1324` (replace `_inbox_read` function)

**Step 1: Replace the _inbox_read function**

Replace with:

```bash
_inbox_read() {
  [[ $# -ge 1 ]] || die "Usage: ohcommodore inbox read <message-id-prefix>"
  local msg_prefix="$1"

  local my_identity maildir
  my_identity=$(_inbox_identity)
  [[ -n "$my_identity" ]] || die "Cannot determine identity"
  maildir=$(maildir_for_identity "$my_identity")

  # Find message by ID prefix in new/ or cur/
  local found=""
  for dir in "$maildir/new" "$maildir/cur"; do
    [[ -d "$dir" ]] || continue
    for msg in "$dir"/*; do
      [[ -f "$msg" ]] || continue
      local msg_id
      msg_id=$(email_get_header "$msg" "Message-ID")
      if [[ "$msg_id" == *"$msg_prefix"* ]]; then
        found="$msg"
        break 2
      fi
    done
  done

  [[ -n "$found" ]] || die "No message found matching '$msg_prefix'"

  # If in new/, move to cur/ (mark as read)
  if [[ "$found" == */new/* ]]; then
    local filename
    filename=$(basename "$found")
    mv "$found" "$maildir/cur/${filename}:2,S"
    found="$maildir/cur/${filename}:2,S"
  fi

  # Output message content
  cat "$found"
}
```

**Step 2: Test syntax**

Run: `bash -n ohcommodore`
Expected: No syntax errors

**Step 3: Commit**

```bash
git add ohcommodore
git commit -m "feat: rewrite inbox read to use Maildir"
```

---

## Task 7: Rewrite Scheduler to Poll Maildir

**Files:**
- Modify: `ohcommodore:1330-1403` (replace `cmd__scheduler` function)

**Step 1: Replace the cmd__scheduler function**

Replace with:

```bash
cmd__scheduler() {
  require_role commodore captain

  local my_identity maildir poll_interval
  my_identity=$(_inbox_identity)
  [[ -n "$my_identity" ]] || die "No identity configured"
  maildir=$(maildir_for_identity "$my_identity")
  ensure_maildir "$maildir"

  poll_interval=$(duckdb "$(msg_db)" -noheader -csv "SELECT value FROM config WHERE key = 'POLL_INTERVAL_SEC'" 2>/dev/null || echo "10")

  trap 'log "Scheduler shutting down"; exit 0' SIGTERM SIGINT

  log "Email scheduler starting for $my_identity (poll: ${poll_interval}s, maildir: $maildir)"

  while true; do
    # Process new mail
    for msg in "$maildir/new/"*; do
      [[ -f "$msg" ]] || continue

      local filename topic
      filename=$(basename "$msg")

      # Claim by moving to cur/ (Maildir protocol)
      mv "$msg" "$maildir/cur/${filename}:2,S"
      msg="$maildir/cur/${filename}:2,S"

      topic=$(email_get_header "$msg" "X-Ohcom-Topic")

      case "$topic" in
        cmd.exec)
          _scheduler_handle_cmd_exec "$msg"
          ;;
        cmd.result)
          _scheduler_handle_cmd_result "$msg"
          ;;
        *)
          log "Unknown topic '$topic' in message, skipping"
          ;;
      esac
    done

    sleep "$poll_interval"
  done
}

_scheduler_handle_cmd_exec() {
  local msg="$1"

  local cmd request_id source started_at ended_at exit_code output
  cmd=$(email_get_body "$msg")
  request_id=$(email_get_header "$msg" "X-Ohcom-Request-ID")
  source=$(email_get_header "$msg" "From")

  [[ -n "$cmd" ]] || { log "Empty command in message, skipping"; return; }

  log "Executing: $cmd"
  started_at=$(date -u +%Y-%m-%dT%H:%M:%SZ)

  # Create artifact directory for output
  local artifact_dir
  artifact_dir="$(artifacts_root)/${request_id:-$(uuid_gen)}"
  mkdir -p "$artifact_dir"

  # Execute command with timeout, capture output
  set +e
  timeout "$CMD_TIMEOUT_S" bash -c "$cmd" > "$artifact_dir/stdout.txt" 2> "$artifact_dir/stderr.txt"
  exit_code=$?
  set -e

  ended_at=$(date -u +%Y-%m-%dT%H:%M:%SZ)

  log "Command finished: exit=$exit_code"

  # Send result back to source
  if [[ -n "$source" ]]; then
    local my_identity hostname body
    my_identity=$(_inbox_identity)
    hostname=$(hostname -f 2>/dev/null || hostname)

    body=$(cat << RESULT_EOF
Exit code: $exit_code
Started: $started_at
Ended: $ended_at

--stdout--
${hostname}:${artifact_dir}/stdout.txt

--stderr--
${hostname}:${artifact_dir}/stderr.txt
RESULT_EOF
)

    send_reply "$msg" "$my_identity" "Re: cmd.exec" "$body" \
      "X-Ohcom-Topic: cmd.result" \
      "X-Ohcom-Request-ID: ${request_id:-unknown}" \
      "X-Ohcom-Exit-Code: $exit_code"

    log "Result sent to $source"
  fi
}

_scheduler_handle_cmd_result() {
  local msg="$1"

  local request_id exit_code from
  request_id=$(email_get_header "$msg" "X-Ohcom-Request-ID")
  exit_code=$(email_get_header "$msg" "X-Ohcom-Exit-Code")
  from=$(email_get_header "$msg" "From")

  log "Received result: request=$request_id exit=$exit_code from=$from"
  # Results are stored in Maildir for later inspection
}
```

**Step 2: Test syntax**

Run: `bash -n ohcommodore`
Expected: No syntax errors

**Step 3: Commit**

```bash
git add ohcommodore
git commit -m "feat: rewrite scheduler to poll Maildir for email messages"
```

---

## Task 8: Update Identity to Use Ship ID Format

**Files:**
- Modify: `ohcommodore:1326-1328` (update `_inbox_identity` function)

**Step 1: Update _inbox_identity**

The current implementation reads from DuckDB config, which is fine. But we need to ensure the identity format works with email. Update:

```bash
_inbox_identity() {
  local identity
  identity=$(duckdb "$(msg_db)" -noheader -csv "SELECT value FROM config WHERE key = 'IDENTITY'" 2>/dev/null)

  # Ensure we have a valid email-style identity
  if [[ -z "$identity" ]]; then
    local role hostname
    role=$(get_role)
    hostname=$(hostname -f 2>/dev/null || hostname)
    identity="${role}@${hostname}"
  fi

  echo "$identity"
}
```

**Step 2: Test syntax**

Run: `bash -n ohcommodore`
Expected: No syntax errors

**Step 3: Commit**

```bash
git add ohcommodore
git commit -m "fix: ensure inbox identity returns valid email format"
```

---

## Task 9: Delete Old Queue Code

**Files:**
- Modify: `ohcommodore` (delete lines 103-108, 114-122, 143-564 approximately)

**Step 1: Remove old queue directory helpers**

Delete these functions (lines ~103-122):
- `q_root()`
- `q_inbound()`
- `q_incoming()`
- `q_dead()`
- `q_outbound()`
- `nq_dir()`
- `queue_init_dirs()`

**Step 2: Remove old message/queue functions**

Delete these functions (lines ~143-564):
- `write_msg_file()`
- `deliver_msg_file()`
- `ingest_queue_file()`
- `daemon_shutdown_handler()`
- `daemon_recover_claimed()`
- `daemon_claim_one()`
- `emit_cmd_result()`
- `fetch_pending_cmd_exec()`
- `complete_cmd_exec()`
- `nq_submit_job()`
- `nq_check_jobs()`
- `daemon_ingest_one()`
- `daemon_process_one()`

**Step 3: Remove messages table from init_messages_db**

Update `init_messages_db()` to only create config table (messages are now in Maildir):

```bash
init_messages_db() {
  local db
  db="$(msg_db)"
  mkdir -p "$(dirname "$db")"
  duckdb "$db" "
    CREATE TABLE IF NOT EXISTS config (
      key TEXT PRIMARY KEY,
      value TEXT
    );
  "
}
```

**Step 4: Test syntax**

Run: `bash -n ohcommodore`
Expected: No syntax errors

**Step 5: Commit**

```bash
git add ohcommodore
git commit -m "refactor: remove old SCP-based queue machinery

Delete ~300 lines of queue code replaced by email:
- Queue directory helpers (q_*, nq_*)
- Message file creation/delivery (write_msg_file, deliver_msg_file)
- Daemon claim/process functions
- DuckDB messages table (now using Maildir)
"
```

---

## Task 10: Update init_common for Email

**Files:**
- Modify: `ohcommodore:1128-1149` (update `init_common` function)

**Step 1: Update init_common to create Maildir**

Update the function:

```bash
init_common() {
  local identity="$1"
  local db maildir
  db="$(msg_db)"
  maildir=$(maildir_for_identity "$identity")

  # Create Maildir structure
  ensure_maildir "$maildir"

  # Initialize config database
  init_messages_db

  local escaped_identity
  escaped_identity=$(sql_escape "$identity")

  duckdb "$db" "
    INSERT INTO config (key, value) VALUES ('POLL_INTERVAL_SEC', '10')
      ON CONFLICT (key) DO NOTHING;
    INSERT INTO config (key, value) VALUES ('IDENTITY', '$escaped_identity')
      ON CONFLICT (key) DO NOTHING;
  "
}
```

**Step 2: Test syntax**

Run: `bash -n ohcommodore`
Expected: No syntax errors

**Step 3: Commit**

```bash
git add ohcommodore
git commit -m "feat: update init_common to create Maildir structure"
```

---

## Task 11: Remove nq from cloudinit/init.sh

**Files:**
- Modify: `cloudinit/init.sh:141-150` (remove nq installation)

**Step 1: Delete nq installation block**

Remove these lines:

```bash
log "Installing nq (job queue utility)..."
if ! need_cmd nq; then
  git clone --depth 1 https://github.com/leahneukirchen/nq /tmp/nq || die "Failed to clone nq repository"
  cd /tmp/nq || die "Failed to cd to nq directory"
  make || die "Failed to build nq"
  sudo make install PREFIX=/usr/local || die "Failed to install nq"
  rm -rf /tmp/nq
else
  log "nq already installed — skipping"
fi
```

**Step 2: Test syntax**

Run: `bash -n cloudinit/init.sh`
Expected: No syntax errors

**Step 3: Commit**

```bash
git add cloudinit/init.sh
git commit -m "chore: remove nq installation (replaced by direct execution)"
```

---

## Task 12: Update CLAUDE.md Documentation

**Files:**
- Modify: `CLAUDE.md`

**Step 1: Update the v2 Queue System section**

Replace the "v2 Queue System" section with:

```markdown
## Email Messaging System

The messaging system uses email over SSH tunnels for inter-node communication.

### Architecture

- **Flagship**: Runs OpenSMTPD on localhost:25, delivers to per-identity Maildirs
- **Ships**: SSH tunnel to flagship:25 via autossh, send mail via sendmail
- **Storage**: Standard Maildir format (`~/Maildir/<identity>/{new,cur,tmp}`)

### Message Format

Messages are standard RFC 5322 emails with custom `X-Ohcom-*` headers:

```
From: commodore@flagship
To: ship-a1b2c3@flagship
Subject: cmd.exec
X-Ohcom-Topic: cmd.exec
X-Ohcom-Request-ID: req-123

cd ~/myrepo && cargo test
```

### Debugging

```bash
# See pending messages
ls ~/Maildir/*/new/

# Read message history
mutt -f ~/Maildir/commodore/

# Watch for new mail
watch -n1 'ls ~/Maildir/*/new/'
```
```

**Step 2: Remove outdated sections**

Remove:
- "v2 Queue System" section
- "Directory Layout (per namespace)" section showing q/ directories
- References to nq

**Step 3: Commit**

```bash
git add CLAUDE.md
git commit -m "docs: update CLAUDE.md for email-based messaging"
```

---

## Task 13: Final Integration Test

**Step 1: Syntax check all modified files**

Run:
```bash
bash -n ohcommodore && bash -n cloudinit/init.sh && echo "All syntax OK"
```
Expected: "All syntax OK"

**Step 2: Run shellcheck**

Run:
```bash
shellcheck ohcommodore cloudinit/init.sh
```
Expected: No errors (warnings OK)

**Step 3: Final commit and tag**

```bash
git add -A
git commit -m "feat: complete email-based messaging migration

- OpenSMTPD on flagship for local mail delivery
- autossh tunnels from ships to flagship:25
- Maildir storage replaces DuckDB messages table
- ~300 lines of queue code removed
- Standard email tools (mutt, grep) for debugging
"
```

---

## Summary

| Task | Description | Lines Changed |
|------|-------------|---------------|
| 1 | OpenSMTPD setup in init.sh | +25 |
| 2 | autossh tunnel for ships | +30 |
| 3 | Email helper functions | +60 |
| 4 | Rewrite _inbox_send | +20 (replace 50) |
| 5 | Rewrite _inbox_list | +35 (replace 30) |
| 6 | Rewrite _inbox_read | +35 (replace 10) |
| 7 | Rewrite scheduler | +80 (replace 75) |
| 8 | Update _inbox_identity | +10 (replace 3) |
| 9 | Delete old queue code | -300 |
| 10 | Update init_common | +5 (replace 20) |
| 11 | Remove nq from init.sh | -10 |
| 12 | Update documentation | ~50 |
| 13 | Integration test | 0 |

**Net change:** ~-100 lines, dramatically simpler architecture
