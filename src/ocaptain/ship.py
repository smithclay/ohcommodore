"""Ship VM provisioning and bootstrap."""

import json
import shlex
from importlib.resources import files
from io import BytesIO

from fabric import Connection

from .provider import VM, get_provider
from .voyage import Voyage


def bootstrap_ship(
    voyage: Voyage,
    storage: VM,
    index: int,
    tokens: dict[str, str] | None = None,
    telemetry: bool = True,
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
        telemetry: Enable OTLP telemetry tunnel to storage VM (default True)
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

        # 2. Install sshfs, expect (for auto-accepting bypass permissions dialog), and autossh
        c.run(
            "sudo apt-get update -qq && sudo apt-get install -y -qq sshfs expect autossh",
            hide=True,
        )
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

        # 2b. Establish autossh tunnel for OTLP telemetry
        if telemetry:
            c.run(
                f"autossh -f -N -M 0 "
                f"-o StrictHostKeyChecking=no "
                f"-o ServerAliveInterval=15 "
                f"-o ServerAliveCountMax=3 "
                f"-L 4318:localhost:4318 {storage.ssh_dest}"
            )

        # 3. Write ship identity
        c.run("mkdir -p ~/.ocaptain/hooks")
        c.put(BytesIO(ship_id.encode()), f"{home}/.ocaptain/ship_id")
        c.put(BytesIO(voyage.id.encode()), f"{home}/.ocaptain/voyage_id")
        c.put(BytesIO(storage.ssh_dest.encode()), f"{home}/.ocaptain/storage_ssh")

        # 4. Install stop hook
        c.run("cp ~/voyage/on-stop.sh ~/.ocaptain/hooks/on-stop.sh")

        # 4b. Install expect script for auto-accepting bypass permissions dialog
        expect_script = files("ocaptain.templates").joinpath("run_claude.exp").read_text()
        c.put(BytesIO(expect_script.encode()), f"{home}/.ocaptain/run-claude.exp")
        c.run("chmod +x ~/.ocaptain/run-claude.exp")

        # 5. Configure Claude Code
        c.run("mkdir -p ~/.claude")
        # Skip onboarding
        c.run("echo '{\"hasCompletedOnboarding\":true}' > ~/.claude.json")

        env_vars = {"CLAUDE_CODE_TASK_LIST_ID": voyage.task_list_id}

        if telemetry:
            env_vars.update(
                {
                    "CLAUDE_CODE_ENABLE_TELEMETRY": "1",
                    "OTEL_METRICS_EXPORTER": "otlp",
                    "OTEL_LOGS_EXPORTER": "otlp",
                    "OTEL_EXPORTER_OTLP_PROTOCOL": "http/protobuf",
                    "OTEL_EXPORTER_OTLP_ENDPOINT": "http://localhost:4318",
                    "OTEL_RESOURCE_ATTRIBUTES": f"voyage.id={voyage.id},ship.id={ship_id}",
                }
            )

        settings = {
            "env": env_vars,
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
