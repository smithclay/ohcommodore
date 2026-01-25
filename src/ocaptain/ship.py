"""Ship VM provisioning and bootstrap."""

import json
import shlex
from io import BytesIO

from fabric import Connection

from .provider import VM, get_provider
from .voyage import Voyage


def bootstrap_ship(
    voyage: Voyage, storage: VM, index: int, tokens: dict[str, str] | None = None
) -> VM:
    """
    Bootstrap a single ship VM.

    1. Provision VM
    2. Mount shared storage via SSHFS
    3. Write ship identity
    4. Install stop hook
    5. Configure Claude Code (with secure token injection)
    6. Authenticate GitHub CLI (if GH_TOKEN provided)

    Note: Claude is launched separately via zellij from the storage VM.

    Args:
        voyage: The voyage configuration
        storage: The storage VM to mount
        index: Ship index number
        tokens: Dict with CLAUDE_CODE_OAUTH_TOKEN and optionally GH_TOKEN
    """
    if tokens is None:
        tokens = {}
    provider = get_provider()
    ship_id = f"ship-{index}"
    ship_name = voyage.ship_name(index)

    # 1. Provision
    ship = provider.create(ship_name)

    with Connection(ship.ssh_dest) as c:
        # Get home directory for SFTP operations (c.put doesn't expand ~)
        home = c.run("echo $HOME", hide=True).stdout.strip()

        # 2. Install sshfs and expect (for auto-accepting bypass permissions dialog)
        c.run("sudo apt-get update -qq && sudo apt-get install -y -qq sshfs expect", hide=True)
        c.run(f"mkdir -p ~/voyage ~/.claude/tasks/{voyage.task_list_id}")
        # Add storage to known_hosts and mount via sshfs
        sshfs_opts = (
            "reconnect,ServerAliveInterval=15,ServerAliveCountMax=3,StrictHostKeyChecking=no"
        )
        c.run(f"sshfs {storage.ssh_dest}:voyage ~/voyage -o {sshfs_opts}")
        # Mount task directory where Claude Code expects it
        c.run(
            f"sshfs {storage.ssh_dest}:.claude/tasks/{voyage.task_list_id} "
            f"~/.claude/tasks/{voyage.task_list_id} -o {sshfs_opts}"
        )

        # 3. Write ship identity
        c.run("mkdir -p ~/.ocaptain/hooks")
        c.put(BytesIO(ship_id.encode()), f"{home}/.ocaptain/ship_id")
        c.put(BytesIO(voyage.id.encode()), f"{home}/.ocaptain/voyage_id")
        c.put(BytesIO(storage.ssh_dest.encode()), f"{home}/.ocaptain/storage_ssh")

        # 4. Install stop hook
        c.run("cp ~/voyage/on-stop.sh ~/.ocaptain/hooks/on-stop.sh")

        # 4b. Create expect script for auto-accepting bypass permissions dialog
        # Handle multiple TUI dialogs by selecting "continue" options
        expect_script = r"""#!/usr/bin/expect -f
set timeout -1
spawn -noecho {*}$argv
# Loop to handle multiple dialogs (bypass permissions, settings errors, etc.)
# Each dialog has 2 options - we want to select option 2 and continue
expect {
    "Yes, I accept" {
        # Bypass permissions dialog - select option 2
        send "\x1b\[B"
        sleep 0.1
        send "\r"
        exp_continue
    }
    "Continue without these settings" {
        # Settings error dialog - select option 2 to continue
        send "\x1b\[B"
        sleep 0.1
        send "\r"
        exp_continue
    }
    "Begin" {
        # Claude is ready for input - start interaction
    }
    timeout {
        # Fallback - just interact
    }
}
interact
"""
        c.put(BytesIO(expect_script.encode()), f"{home}/.ocaptain/run-claude.exp")
        c.run("chmod +x ~/.ocaptain/run-claude.exp")

        # 5. Configure Claude Code
        c.run("mkdir -p ~/.claude")
        # Skip onboarding
        c.run("echo '{\"hasCompletedOnboarding\":true}' > ~/.claude.json")

        settings = {
            "env": {
                "CLAUDE_CODE_TASK_LIST_ID": voyage.task_list_id,
            },
            "hooks": {
                "Stop": [
                    {
                        "matcher": "",  # Empty string matches all
                        "hooks": [
                            {
                                "type": "command",
                                "command": f"{home}/.ocaptain/hooks/on-stop.sh",
                            }
                        ],
                    }
                ]
            },
        }
        c.put(BytesIO(json.dumps(settings, indent=2).encode()), f"{home}/.claude/settings.json")

        # 6. Authenticate GitHub CLI (if GH_TOKEN provided, for git push)
        if gh_token := tokens.get("GH_TOKEN"):
            c.run(f"echo {shlex.quote(gh_token)} | gh auth login --with-token", hide=True)
            c.run("gh auth setup-git", hide=True)

        # Ship is now ready - Claude will be launched via zellij from storage

    return ship


def add_ships(
    voyage: Voyage,
    storage: VM,
    count: int,
    start_index: int,
    tokens: dict[str, str] | None = None,
) -> list[VM]:
    """Add additional ships to a running voyage."""
    from concurrent.futures import ThreadPoolExecutor, as_completed

    ships: list[VM] = []

    with ThreadPoolExecutor(max_workers=count) as executor:
        futures = {
            executor.submit(bootstrap_ship, voyage, storage, start_index + i, tokens): i
            for i in range(count)
        }

        for future in as_completed(futures):
            try:
                ships.append(future.result())
            except Exception as e:
                print(f"Warning: ship bootstrap failed: {e}")

    return ships
