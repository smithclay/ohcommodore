"""Tests for BoxLite provider implementation."""

from unittest.mock import MagicMock, patch


def test_boxlite_provider_registered() -> None:
    """BoxLiteProvider should register with name 'boxlite'."""
    with patch.dict("sys.modules", {"boxlite": MagicMock()}):
        from ocaptain.provider import _PROVIDERS

        # Import triggers registration
        from ocaptain.providers import boxlite  # noqa: F401

        assert "boxlite" in _PROVIDERS
