"""BoxLite micro-VM provider for local development.

BoxLite runs hardware-isolated micro-VMs locally with sub-second boot times.
Each VM gets its own Linux kernel for true isolation.

Requirements:
- macOS 12+ or Linux with KVM
- Python 3.10+
- boxlite>=0.3.0 (pip install ocaptain[boxlite])
"""

from __future__ import annotations

import asyncio
import logging
import shlex
import time
from typing import TYPE_CHECKING

from fabric import Connection

from ..config import CONFIG, get_ssh_keypair
from ..provider import VM, Provider, VMStatus, register_provider

logger = logging.getLogger(__name__)

if TYPE_CHECKING:
    import boxlite as boxlite_module


def _get_boxlite() -> boxlite_module:
    """Import boxlite, raising helpful error if not installed."""
    try:
        import boxlite

        return boxlite
    except ImportError as e:
        raise ImportError(
            "boxlite not installed. Install with: pip install ocaptain[boxlite]"
        ) from e


@register_provider("boxlite")
class BoxLiteProvider(Provider):
    """Local micro-VM provider using BoxLite.

    VMs are ephemeral and only exist while the provider instance lives.
    """

    def __init__(self) -> None:
        self._boxes: dict[str, object] = {}  # boxlite.SimpleBox instances
        self._vms: dict[str, VM] = {}
        self._loop = asyncio.new_event_loop()
        self._config = CONFIG.providers.get("boxlite", {})

    def __del__(self) -> None:
        """Cleanup event loop on provider destruction."""
        if hasattr(self, "_loop") and self._loop and not self._loop.is_closed():
            self._loop.close()

    def create(self, name: str, *, wait: bool = True) -> VM:
        """Create a new BoxLite VM."""
        return self._loop.run_until_complete(self._create(name, wait))

    async def _create(self, name: str, wait: bool) -> VM:
        """Async implementation of create."""
        boxlite = _get_boxlite()

        image = self._config.get("image", "ubuntu:22.04")
        box = boxlite.SimpleBox(image)
        await box.__aenter__()

        try:
            self._boxes[name] = box

            # Bootstrap: install Tailscale, SSH, get IP
            await self._bootstrap_vm(box, name)

            # Get Tailscale IP
            result = await box.exec("tailscale", "ip", "-4")
            ts_ip = result.stdout.strip()

            if not ts_ip:
                raise RuntimeError(
                    f"Tailscale did not return an IP for {name}. VM may not have joined tailnet."
                )

            vm = VM(
                id=name,
                name=name,
                ssh_dest=f"ubuntu@{ts_ip}",
                status=VMStatus.RUNNING,
            )
            self._vms[name] = vm

            if wait and not self.wait_ready(vm):
                raise TimeoutError(f"VM {name} did not become SSH-accessible")

            return vm
        except Exception:
            # Cleanup on failure
            logger.warning("VM creation failed for %s, cleaning up...", name)
            try:
                await box.__aexit__(None, None, None)
            except Exception as cleanup_error:
                logger.error("Failed to cleanup box %s: %s", name, cleanup_error)
            self._boxes.pop(name, None)
            self._vms.pop(name, None)
            raise

    async def _bootstrap_vm(self, box: object, name: str) -> None:
        """Bootstrap VM with Tailscale and SSH."""
        run = box.exec  # type: ignore[attr-defined]

        # Install essentials
        await run("apt-get", "update")
        await run("apt-get", "install", "-y", "openssh-server", "curl", "sudo")

        # Configure sshd on default port 22
        await run("mkdir", "-p", "/run/sshd")
        await run("/usr/sbin/sshd")

        # Create ubuntu user if needed and setup SSH
        await run("bash", "-c", "id ubuntu || useradd -m -s /bin/bash ubuntu")
        await run("bash", "-c", "echo 'ubuntu ALL=(ALL) NOPASSWD:ALL' >> /etc/sudoers")

        # Inject ocaptain SSH keypair
        _, public_key = get_ssh_keypair()
        await run("mkdir", "-p", "/home/ubuntu/.ssh")
        ssh_key_cmd = f"echo {shlex.quote(public_key.strip())} >> /home/ubuntu/.ssh/authorized_keys"
        await run("bash", "-c", ssh_key_cmd)
        await run("chown", "-R", "ubuntu:ubuntu", "/home/ubuntu/.ssh")
        await run("chmod", "700", "/home/ubuntu/.ssh")
        await run("chmod", "600", "/home/ubuntu/.ssh/authorized_keys")

        # Install Tailscale
        await run("bash", "-c", "curl -fsSL https://tailscale.com/install.sh | sh")

        # Start tailscaled with in-memory state (ephemeral)
        await run("bash", "-c", "tailscaled --state=mem: &")
        await run("sleep", "2")  # Give tailscaled time to start

        # Join tailnet
        oauth_secret = CONFIG.tailscale.oauth_secret
        if not oauth_secret:
            raise ValueError("Tailscale OAuth secret required. Set OCAPTAIN_TAILSCALE_OAUTH_SECRET")

        ship_tag = CONFIG.tailscale.ship_tag
        auth_key = f"{oauth_secret}?ephemeral=true&preauthorized=true"
        await run(
            "tailscale",
            "up",
            f"--authkey={auth_key}",
            f"--hostname={name}",
            f"--advertise-tags={ship_tag}",
        )

        # Install Claude Code
        await run("bash", "-c", "curl -fsSL https://claude.ai/install.sh | bash")

    def destroy(self, vm_id: str) -> None:
        """Destroy a BoxLite VM."""
        if vm_id not in self._boxes:
            return
        self._loop.run_until_complete(self._destroy(vm_id))

    async def _destroy(self, vm_id: str) -> None:
        """Async implementation of destroy."""
        box = self._boxes[vm_id]
        run = box.exec  # type: ignore[attr-defined]

        try:
            await run("tailscale", "logout")
        except Exception as e:
            logger.warning("Failed to logout from Tailscale during VM %s destruction: %s", vm_id, e)

        await box.__aexit__(None, None, None)  # type: ignore[attr-defined]
        del self._boxes[vm_id]
        del self._vms[vm_id]

    def get(self, vm_id: str) -> VM | None:
        """Get VM by ID."""
        return self._vms.get(vm_id)

    def list(self, prefix: str | None = None) -> list[VM]:
        """List VMs, optionally filtered by name prefix."""
        vms = self._vms.values()
        if prefix:
            return [v for v in vms if v.name.startswith(prefix)]
        return list(vms)

    def wait_ready(self, vm: VM, timeout: int = 300) -> bool:
        """Wait for VM to be SSH-accessible via Tailscale."""
        start = time.time()
        while time.time() - start < timeout:
            try:
                with Connection(vm.ssh_dest, connect_timeout=5) as c:
                    c.run("echo ready", hide=True)
                return True
            except KeyboardInterrupt:
                raise
            except Exception:
                time.sleep(2)
        return False
