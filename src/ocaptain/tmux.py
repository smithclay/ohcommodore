"""Tmux session management for interactive Claude ships."""

import shlex

from fabric import Connection

from .provider import VM
from .voyage import Voyage


def _build_ship_command(ship: VM, ship_id: str, voyage: Voyage, oauth_token: str) -> str:
    """Build the command to run Claude on a ship via SSH."""
    # SSH with TTY allocation (required for Claude), keepalives, and skip host key checking
    ssh_opts = (
        "-tt "  # Force TTY allocation
        "-o StrictHostKeyChecking=no "
        "-o UserKnownHostsFile=/dev/null "
        "-o ServerAliveInterval=15 "
        "-o ServerAliveCountMax=3"
    )

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
    """Create tmux session on storage and launch Claude on all ships.

    Args:
        voyage: The voyage configuration
        storage: The storage VM where tmux runs
        ships: List of ship VMs to launch
        tokens: Dict with CLAUDE_CODE_OAUTH_TOKEN
    """
    oauth_token = tokens.get("CLAUDE_CODE_OAUTH_TOKEN", "")
    session = voyage.id

    with Connection(storage.ssh_dest) as c:
        # Create new detached tmux session with first ship
        if ships:
            first_cmd = _build_ship_command(ships[0], "ship-0", voyage, oauth_token)
            c.run(
                f"tmux new-session -d -s {session} -n ship-0 {shlex.quote(first_cmd)}",
                hide=True,
            )

            # Add windows for remaining ships
            for i, ship in enumerate(ships[1:], start=1):
                ship_id = f"ship-{i}"
                cmd = _build_ship_command(ship, ship_id, voyage, oauth_token)
                c.run(
                    f"tmux new-window -t {session} -n {ship_id} {shlex.quote(cmd)}",
                    hide=True,
                )


def attach_session(storage: VM, voyage_id: str, ship_index: int | None = None) -> list[str]:
    """Return SSH command to attach to a voyage's tmux session.

    Args:
        storage: The storage VM
        voyage_id: The voyage ID (also the session name)
        ship_index: Optional ship index to select

    Returns:
        Command list suitable for subprocess.run()
    """
    if ship_index is not None:
        # Attach and select specific window
        tmux_cmd = f"tmux attach -t {voyage_id}:{ship_index}"
    else:
        tmux_cmd = f"tmux attach -t {voyage_id}"

    return ["ssh", "-t", storage.ssh_dest, tmux_cmd]


def kill_session(storage: VM, voyage_id: str) -> None:
    """Kill a tmux session on the storage VM."""
    with Connection(storage.ssh_dest) as c:
        c.run(f"tmux kill-session -t {voyage_id}", warn=True, hide=True)


def add_ships_to_session(
    voyage: Voyage, storage: VM, ships: list[VM], start_index: int, tokens: dict[str, str]
) -> None:
    """Add new ship windows to an existing tmux session.

    Args:
        voyage: The voyage configuration
        storage: The storage VM where tmux runs
        ships: List of new ship VMs to add
        start_index: The starting index for ship numbering
        tokens: Dict with CLAUDE_CODE_OAUTH_TOKEN
    """
    oauth_token = tokens.get("CLAUDE_CODE_OAUTH_TOKEN", "")
    session = voyage.id

    with Connection(storage.ssh_dest) as c:
        for i, ship in enumerate(ships):
            ship_id = f"ship-{start_index + i}"
            cmd = _build_ship_command(ship, ship_id, voyage, oauth_token)
            c.run(
                f"tmux new-window -t {session} -n {ship_id} {shlex.quote(cmd)}",
                hide=True,
                warn=True,
            )
