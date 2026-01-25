"""Configuration loading and defaults."""

import json
import os
from pathlib import Path
from typing import Any

from pydantic import BaseModel


class OcaptainConfig(BaseModel):
    """Global ocaptain configuration."""

    provider: str = "exedev"
    default_ships: int = 3
    stale_threshold_minutes: int = 30
    ssh_options: list[str] = ["-o", "StrictHostKeyChecking=accept-new"]

    # Provider-specific
    exedev_api_url: str = "https://api.exe.dev"
    exedev_api_key: str | None = None


def load_config() -> OcaptainConfig:
    """Load config from file and environment."""

    config_path = Path.home() / ".config" / "ocaptain" / "config.json"

    data: dict[str, Any] = {}
    if config_path.exists():
        data = json.loads(config_path.read_text())

    # Environment overrides
    if key := os.environ.get("EXEDEV_API_KEY"):
        data["exedev_api_key"] = key
    if provider := os.environ.get("OCAPTAIN_PROVIDER"):
        data["provider"] = provider

    return OcaptainConfig(**data)


# Global config (loaded once)
CONFIG = load_config()
