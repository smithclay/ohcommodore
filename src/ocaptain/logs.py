"""Log viewing and aggregation."""

from fabric import Connection

from .provider import VM
from .voyage import Voyage


def view_logs(
    storage: VM,
    voyage: Voyage,
    ship_id: str | None = None,
    follow: bool = False,
    grep: str | None = None,
    tail: int | None = None,
) -> None:
    """View aggregated logs from storage VM."""

    pattern = f"{ship_id}.log" if ship_id else "*.log"
    path = f"/voyage/logs/{pattern}"

    # Build command
    if follow:
        cmd = f"tail -f {path}"
    elif tail:
        cmd = f"tail -n {tail} {path}"
    else:
        cmd = f"cat {path}"

    if grep:
        cmd = f"{cmd} | grep --color=always '{grep}'"

    with Connection(storage.ssh_dest) as c:
        # Use pty=True for follow mode to handle Ctrl+C properly
        c.run(cmd, pty=follow)
