"""Mutagen sync session management."""

import subprocess  # nosec: B404
from pathlib import Path


def _build_create_command(
    local_path: str,
    remote_user: str,
    remote_host: str,
    remote_path: str,
    session_name: str,
) -> list[str]:
    """Build mutagen sync create command."""
    return [
        "mutagen",
        "sync",
        "create",
        local_path,
        f"{remote_user}@{remote_host}:{remote_path}",
        f"--name={session_name}",
        "--sync-mode=two-way-resolved",
        "--ignore-vcs",
        "--ignore=.git",
    ]


def create_sync(
    local_path: Path,
    remote_user: str,
    remote_host: str,
    remote_path: str,
    session_name: str,
) -> None:
    """Create a Mutagen sync session from local to remote."""
    cmd = _build_create_command(
        str(local_path),
        remote_user,
        remote_host,
        remote_path,
        session_name,
    )
    subprocess.run(cmd, check=True, capture_output=True)  # nosec: B603, B607


def terminate_sync(session_name: str) -> None:
    """Terminate a Mutagen sync session."""
    subprocess.run(  # nosec: B603, B607
        ["mutagen", "sync", "terminate", session_name],
        check=False,
        capture_output=True,
    )


def terminate_voyage_syncs(voyage_id: str) -> None:
    """Terminate all sync sessions for a voyage."""
    # List sessions, filter by voyage prefix, terminate each
    result = subprocess.run(  # nosec: B603, B607
        ["mutagen", "sync", "list"],
        capture_output=True,
        text=True,
        check=False,
    )
    for line in result.stdout.splitlines():
        if voyage_id in line and "Name:" in line:
            session_name = line.split("Name:")[-1].strip()
            terminate_sync(session_name)
