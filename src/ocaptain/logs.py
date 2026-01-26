"""Log viewing and aggregation."""

import re
import shlex
import subprocess  # nosec B404
from pathlib import Path

from fabric import Connection

from .provider import VM
from .voyage import Voyage

# Valid ship_id pattern: "ship-" followed by digits
_SHIP_ID_PATTERN = re.compile(r"^ship-\d+$")


def view_logs(
    storage: VM,
    voyage: Voyage,
    ship_id: str | None = None,
    follow: bool = False,
    grep: str | None = None,
    tail: int | None = None,
) -> None:
    """View aggregated logs from storage VM."""
    # Validate ship_id to prevent path traversal
    if ship_id is not None and not _SHIP_ID_PATTERN.match(ship_id):
        raise ValueError(f"Invalid ship_id format: {ship_id}")

    pattern = f"{ship_id}.log" if ship_id else "*.log"
    path = f"~/voyage/logs/{pattern}"

    # Build command
    if follow:
        cmd = f"tail -f {path}"
    elif tail:
        cmd = f"tail -n {tail} {path}"
    else:
        cmd = f"cat {path}"

    # Use shlex.quote to prevent command injection
    if grep:
        cmd = f"{cmd} | grep --color=always {shlex.quote(grep)}"

    with Connection(storage.ssh_dest) as c:
        # Use pty=True for follow mode to handle Ctrl+C properly
        c.run(cmd, pty=follow)


def view_logs_local(
    voyage_dir: Path,
    voyage: Voyage,
    ship_id: str | None = None,
    follow: bool = False,
    grep: str | None = None,
    tail: int | None = None,
) -> None:
    """View aggregated logs from local voyage directory."""
    # Validate ship_id to prevent path traversal
    if ship_id is not None and not _SHIP_ID_PATTERN.match(ship_id):
        raise ValueError(f"Invalid ship_id format: {ship_id}")

    logs_dir = voyage_dir / "logs"
    if not logs_dir.exists():
        print(f"No logs directory found: {logs_dir}")
        return

    # Get log files
    if ship_id:
        log_files = [logs_dir / f"{ship_id}.log"]
        if not log_files[0].exists():
            print(f"Log file not found: {log_files[0]}")
            return
    else:
        log_files = sorted(logs_dir.glob("*.log"))
        if not log_files:
            print("No log files found")
            return

    # Build command
    file_paths = [str(f) for f in log_files]

    if follow:
        cmd = ["tail", "-f"] + file_paths
    elif tail:
        cmd = ["tail", "-n", str(tail)] + file_paths
    else:
        cmd = ["cat"] + file_paths

    if grep:
        # Use shell to pipe through grep
        cmd_str = " ".join(shlex.quote(c) for c in cmd)
        cmd_str = f"{cmd_str} | grep --color=always {shlex.quote(grep)}"
        subprocess.run(cmd_str, shell=True)  # nosec B602 - grep pattern is quoted
    else:
        subprocess.run(cmd)  # nosec B603, B607
