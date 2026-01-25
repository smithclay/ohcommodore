"""Log viewing and aggregation."""

import re
import shlex

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
