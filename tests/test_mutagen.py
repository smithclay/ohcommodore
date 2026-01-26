"""Tests for Mutagen sync module."""

from unittest.mock import patch


def test_create_sync_builds_correct_command() -> None:
    """create_sync should build correct mutagen command."""
    from ocaptain.mutagen import _build_create_command

    cmd = _build_create_command(
        local_path="/Users/clay/voyages/voyage-abc123/workspace",
        remote_user="ubuntu",
        remote_host="100.64.1.5",
        remote_path="/home/ubuntu/voyage/workspace",
        session_name="voyage-abc123-ship-0",
    )

    assert cmd[0] == "mutagen"
    assert cmd[1] == "sync"
    assert cmd[2] == "create"
    assert "/Users/clay/voyages/voyage-abc123/workspace" in cmd
    assert "ubuntu@100.64.1.5:/home/ubuntu/voyage/workspace" in cmd
    assert "--name=voyage-abc123-ship-0" in cmd


def test_terminate_sync_calls_mutagen() -> None:
    """terminate_sync should call mutagen terminate."""
    from ocaptain.mutagen import terminate_sync

    with patch("ocaptain.mutagen.subprocess.run") as mock_run:
        terminate_sync("voyage-abc123-ship-0")

    mock_run.assert_called_once()
    args = mock_run.call_args[0][0]
    assert args == ["mutagen", "sync", "terminate", "voyage-abc123-ship-0"]
