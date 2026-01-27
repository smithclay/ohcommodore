"""Tests for Mutagen sync module."""

import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest


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


def test_create_sync_with_extra_ignores() -> None:
    """create_sync should include extra ignore patterns when provided."""
    from ocaptain.mutagen import _build_create_command

    cmd = _build_create_command(
        local_path="/Users/clay/voyages/voyage-abc123/workspace",
        remote_user="ubuntu",
        remote_host="100.64.1.5",
        remote_path="/home/ubuntu/voyage/workspace",
        session_name="voyage-abc123-ship-0",
        extra_ignores=[".claude", ".cache"],
    )

    assert "--ignore=.claude" in cmd
    assert "--ignore=.cache" in cmd
    # Verify extra ignores come after base flags
    assert cmd.index("--ignore=.claude") > cmd.index("--ignore=.git")


def test_terminate_sync_calls_mutagen() -> None:
    """terminate_sync should call mutagen terminate."""
    from ocaptain.mutagen import terminate_sync

    with patch("ocaptain.mutagen.subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=0, stderr="")
        terminate_sync("voyage-abc123-ship-0")

    mock_run.assert_called_once()
    args = mock_run.call_args[0][0]
    assert args == ["mutagen", "sync", "terminate", "voyage-abc123-ship-0"]


def test_create_sync_raises_on_failure() -> None:
    """create_sync should raise CalledProcessError when mutagen fails."""
    from ocaptain.mutagen import create_sync

    with (
        patch("ocaptain.mutagen.subprocess.run") as mock_run,
        patch("ocaptain.mutagen.ensure_ssh_config"),
    ):
        mock_run.side_effect = subprocess.CalledProcessError(1, "mutagen", stderr="sync failed")

        with pytest.raises(subprocess.CalledProcessError):
            create_sync(
                local_path=Path("/local/path"),
                remote_user="ubuntu",
                remote_host="100.64.1.5",
                remote_path="/remote/path",
                session_name="test-session",
            )


def test_terminate_sync_logs_on_failure() -> None:
    """terminate_sync should log debug message when mutagen returns non-zero."""
    from ocaptain.mutagen import terminate_sync

    with patch("ocaptain.mutagen.subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=1, stderr="session not found")

        with patch("ocaptain.mutagen.logger") as mock_logger:
            terminate_sync("nonexistent-session")

            mock_logger.debug.assert_called_once()
            call_args = mock_logger.debug.call_args[0]
            assert "nonexistent-session" in call_args[1]
            assert call_args[2] == 1  # return code


def test_terminate_sync_handles_file_not_found() -> None:
    """terminate_sync should log warning when mutagen is not installed."""
    from ocaptain.mutagen import terminate_sync

    with patch("ocaptain.mutagen.subprocess.run") as mock_run:
        mock_run.side_effect = FileNotFoundError("mutagen not found")

        with patch("ocaptain.mutagen.logger") as mock_logger:
            # Should not raise
            terminate_sync("test-session")

            mock_logger.warning.assert_called_once()
            assert "mutagen not found" in mock_logger.warning.call_args[0][0]
