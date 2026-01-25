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
    7. Start Claude Code

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

        # 2. Install sshfs and mount shared storage
        c.run("sudo apt-get update -qq && sudo apt-get install -y -qq sshfs", hide=True)
        c.run("mkdir -p ~/voyage ~/tasks")
        # Add storage to known_hosts and mount via sshfs
        sshfs_opts = (
            "reconnect,ServerAliveInterval=15,ServerAliveCountMax=3,StrictHostKeyChecking=no"
        )
        c.run(f"sshfs {storage.ssh_dest}:voyage ~/voyage -o {sshfs_opts}")
        c.run(
            f"sshfs {storage.ssh_dest}:.claude/tasks/{voyage.task_list_id} ~/tasks -o {sshfs_opts}"
        )

        # 3. Write ship identity
        c.run("mkdir -p ~/.ocaptain/hooks")
        c.put(BytesIO(ship_id.encode()), f"{home}/.ocaptain/ship_id")
        c.put(BytesIO(voyage.id.encode()), f"{home}/.ocaptain/voyage_id")
        c.put(BytesIO(storage.ssh_dest.encode()), f"{home}/.ocaptain/storage_ssh")

        # 4. Install stop hook
        c.run("cp ~/voyage/on-stop.sh ~/.ocaptain/hooks/on-stop.sh")

        # 5. Configure Claude Code
        c.run("mkdir -p ~/.claude")

        env_settings: dict[str, str] = {
            "CLAUDE_CODE_TASK_LIST_ID": voyage.task_list_id,
        }
        # Inject Claude Code OAuth token if available
        if oauth_token := tokens.get("CLAUDE_CODE_OAUTH_TOKEN"):
            env_settings["CLAUDE_CODE_OAUTH_TOKEN"] = oauth_token

        settings = {
            "env": env_settings,
            "hooks": {
                "Stop": [
                    {
                        "matcher": {},
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
        # Secure the settings file (contains sensitive tokens)
        c.run(f"chmod 600 {home}/.claude/settings.json")

        # 6. Authenticate GitHub CLI (if GH_TOKEN provided, for git push)
        if gh_token := tokens.get("GH_TOKEN"):
            c.run(f"echo {shlex.quote(gh_token)} | gh auth login --with-token", hide=True)
            c.run("gh auth setup-git", hide=True)

        # 7. Start Claude Code (detached)
        # Export token as env var since settings.json env is processed after auth check
        oauth_token = tokens.get("CLAUDE_CODE_OAUTH_TOKEN", "")
        c.run(
            f"nohup env CLAUDE_CODE_OAUTH_TOKEN={shlex.quote(oauth_token)} "
            "claude -p --dangerously-skip-permissions"
            f'"$(cat ~/voyage/prompt.md)" &> ~/voyage/logs/{ship_id}.log &',
            disown=True,
        )

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
