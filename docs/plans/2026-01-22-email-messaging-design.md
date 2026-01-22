# Email-Based Messaging for ohcommodore

**Date:** 2026-01-22
**Status:** Draft

## Overview

Replace the current SCP-based file passing and DuckDB message queue with email over SSH tunnels. Each node gets a real email inbox on the flagship's SMTP server.

## Motivation

- **Simpler mental model**: Email is familiar - everyone understands inboxes and sending mail
- **Better debugging**: Inspect messages with mutt, grep, standard unix tools
- **Reliability**: SMTP has built-in queueing; Maildir is battle-tested
- **Extensibility**: Could expose real email addresses or integrate with external systems
- **Code reduction**: Net ~245 lines deleted, replacing complex queue machinery with standard protocols

## Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│ Flagship (OpenSMTPD on localhost:25)                            │
│                                                                 │
│  ~/Maildir/                                                     │
│    └── commodore/new/     <- commodore's inbox                  │
│    └── ship-a1b2c3/new/   <- per-ship virtual mailboxes         │
│    └── ship-x7y8z9/new/                                         │
│                                                                 │
│  OpenSMTPD routes:                                              │
│    commodore@flagship    -> ~/Maildir/commodore/                │
│    *@flagship            -> ~/Maildir/{local-part}/             │
│                                                                 │
└─────────────────────────────────────────────────────────────────┘
         ▲                              ▲
         │ SSH tunnel (port 25)         │ SSH tunnel (port 25)
         │                              │
┌────────┴────────┐            ┌────────┴────────┐
│ Ship a1b2c3     │            │ Ship x7y8z9     │
│                 │            │                 │
│ /etc/hosts:     │            │ /etc/hosts:     │
│ 127.0.0.1 flagship          │ 127.0.0.1 flagship
│                 │            │                 │
│ sendmail ->     │            │ sendmail ->     │
│ tunnel -> SMTP  │            │ tunnel -> SMTP  │
└─────────────────┘            └─────────────────┘
```

### Key Design Decisions

1. **SSH tunnels for transport**: Ships open persistent SSH tunnel to flagship:25. Reuses existing SSH key auth, no SMTP authentication needed.

2. **Maildir for storage**: Standard Maildir format (new/cur/tmp). Files in `new/` are unread, moved to `cur/` when processed. Atomic, well-understood.

3. **OpenSMTPD as SMTP server**: Minimal, secure, easy config. Listens only on localhost.

4. **Email addresses**: `ship-id@flagship` format. The hostname `flagship` is aliased to 127.0.0.1 in /etc/hosts on each ship.

5. **Threading via standard headers**: `Message-ID`, `In-Reply-To`, `References` enable conversation threading.

## Message Format

### cmd.exec (command request)

```
From: commodore@flagship
To: ohcommodore-a1b2c3@flagship
Subject: cmd.exec
Message-ID: <uuid-1234@flagship>
Date: Thu, 23 Jan 2026 10:30:00 +0000
X-Ohcom-Topic: cmd.exec
X-Ohcom-Request-ID: req-5678

cd ~/myrepo && cargo test
```

### cmd.result (command response)

```
From: ohcommodore-a1b2c3@flagship
To: commodore@flagship
Subject: Re: cmd.exec
Message-ID: <uuid-9999@ohcommodore-a1b2c3>
In-Reply-To: <uuid-1234@flagship>
References: <uuid-1234@flagship>
Date: Thu, 23 Jan 2026 10:31:15 +0000
X-Ohcom-Topic: cmd.result
X-Ohcom-Request-ID: req-5678
X-Ohcom-Exit-Code: 0

test result: ok. 42 passed; 0 failed

--stdout--
/path/to/stdout.txt on ohcommodore-a1b2c3
--stderr--
/path/to/stderr.txt on ohcommodore-a1b2c3
```

## Component Details

### OpenSMTPD Configuration (Flagship)

`/etc/smtpd.conf`:
```
# Listen only on localhost (ships tunnel in)
listen on lo

# Virtual users - all mail goes to exedev user's Maildir tree
action "deliver" maildir "/home/exedev/Maildir/%{rcpt.user}"

# Accept all mail for "flagship" domain
match from local for domain "flagship" action "deliver"
match from local for local action "deliver"
```

### SSH Tunnel Service (Ships)

`/etc/systemd/system/ohcom-tunnel.service`:
```ini
[Unit]
Description=ohcommodore SMTP tunnel to flagship
After=network.target

[Service]
User=exedev
ExecStart=/usr/bin/autossh -M 0 -N -L 25:localhost:25 FLAGSHIP_SSH_DEST
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
```

### Scheduler Loop (Simplified)

```bash
scheduler_loop() {
  my_maildir="$HOME/Maildir/$IDENTITY"

  while true; do
    # Process new mail
    for msg in "$my_maildir/new/"*; do
      [[ -f "$msg" ]] || continue

      # Claim by moving to cur/ (Maildir protocol)
      mv "$msg" "$my_maildir/cur/$(basename "$msg"):2,S"

      topic=$(grep -i '^X-Ohcom-Topic:' "$msg" | cut -d: -f2- | tr -d ' ')

      case "$topic" in
        cmd.exec)
          cmd=$(sed -n '/^$/,$p' "$msg" | tail -n +2)
          output=$(eval "$cmd" 2>&1)
          send_result "$msg" "$?" "$output"
          ;;
        cmd.result)
          # Handle result (commodore)
          ;;
      esac
    done

    sleep 10
  done
}
```

## Code Changes

### Additions (~60 lines)

| Component | Lines | Location |
|-----------|-------|----------|
| OpenSMTPD install + config | ~10 | cloudinit/init.sh |
| autossh tunnel service | ~15 | cloudinit/init.sh |
| /etc/hosts entry | ~2 | cloudinit/init.sh |
| `send_mail()` helper | ~20 | ohcommodore |
| `read_maildir()` helper | ~15 | ohcommodore |

### Deletions (~305 lines)

| Component | Lines | Replacement |
|-----------|-------|-------------|
| `write_msg_file()` | ~20 | sendmail |
| `deliver_msg_file()` | ~40 | sendmail (via tunnel) |
| `ingest_queue_file()` | ~35 | Maildir new/ |
| `daemon_*` functions | ~80 | Maildir new→cur |
| `q_*` directory management | ~15 | Maildir handles this |
| `nq_*` functions | ~100 | Direct execution or keep nq |
| DuckDB messages table | ~15 | Email IS storage |

### Retained

- DuckDB `ships` table (fleet registry)
- DuckDB `config` table (identity, settings)

## Debugging

```bash
# See pending messages
ls ~/Maildir/commodore/new/

# Read message history (threaded view)
mutt -f ~/Maildir/commodore/

# Grep for specific request
grep -r "req-5678" ~/Maildir/

# Watch for new mail
watch -n1 'ls ~/Maildir/*/new/'
```

## Migration Path

1. Add email infrastructure to cloudinit (OpenSMTPD, autossh)
2. Implement new `inbox send` using sendmail
3. Implement new scheduler using Maildir polling
4. Delete old queue code
5. Update init scripts

## Security Considerations

Same as current system:
- SSH key authentication gates access
- SMTP server only listens on localhost
- Command execution trusts inbox contents (by design)

## References

- [OpenSMTPD Maildir configuration](https://wiki.archlinux.org/title/OpenSMTPD)
- [Maildir format specification](https://cr.yp.to/proto/maildir.html)
- [minismtp - Rust SMTP library](https://github.com/saefstroem/minismtp)
- [Stalwart SMTP Server](https://github.com/stalwartlabs/smtp-server)
