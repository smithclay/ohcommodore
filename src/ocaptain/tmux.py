"""Tmux session management for interactive Claude ships."""

import shlex
import subprocess  # nosec B404

from .config import CONFIG
from .provider import VM, is_sprite_vm
from .voyage import Voyage


def _get_sprites_org() -> str:
    """Get sprites org from config."""
    if (sprites_config := CONFIG.providers.get("sprites")) and (org := sprites_config.get("org")):
        return org
    raise ValueError("Sprites org not configured")


def _build_ship_command(ship: VM, ship_id: str, voyage: Voyage, oauth_token: str) -> str:
    """Build the command to run Claude on a ship."""
    if is_sprite_vm(ship):
        return _build_sprite_ship_command(ship, ship_id, voyage, oauth_token)
    else:
        return _build_ssh_ship_command(ship, ship_id, voyage, oauth_token)


def _build_claude_command(ship_id: str, voyage: Voyage, oauth_token: str) -> str:
    """Build the command that runs Claude (executed inside tmux on ship)."""
    return (
        f"set -o pipefail && "
        f"cd ~/voyage/workspace && "
        f"echo '[{ship_id}] Starting at '$(date -Iseconds) >> ~/voyage/logs/{ship_id}.log && "
        f"export LANG=C.utf8 LC_CTYPE=C.utf8 LC_ALL=C.utf8 && "
        f"export CLAUDE_CODE_OAUTH_TOKEN={shlex.quote(oauth_token)} && "
        f"export CLAUDE_CODE_TASK_LIST_ID={shlex.quote(voyage.task_list_id)} && "
        f"~/.ocaptain/run-claude.exp "
        f"claude --dangerously-skip-permissions "
        f"--append-system-prompt-file $HOME/voyage/prompt.md "
        f"'Execute STEP 1 now.' "
        f"2>&1 | tee -a ~/voyage/logs/{ship_id}.log ; "
        f"EXIT_CODE=$? && "
        f"echo '' >> ~/voyage/logs/{ship_id}.log && "
        f'echo "[{ship_id}] Exit $EXIT_CODE at $(date -Iseconds)" '
        f">> ~/voyage/logs/{ship_id}.log && "
        f"echo 'Ship {ship_id} finished (exit $EXIT_CODE). Session remains for inspection.' && "
        f"exec bash"  # Keep tmux session alive for inspection
    )


def start_claude_on_ship(ship: VM, ship_id: str, voyage: Voyage, oauth_token: str) -> None:
    """Start Claude in a tmux session on the ship (runs autonomously)."""
    claude_cmd = _build_claude_command(ship_id, voyage, oauth_token)

    # SSH to ship and start tmux session with Claude
    # Create tmux session named 'claude' on the ship
    tmux_cmd = f"tmux new-session -d -s claude {shlex.quote(claude_cmd)}"

    subprocess.run(  # nosec B603 B607
        [
            "ssh",
            "-o",
            "StrictHostKeyChecking=no",
            "-o",
            "UserKnownHostsFile=/dev/null",
            ship.ssh_dest,
            tmux_cmd,
        ],
        check=True,
    )


def _build_ssh_ship_command(ship: VM, ship_id: str, voyage: Voyage, oauth_token: str) -> str:
    """Build command to attach to ship's tmux session (for local observation)."""
    ssh_opts = (
        "-tt "  # Force TTY allocation
        "-o StrictHostKeyChecking=no "
        "-o UserKnownHostsFile=/dev/null "
        "-o ServerAliveInterval=15 "
        "-o ServerAliveCountMax=3"
    )

    # Attach to the ship's tmux session
    return f"ssh {ssh_opts} {ship.ssh_dest} 'tmux attach -t claude'"


def start_claude_on_sprite(ship: VM, ship_id: str, voyage: Voyage, oauth_token: str) -> None:
    """Start Claude in a tmux session on a sprite ship (runs autonomously)."""
    org = _get_sprites_org()
    sprite_name = ship.name

    claude_cmd = _build_claude_command(ship_id, voyage, oauth_token)
    tmux_cmd = f"tmux new-session -d -s claude {shlex.quote(claude_cmd)}"

    subprocess.run(  # nosec B603 B607
        ["sprite", "exec", "-o", org, "-s", sprite_name, "bash", "-c", tmux_cmd],
        check=True,
    )


def _build_sprite_ship_command(ship: VM, ship_id: str, voyage: Voyage, oauth_token: str) -> str:
    """Build command to attach to sprite's tmux session (for local observation)."""
    org = _get_sprites_org()
    sprite_name = ship.name

    return f"sprite exec -o {org} -s {sprite_name} -tty tmux attach -t claude"


def _ship_id_from_vm(voyage: Voyage, ship: VM) -> str | None:
    """Extract ship id (ship-<n>) from VM name."""
    prefix = f"{voyage.id}-ship"
    if not ship.name.startswith(prefix):
        return None

    suffix = ship.name[len(prefix) :]
    if not suffix.isdigit():
        return None

    return f"ship-{suffix}"


def launch_fleet(
    voyage: Voyage,
    ships: list[VM],
    tokens: dict[str, str],
) -> None:
    """Start Claude autonomously on each ship in tmux sessions.

    Claude runs inside tmux on each ship, surviving laptop disconnection.
    Use `ocaptain shell` to attach and observe.

    Raises:
        ValueError: If CLAUDE_CODE_OAUTH_TOKEN is missing from tokens
    """
    oauth_token = tokens.get("CLAUDE_CODE_OAUTH_TOKEN")
    if not oauth_token:
        raise ValueError(
            "CLAUDE_CODE_OAUTH_TOKEN not provided - cannot launch fleet without authentication"
        )

    if not ships:
        return

    # Start Claude on each ship (runs autonomously in tmux on the ship)
    for i, ship in enumerate(ships):
        ship_id = _ship_id_from_vm(voyage, ship) or f"ship-{i}"

        if is_sprite_vm(ship):
            start_claude_on_sprite(ship, ship_id, voyage, oauth_token)
        else:
            start_claude_on_ship(ship, ship_id, voyage, oauth_token)
