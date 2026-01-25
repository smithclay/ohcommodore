"""Zellij session management for interactive Claude ships."""

import shlex
from io import BytesIO

from fabric import Connection

from .provider import VM
from .voyage import Voyage


def generate_layout(voyage: Voyage, ships: list[VM], tokens: dict[str, str]) -> str:
    """Generate a zellij layout file for the voyage.

    Creates a layout with one pane per ship, each SSHing to its ship VM
    and running Claude interactively.
    """
    oauth_token = tokens.get("CLAUDE_CODE_OAUTH_TOKEN", "")

    panes = []
    for i, ship in enumerate(ships):
        ship_id = f"ship-{i}"
        # Command that each pane will run
        cmd = _build_ship_command(ship, ship_id, voyage, oauth_token)
        # Escape for KDL string
        cmd_escaped = cmd.replace("\\", "\\\\").replace('"', '\\"')
        panes.append(
            f'        pane name="{ship_id}" command="bash" {{\n'
            f'            args "-c" "{cmd_escaped}"\n'
            f"        }}"
        )

    # Arrange panes: vertical for 2-3 ships, grid for 4+
    if len(ships) <= 3:
        panes_block = "\n".join(panes)
        layout = f'''layout {{
    tab name="{voyage.id}" {{
        pane split_direction="vertical" {{
{panes_block}
        }}
    }}
}}'''
    else:
        # Grid layout: 2 columns
        panes_block = "\n".join(panes)
        layout = f'''layout {{
    tab name="{voyage.id}" {{
        pane split_direction="horizontal" {{
{panes_block}
        }}
    }}
}}'''

    return layout


def _build_ship_command(ship: VM, ship_id: str, voyage: Voyage, oauth_token: str) -> str:
    """Build the command to run Claude on a ship via SSH."""
    # SSH with keepalive settings for reliability
    ssh_opts = "-o ServerAliveInterval=15 -o ServerAliveCountMax=3"

    # The command to run on the ship
    remote_cmd = (
        f"cd ~/voyage/workspace && "
        f"CLAUDE_CODE_OAUTH_TOKEN={shlex.quote(oauth_token)} "
        f"CLAUDE_CODE_TASK_LIST_ID={shlex.quote(voyage.task_list_id)} "
        f"claude --dangerously-skip-permissions "
        f"--system-prompt-file ~/voyage/prompt.md "
        f"'Begin' "
        f"2>&1 | tee -a ~/voyage/logs/{ship_id}.log"
    )

    return f"ssh {ssh_opts} {ship.ssh_dest} {shlex.quote(remote_cmd)}"


def launch_fleet(voyage: Voyage, storage: VM, ships: list[VM], tokens: dict[str, str]) -> None:
    """Create zellij session on storage and launch Claude on all ships.

    Args:
        voyage: The voyage configuration
        storage: The storage VM where zellij runs
        ships: List of ship VMs to launch
        tokens: Dict with CLAUDE_CODE_OAUTH_TOKEN
    """
    layout = generate_layout(voyage, ships, tokens)

    with Connection(storage.ssh_dest) as c:
        home = c.run("echo $HOME", hide=True).stdout.strip()

        # Write layout file
        layout_path = f"{home}/voyage/layout.kdl"
        c.put(BytesIO(layout.encode()), layout_path)

        # Start zellij session in detached mode
        # Use nohup + disown to ensure session survives SSH disconnect
        c.run(
            f"nohup zellij --session {voyage.id} --layout {layout_path} </dev/null &>/dev/null &",
            disown=True,
        )


def attach_session(storage: VM, voyage_id: str, ship_index: int | None = None) -> list[str]:
    """Return SSH command to attach to a voyage's zellij session.

    Args:
        storage: The storage VM
        voyage_id: The voyage ID (also the session name)
        ship_index: Optional ship index to focus

    Returns:
        Command list suitable for subprocess.run()
    """
    if ship_index is not None:
        # Attach and focus specific pane
        zellij_cmd = f"zellij attach {voyage_id} && zellij action focus-pane {ship_index}"
    else:
        zellij_cmd = f"zellij attach {voyage_id}"

    return ["ssh", "-t", storage.ssh_dest, zellij_cmd]


def kill_session(storage: VM, voyage_id: str) -> None:
    """Kill a zellij session on the storage VM."""
    with Connection(storage.ssh_dest) as c:
        # delete-session with --force doesn't prompt
        c.run(f"zellij delete-session {voyage_id} --force", warn=True, hide=True)


def add_ships_to_session(
    voyage: Voyage, storage: VM, ships: list[VM], start_index: int, tokens: dict[str, str]
) -> None:
    """Add new ship panes to an existing zellij session.

    Args:
        voyage: The voyage configuration
        storage: The storage VM where zellij runs
        ships: List of new ship VMs to add
        start_index: The starting index for ship numbering
        tokens: Dict with CLAUDE_CODE_OAUTH_TOKEN
    """
    oauth_token = tokens.get("CLAUDE_CODE_OAUTH_TOKEN", "")

    with Connection(storage.ssh_dest) as c:
        for i, ship in enumerate(ships):
            ship_id = f"ship-{start_index + i}"
            cmd = _build_ship_command(ship, ship_id, voyage, oauth_token)

            # Add a new pane to the existing session and run the command
            # Use zellij action to add pane while session is running
            c.run(
                f"zellij --session {voyage.id} action new-pane -- bash -c {shlex.quote(cmd)}",
                warn=True,
            )
