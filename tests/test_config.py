"""Tests for ocaptain configuration."""

import pytest

from ocaptain.config import OcaptainConfig, load_config


def test_config_has_tailscale_section() -> None:
    """Config should have tailscale section with oauth_secret and ip."""
    config = OcaptainConfig()
    assert hasattr(config, "tailscale")
    assert config.tailscale.oauth_secret is None
    assert config.tailscale.ip is None
    assert config.tailscale.ship_tag == "tag:ocaptain-ship"


def test_config_has_local_section() -> None:
    """Config should have local storage section."""
    config = OcaptainConfig()
    assert hasattr(config, "local")
    assert "voyages" in config.local.workspace_dir


def test_tailscale_oauth_secret_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Tailscale OAuth secret should load from environment."""
    monkeypatch.setenv("OCAPTAIN_TAILSCALE_OAUTH_SECRET", "tskey-client-xxx-yyy")
    config = load_config()
    assert config.tailscale.oauth_secret == "tskey-client-xxx-yyy"


def test_boxlite_config_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    """BoxLite config should have sensible defaults."""
    monkeypatch.delenv("OCAPTAIN_PROVIDER", raising=False)

    from ocaptain.config import load_config

    config = load_config()
    boxlite_cfg = config.providers.get("boxlite", {})

    # Should have defaults even if not explicitly configured
    assert boxlite_cfg.get("image", "ubuntu:22.04") == "ubuntu:22.04"
