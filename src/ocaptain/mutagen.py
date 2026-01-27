"""Mutagen sync session management."""

import subprocess  # nosec: B404
from pathlib import Path


def ensure_ssh_config() -> None:
    """Ensure SSH config exists for ocaptain ships.

    Creates an SSH config entry that:
    - Uses the ocaptain SSH key
    - Disables host key checking (ships are ephemeral)
    - Matches Tailscale IPs (100.*)
    """
    ssh_dir = Path.home() / ".ssh"
    ssh_dir.mkdir(mode=0o700, exist_ok=True)

    config_path = ssh_dir / "config"
    key_path = Path.home() / ".config" / "ocaptain" / "id_ed25519"

    # SSH config block for ocaptain ships
    # nosec: B106 - intentionally permissive for ephemeral VMs on Tailscale
    ocaptain_config = f"""
# ocaptain ship connections (Tailscale IPs)
Host 100.*
    Port 2222
    IdentityFile {key_path}
    StrictHostKeyChecking no
    UserKnownHostsFile /dev/null
    LogLevel ERROR
"""

    # Check if config already contains our block
    marker = "# ocaptain ship connections"
    if config_path.exists():
        existing = config_path.read_text()
        if marker in existing:
            return  # Already configured
        # Append to existing config
        with open(config_path, "a") as f:
            f.write(ocaptain_config)
    else:
        config_path.write_text(ocaptain_config)
        config_path.chmod(0o600)


def _build_create_command(
    local_path: str,
    remote_user: str,
    remote_host: str,
    remote_path: str,
    session_name: str,
    extra_ignores: list[str] | None = None,
) -> list[str]:
    """Build mutagen sync create command."""
    cmd = [
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
    if extra_ignores:
        for ignore in extra_ignores:
            cmd.append(f"--ignore={ignore}")
    return cmd


def create_sync(
    local_path: Path,
    remote_user: str,
    remote_host: str,
    remote_path: str,
    session_name: str,
    extra_ignores: list[str] | None = None,
) -> None:
    """Create a Mutagen sync session from local to remote."""
    # Ensure SSH is configured for Tailscale IPs
    ensure_ssh_config()

    cmd = _build_create_command(
        str(local_path),
        remote_user,
        remote_host,
        remote_path,
        session_name,
        extra_ignores,
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
