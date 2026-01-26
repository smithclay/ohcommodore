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


def _build_ssh_ship_command(ship: VM, ship_id: str, voyage: Voyage, oauth_token: str) -> str:
    """Build the command to run Claude on a ship via SSH."""
    # SSH with TTY allocation (required for Claude), keepalives, and skip host key checking
    ssh_opts = (
        "-tt "  # Force TTY allocation
        "-o StrictHostKeyChecking=no "
        "-o UserKnownHostsFile=/dev/null "
        "-o ServerAliveInterval=15 "
        "-o ServerAliveCountMax=3"
    )

    # Wrapper script that captures errors and keeps window open for debugging
    # Uses unbuffered output via tee and captures exit status
    # Uses expect script to auto-accept the bypass permissions dialog
    # set -o pipefail ensures $? captures Claude/expect exit code, not tee
    remote_cmd = (
        f"set -o pipefail && "
        f"cd ~/voyage/workspace && "
        f"echo '[{ship_id}] Starting at '$(date -Iseconds) >> ~/voyage/logs/{ship_id}.log && "
        f"export LANG=C.utf8 LC_CTYPE=C.utf8 LC_ALL=C.utf8 && "
        f"export CLAUDE_CODE_OAUTH_TOKEN={shlex.quote(oauth_token)} && "
        f"export CLAUDE_CODE_TASK_LIST_ID={shlex.quote(voyage.task_list_id)} && "
        # Use expect script to auto-accept the bypass permissions dialog
        f"~/.ocaptain/run-claude.exp "
        f"claude --dangerously-skip-permissions "
        f"--append-system-prompt-file $HOME/voyage/prompt.md "
        f"'Execute STEP 1 now.' "
        f"2>&1 | tee -a ~/voyage/logs/{ship_id}.log ; "
        # Capture exit status and log it
        f"EXIT_CODE=$? && "
        f"echo '' >> ~/voyage/logs/{ship_id}.log && "
        f'echo "[{ship_id}] Exit $EXIT_CODE at $(date -Iseconds)" '
        f">> ~/voyage/logs/{ship_id}.log && "
        # Keep window open for 60s to allow debugging
        f"echo 'Ship {ship_id} finished (exit $EXIT_CODE). Window closes in 60s...' && "
        f"sleep 60"
    )

    return f"ssh {ssh_opts} {ship.ssh_dest} {shlex.quote(remote_cmd)}"


def _build_sprite_ship_command(ship: VM, ship_id: str, voyage: Voyage, oauth_token: str) -> str:
    """Build the command to run Claude on a sprite ship via sprite exec."""
    org = _get_sprites_org()
    sprite_name = ship.name  # For sprites, name == id

    # Same remote command as SSH version
    remote_cmd = (
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
        f"echo 'Ship {ship_id} finished (exit $EXIT_CODE). Window closes in 60s...' && "
        f"sleep 60"
    )

    return f"sprite exec -o {org} -s {sprite_name} -tty bash -c {shlex.quote(remote_cmd)}"


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
    """Create local tmux session with windows connecting to ships.

    Each window runs either:
    - `ssh -tt ship` (for exedev ships)
    - `sprite exec -tty -s ship` (for sprite ships)
    """
    oauth_token = tokens.get("CLAUDE_CODE_OAUTH_TOKEN", "")
    session = voyage.id

    if not ships:
        return

    # Create first window
    first_ship = ships[0]
    first_ship_id = _ship_id_from_vm(voyage, first_ship) or "ship-0"
    first_cmd = _build_ship_command(first_ship, first_ship_id, voyage, oauth_token)

    subprocess.run(  # nosec B603 B607
        ["tmux", "new-session", "-d", "-s", session, "-n", first_ship_id, first_cmd],
        check=True,
    )

    # Add windows for remaining ships
    for i, ship in enumerate(ships[1:], start=1):
        ship_id = _ship_id_from_vm(voyage, ship) or f"ship-{i}"
        cmd = _build_ship_command(ship, ship_id, voyage, oauth_token)
        subprocess.run(  # nosec B603 B607
            ["tmux", "new-window", "-t", session, "-n", ship_id, cmd],
            check=True,
        )


def attach_session(voyage_id: str, ship_index: int | None = None) -> list[str]:
    """Return command to attach to local tmux session."""
    if ship_index is not None:
        return ["tmux", "attach", "-t", f"{voyage_id}:ship-{ship_index}"]
    return ["tmux", "attach", "-t", voyage_id]
