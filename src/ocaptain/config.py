"""Configuration loading and defaults."""

import json
import logging
import os
import subprocess  # nosec: B404
from pathlib import Path
from typing import Annotated, Any

from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)


class OcaptainConfig(BaseModel):
    """Global ocaptain configuration."""

    provider: str = "exedev"
    default_ships: Annotated[int, Field(gt=0)] = 3
    stale_threshold_minutes: Annotated[int, Field(gt=0)] = 30
    telemetry_enabled: bool = True


def load_config() -> OcaptainConfig:
    """Load config from file and environment."""

    config_path = Path.home() / ".config" / "ocaptain" / "config.json"

    data: dict[str, Any] = {}
    if config_path.exists():
        data = json.loads(config_path.read_text())

    # Environment overrides
    if provider := os.environ.get("OCAPTAIN_PROVIDER"):
        data["provider"] = provider

    if telemetry := os.environ.get("OCAPTAIN_TELEMETRY"):
        data["telemetry_enabled"] = telemetry.lower() in ("1", "true", "yes")

    return OcaptainConfig(**data)


def get_ssh_keypair() -> tuple[str, str]:
    """Get or create the ocaptain SSH keypair for VM-to-VM communication.

    Returns (private_key, public_key) as strings.
    """
    config_dir = Path.home() / ".config" / "ocaptain"
    config_dir.mkdir(parents=True, exist_ok=True)

    private_key_path = config_dir / "id_ed25519"
    public_key_path = config_dir / "id_ed25519.pub"
    registered_path = config_dir / "key_registered"

    if not private_key_path.exists():
        # Generate new keypair
        subprocess.run(  # nosec: B603, B607
            [
                "ssh-keygen",
                "-t",
                "ed25519",
                "-f",
                str(private_key_path),
                "-N",
                "",  # No passphrase
                "-C",
                "ocaptain-vm-key",
            ],
            check=True,
            capture_output=True,
        )

    # Register the key with exe.dev if not already done
    if not registered_path.exists():
        public_key = public_key_path.read_text().strip()
        result = subprocess.run(  # nosec: B603, B607
            ["ssh", "exe.dev", "ssh-key", "add", public_key],
            capture_output=True,
            text=True,
        )
        if result.returncode == 0:
            registered_path.write_text("registered")
        else:
            # Key might already be registered, that's OK
            if "already" in result.stderr.lower() or "already" in result.stdout.lower():
                registered_path.write_text("registered")
            else:
                logger.warning(
                    "Failed to register SSH key with exe.dev: %s %s",
                    result.stderr,
                    result.stdout,
                )

    return private_key_path.read_text(), public_key_path.read_text()


# Global config (loaded once)
CONFIG = load_config()
