"""Tests for sprites.dev provider implementation."""

import tempfile
from io import BytesIO
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest


def test_sprite_result_ok_true() -> None:
    """SpriteResult.ok should return True when return_code is 0."""
    from ocaptain.providers.sprites import SpriteResult

    result = SpriteResult(stdout="output", stderr="", return_code=0)
    assert result.ok is True


def test_sprite_result_ok_false() -> None:
    """SpriteResult.ok should return False when return_code is non-zero."""
    from ocaptain.providers.sprites import SpriteResult

    result = SpriteResult(stdout="", stderr="error", return_code=1)
    assert result.ok is False


def test_get_sprites_org_raises_when_not_configured() -> None:
    """_get_sprites_org should raise ValueError when org not configured."""
    from ocaptain.providers.sprites import _get_sprites_org

    with patch("ocaptain.providers.sprites.CONFIG") as mock_config:
        mock_config.providers = {}
        with pytest.raises(ValueError, match="Sprites org not configured"):
            _get_sprites_org()


def test_get_sprites_org_returns_org_from_config() -> None:
    """_get_sprites_org should return org from config when set."""
    from ocaptain.providers.sprites import _get_sprites_org

    with patch("ocaptain.providers.sprites.CONFIG") as mock_config:
        mock_config.providers = {"sprites": {"org": "test-org"}}
        assert _get_sprites_org() == "test-org"


def test_sprite_connection_run_raises_on_failure() -> None:
    """SpriteConnection.run should raise RuntimeError on non-zero exit when warn=False."""
    from ocaptain.providers.sprites import SpriteConnection

    with patch("ocaptain.providers.sprites.subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(stdout="", stderr="command failed", returncode=1)

        conn = SpriteConnection("test-org", "test-sprite")
        with pytest.raises(RuntimeError, match="Command failed on sprite"):
            conn.run("failing-command")


def test_sprite_connection_run_returns_result_when_warn() -> None:
    """SpriteConnection.run should return result without raising when warn=True."""
    from ocaptain.providers.sprites import SpriteConnection

    with patch("ocaptain.providers.sprites.subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(stdout="", stderr="warning", returncode=1)

        conn = SpriteConnection("test-org", "test-sprite")
        result = conn.run("some-command", warn=True)

        assert result.ok is False
        assert result.stderr == "warning"


def test_sprite_connection_run_passes_timeout() -> None:
    """SpriteConnection.run should pass timeout to subprocess."""
    from ocaptain.providers.sprites import SpriteConnection

    with patch("ocaptain.providers.sprites.subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(stdout="ok", stderr="", returncode=0)

        conn = SpriteConnection("test-org", "test-sprite")
        conn.run("some-command", timeout=30)

        # Verify timeout was passed to subprocess.run
        call_kwargs = mock_run.call_args[1]
        assert call_kwargs["timeout"] == 30


def test_sprite_connection_put_cleans_up_temp_file() -> None:
    """SpriteConnection.put should clean up temp files after upload."""
    from ocaptain.providers.sprites import SpriteConnection

    with patch("ocaptain.providers.sprites.subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=0, stderr="")

        conn = SpriteConnection("test-org", "test-sprite")
        data = BytesIO(b"test content")

        # Track temp files created
        original_named_temp = tempfile.NamedTemporaryFile

        temp_files_created: list[str] = []

        def tracking_temp(*args: Any, **kwargs: Any) -> Any:
            kwargs["delete"] = False
            tmp = original_named_temp(*args, **kwargs)
            temp_files_created.append(tmp.name)
            return tmp

        with patch("tempfile.NamedTemporaryFile", tracking_temp):
            conn.put(data, "/remote/path")

        # Verify temp file was cleaned up
        assert len(temp_files_created) == 1
        assert not Path(temp_files_created[0]).exists()


def test_sprite_connection_put_raises_on_failure() -> None:
    """SpriteConnection.put should raise RuntimeError on upload failure."""
    from ocaptain.providers.sprites import SpriteConnection

    with patch("ocaptain.providers.sprites.subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=1, stderr="upload failed")

        conn = SpriteConnection("test-org", "test-sprite")
        with pytest.raises(RuntimeError, match="File upload failed"):
            conn.put(BytesIO(b"data"), "/remote/path")


def test_sprite_connection_put_with_path() -> None:
    """SpriteConnection.put should handle Path objects without creating temp files."""
    from ocaptain.providers.sprites import SpriteConnection

    with patch("ocaptain.providers.sprites.subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=0, stderr="")

        conn = SpriteConnection("test-org", "test-sprite")

        # Create a real temp file to use as source
        with tempfile.NamedTemporaryFile(delete=False) as tmp:
            tmp.write(b"test content")
            tmp_path = Path(tmp.name)

        try:
            conn.put(tmp_path, "/remote/path")

            # Verify the command used the original path
            call_args = mock_run.call_args[0][0]
            assert any(str(tmp_path) in arg for arg in call_args)
        finally:
            tmp_path.unlink()
