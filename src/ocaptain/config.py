"""Configuration loading and defaults."""

import json
import logging
import os
import subprocess  # nosec: B404
from pathlib import Path
from typing import Annotated, Any

from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)


class TailscaleConfig(BaseModel):
    """Tailscale configuration for ship networking."""

    oauth_secret: str | None = None  # OCAPTAIN_TAILSCALE_OAUTH_SECRET
    ip: str | None = None  # Auto-detected or manual override
    ship_tag: str = "tag:ocaptain-ship"  # Tag applied to ships for ACL isolation


class LocalStorageConfig(BaseModel):
    """Local storage configuration (laptop as storage)."""

    workspace_dir: str = "~/voyages"
    user: str | None = None  # SSH user, defaults to current user
    otlp_port: int = 4318


class OcaptainConfig(BaseModel):
    """Global ocaptain configuration."""

    provider: str = "exedev"
    default_ships: Annotated[int, Field(gt=0)] = 3
    stale_threshold_minutes: Annotated[int, Field(gt=0)] = 30
    telemetry_enabled: bool = True
    providers: dict[str, dict[str, str]] = {}  # {"sprites": {"org": "my-org"}}
    tailscale: TailscaleConfig = TailscaleConfig()
    local: LocalStorageConfig = LocalStorageConfig()


def _find_tailscale() -> str | None:
    """Find tailscale binary, including macOS app bundle."""
    import shutil

    # Check standard PATH
    if path := shutil.which("tailscale"):
        return path

    # Check macOS app bundle
    macos_path = "/Applications/Tailscale.app/Contents/MacOS/Tailscale"
    if Path(macos_path).exists():
        return macos_path

    return None


def _get_tailscale_ip() -> str | None:
    """Auto-detect tailscale IP if available."""
    tailscale = _find_tailscale()
    if not tailscale:
        return None

    try:
        result = subprocess.run(  # nosec: B603, B607
            [tailscale, "ip", "-4"],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
        if result.returncode == 0:
            return result.stdout.strip()
    except (FileNotFoundError, subprocess.TimeoutExpired):
        pass
    return None


def _load_dotenv_files() -> None:
    """Load .env files into environment (cwd first, then ~/.config/ocaptain/.env)."""
    from dotenv import load_dotenv

    # Load in reverse priority order (later loads override earlier)
    config_env = Path.home() / ".config" / "ocaptain" / ".env"
    if config_env.exists():
        load_dotenv(config_env, override=False)

    cwd_env = Path.cwd() / ".env"
    if cwd_env.exists():
        load_dotenv(cwd_env, override=False)


def load_config() -> OcaptainConfig:
    """Load config from file and environment."""

    # Load .env files first
    _load_dotenv_files()

    config_path = Path.home() / ".config" / "ocaptain" / "config.json"

    data: dict[str, Any] = {}
    if config_path.exists():
        data = json.loads(config_path.read_text())

    # Environment overrides
    if provider := os.environ.get("OCAPTAIN_PROVIDER"):
        data["provider"] = provider

    if telemetry := os.environ.get("OCAPTAIN_TELEMETRY"):
        data["telemetry_enabled"] = telemetry.lower() in ("1", "true", "yes")

    # Load sprites org from environment
    if sprites_org := os.environ.get("OCAPTAIN_SPRITES_ORG"):
        data.setdefault("providers", {}).setdefault("sprites", {})["org"] = sprites_org

    # Load tailscale config from environment
    if oauth_secret := os.environ.get("OCAPTAIN_TAILSCALE_OAUTH_SECRET"):
        data.setdefault("tailscale", {})["oauth_secret"] = oauth_secret

    # Auto-detect tailscale IP if not set
    tailscale_data = data.get("tailscale", {})
    if not tailscale_data.get("ip") and (detected_ip := _get_tailscale_ip()):
        data.setdefault("tailscale", {})["ip"] = detected_ip

    # Set default user to current user
    local_data = data.get("local", {})
    if not local_data.get("user"):
        data.setdefault("local", {})["user"] = os.environ.get("USER", "user")

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
