"""Ship VM provisioning and bootstrap."""

import json
from io import StringIO

from fabric import Connection

from .provider import VM, get_provider
from .voyage import Voyage


def bootstrap_ship(voyage: Voyage, storage: VM, index: int) -> VM:
    """
    Bootstrap a single ship VM.

    1. Provision VM
    2. Mount shared storage via SSHFS
    3. Write ship identity
    4. Install stop hook
    5. Configure Claude Code
    6. Start Claude Code
    """
    provider = get_provider()
    ship_id = f"ship-{index}"
    ship_name = voyage.ship_name(index)

    # 1. Provision
    ship = provider.create(ship_name)

    with Connection(ship.ssh_dest) as c:
        # 2. Mount shared storage
        c.run("mkdir -p ~/voyage ~/tasks")
        c.run(
            f"sshfs {storage.ssh_dest}:/voyage ~/voyage "
            "-o reconnect,ServerAliveInterval=15,ServerAliveCountMax=3"
        )
        c.run(
            f"sshfs {storage.ssh_dest}:~/.claude/tasks/{voyage.task_list_id} ~/tasks "
            "-o reconnect,ServerAliveInterval=15,ServerAliveCountMax=3"
        )

        # 3. Write ship identity
        c.run("mkdir -p ~/.ocaptain/hooks")
        c.put(StringIO(ship_id), "~/.ocaptain/ship_id")
        c.put(StringIO(voyage.id), "~/.ocaptain/voyage_id")
        c.put(StringIO(storage.ssh_dest), "~/.ocaptain/storage_ssh")

        # 4. Install stop hook
        c.run("cp ~/voyage/on-stop.sh ~/.ocaptain/hooks/on-stop.sh")

        # 5. Configure Claude Code
        c.run("mkdir -p ~/.claude")

        settings = {
            "env": {
                "CLAUDE_CODE_TASK_LIST_ID": voyage.task_list_id,
            },
            "hooks": {
                "Stop": [
                    {
                        "matcher": {},
                        "hooks": [
                            {
                                "type": "command",
                                "command": "~/.ocaptain/hooks/on-stop.sh",
                            }
                        ],
                    }
                ]
            },
        }
        c.put(StringIO(json.dumps(settings, indent=2)), "~/.claude/settings.json")

        # 6. Start Claude Code (detached)
        c.run(
            f"nohup claude --prompt-file ~/voyage/prompt.md &> ~/voyage/logs/{ship_id}.log &",
            disown=True,
        )

    return ship


def add_ships(voyage: Voyage, storage: VM, count: int, start_index: int) -> list[VM]:
    """Add additional ships to a running voyage."""
    from concurrent.futures import ThreadPoolExecutor, as_completed

    ships: list[VM] = []

    with ThreadPoolExecutor(max_workers=count) as executor:
        futures = {
            executor.submit(bootstrap_ship, voyage, storage, start_index + i): i
            for i in range(count)
        }

        for future in as_completed(futures):
            try:
                ships.append(future.result())
            except Exception as e:
                print(f"Warning: ship bootstrap failed: {e}")

    return ships
