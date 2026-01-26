"""sprites.dev VM provider implementation.

sprites.dev operates via CLI - all commands are run via `sprite <command>`.
Unlike exe.dev, sprites don't have native SSH access, so we use:
- `sprite exec` for running commands
- `sprite exec -file` for file transfers
- Tailscale for networking between ships and laptop
"""

import re
import subprocess  # nosec: B404
import time
from dataclasses import dataclass
from io import BytesIO
from pathlib import Path
from typing import BinaryIO

from ..config import CONFIG, get_ssh_keypair
from ..provider import VM, Provider, VMStatus, register_provider


def _get_sprites_org() -> str:
    """Get sprites org from config or environment."""
    # Check config first (env already merged into config during load)
    if (sprites_config := CONFIG.providers.get("sprites")) and (org := sprites_config.get("org")):
        return org

    raise ValueError(
        "Sprites org not configured. Set OCAPTAIN_SPRITES_ORG env var "
        "or add providers.sprites.org to ~/.config/ocaptain/config.json"
    )


def _run_sprite(*args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    """Run a sprite CLI command."""
    cmd = ["sprite", *args]
    result = subprocess.run(cmd, capture_output=True, text=True, check=False)  # nosec: B603, B607
    if result.returncode != 0 and check:
        import sys

        print(f"sprite command failed: {' '.join(args)}", file=sys.stderr)
        print(f"stderr: {result.stderr}", file=sys.stderr)
        print(f"stdout: {result.stdout}", file=sys.stderr)
        result.check_returncode()
    return result


@dataclass
class SpriteResult:
    """Result of a sprite exec command."""

    stdout: str
    stderr: str
    return_code: int

    @property
    def ok(self) -> bool:
        return self.return_code == 0


class SpriteConnection:
    """Fabric-like wrapper around sprite exec for running commands on sprites."""

    def __init__(self, org: str, sprite: str):
        self.org = org
        self.sprite = sprite

    def __enter__(self) -> "SpriteConnection":
        return self

    def __exit__(self, *args: object) -> None:
        pass

    def run(self, cmd: str, *, hide: bool = False, warn: bool = False) -> SpriteResult:
        """Run a command on the sprite via sprite exec.

        Args:
            cmd: Shell command to run
            hide: If True, suppress output (ignored for SpriteResult, always captured)
            warn: If True, don't raise on non-zero exit

        Returns:
            SpriteResult with stdout, stderr, and return_code
        """
        result = subprocess.run(  # nosec: B603, B607
            [
                "sprite",
                "exec",
                "-o",
                self.org,
                "-s",
                self.sprite,
                "bash",
                "-c",
                cmd,
            ],
            capture_output=True,
            text=True,
            check=False,
        )

        sprite_result = SpriteResult(
            stdout=result.stdout,
            stderr=result.stderr,
            return_code=result.returncode,
        )

        if not sprite_result.ok and not warn:
            raise RuntimeError(
                f"Command failed on sprite {self.sprite}: {cmd}\n"
                f"stdout: {sprite_result.stdout}\n"
                f"stderr: {sprite_result.stderr}"
            )

        return sprite_result

    def put(self, local: BytesIO | BinaryIO | Path, remote: str) -> None:
        """Upload a file to the sprite.

        Args:
            local: BytesIO, file-like object, or Path to upload
            remote: Remote path on the sprite
        """
        if isinstance(local, Path):
            local_path = str(local)
        elif isinstance(local, BytesIO):
            # Write BytesIO to temp file for sprite exec -file
            import tempfile

            with tempfile.NamedTemporaryFile(delete=False) as tmp:
                tmp.write(local.getvalue())
                local_path = tmp.name
        else:
            # File-like object
            import tempfile

            with tempfile.NamedTemporaryFile(delete=False) as tmp:
                tmp.write(local.read())
                local_path = tmp.name

        try:
            result = subprocess.run(  # nosec: B603, B607
                [
                    "sprite",
                    "exec",
                    "-o",
                    self.org,
                    "-s",
                    self.sprite,
                    "-file",
                    f"{local_path}:{remote}",
                    "true",  # No-op command, file transfer is the goal
                ],
                capture_output=True,
                text=True,
                check=False,
            )

            if result.returncode != 0:
                raise RuntimeError(
                    f"File upload failed to sprite {self.sprite}: {local_path} -> {remote}\n"
                    f"stderr: {result.stderr}"
                )
        finally:
            # Clean up temp file if we created one
            if not isinstance(local, Path):
                import os

                os.unlink(local_path)


@register_provider("sprites")
class SpritesProvider(Provider):
    """sprites.dev VM provider using CLI commands."""

    def __init__(self) -> None:
        self.org = _get_sprites_org()

    def create(self, name: str, *, wait: bool = True) -> VM:
        """Create a new sprite VM."""
        _run_sprite("create", "-o", self.org, "-skip-console", name)

        vm = VM(
            id=name,
            name=name,
            ssh_dest=f"sprite://{self.org}/{name}",  # Synthetic URI for detection
            status=VMStatus.RUNNING,
        )

        if wait:
            if not self.wait_ready(vm):
                raise TimeoutError(f"Sprite {vm.name} did not become accessible")
            self._setup_sprite(vm)

        return vm

    def _setup_sprite(self, vm: VM) -> None:
        """Set up a sprite with SSH keys and Claude."""
        private_key, public_key = get_ssh_keypair()

        with self.get_connection(vm) as c:
            # Get home directory
            result = c.run("echo $HOME", hide=True)
            home = result.stdout.strip()
            ssh_dir = f"{home}/.ssh"

            # Ensure .ssh directory exists with correct permissions
            c.run(f"mkdir -p {ssh_dir} && chmod 700 {ssh_dir}")

            # Write private key
            c.put(BytesIO(private_key.encode()), f"{ssh_dir}/id_ed25519")
            c.run(f"chmod 600 {ssh_dir}/id_ed25519")

            # Append public key to authorized_keys
            c.put(BytesIO(public_key.encode()), f"{ssh_dir}/ocaptain_key.pub")
            c.run(f"cat {ssh_dir}/ocaptain_key.pub >> {ssh_dir}/authorized_keys")
            c.run(f"chmod 600 {ssh_dir}/authorized_keys")

            # Install Claude Code
            c.run(
                "curl -fsSL https://claude.ai/install.sh | bash",
                hide=True,
            )

    def destroy(self, vm_id: str) -> None:
        """Destroy a sprite VM."""
        _run_sprite("destroy", "-o", self.org, "-s", vm_id, "-force", check=False)

    def get(self, vm_id: str) -> VM | None:
        """Get sprite by ID."""
        vms = self.list()
        return next((vm for vm in vms if vm.id == vm_id), None)

    def list(self, prefix: str | None = None) -> list[VM]:
        """List sprites, optionally filtered by name prefix."""
        args = ["list", "-o", self.org]
        if prefix:
            args.extend(["-prefix", prefix])

        result = _run_sprite(*args)

        # Parse sprite list output (one name per line)
        vms: list[VM] = []
        for line in result.stdout.strip().split("\n"):
            name = line.strip()
            if not name:
                continue

            vms.append(
                VM(
                    id=name,
                    name=name,
                    ssh_dest=f"sprite://{self.org}/{name}",
                    status=VMStatus.RUNNING,  # Assume running if listed
                )
            )

        return vms

    def wait_ready(self, vm: VM, timeout: int = 300) -> bool:
        """Poll until sprite is accessible via exec."""
        start = time.time()

        while time.time() - start < timeout:
            try:
                result = subprocess.run(  # nosec: B603, B607
                    [
                        "sprite",
                        "exec",
                        "-o",
                        self.org,
                        "-s",
                        vm.name,
                        "echo",
                        "ready",
                    ],
                    capture_output=True,
                    text=True,
                    timeout=10,
                    check=False,
                )
                if result.returncode == 0 and "ready" in result.stdout:
                    return True
            except subprocess.TimeoutExpired:
                pass
            except Exception:  # nosec B110 - intentional retry loop
                pass

            time.sleep(5)

        return False

    def get_connection(self, vm: VM) -> SpriteConnection:
        """Get a SpriteConnection for the VM."""
        return SpriteConnection(self.org, vm.name)

    def get_public_url(self, vm: VM) -> str:
        """Get or create a public URL for a sprite."""
        # Make URL public
        _run_sprite("url", "update", "--auth", "public", "-o", self.org, "-s", vm.name)

        # Get the URL (format: "URL: https://...\nAuth: ...")
        result = _run_sprite("url", "-o", self.org, "-s", vm.name)

        # Extract URL from output
        if match := re.search(r"URL:\s*(https?://[^\s]+)", result.stdout):
            return match.group(1)

        # Fallback: try to find any URL
        if match := re.search(r"https?://[^\s]+", result.stdout):
            return match.group(0)

        raise ValueError(f"Could not parse URL from sprite url output: {result.stdout}")
