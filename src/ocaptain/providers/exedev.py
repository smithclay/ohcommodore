"""exe.dev VM provider implementation.

exe.dev operates entirely over SSH - all commands are run via `ssh exe.dev <command>`.
"""

import json
import subprocess  # nosec: B404
import time
from io import BytesIO

from fabric import Connection

from ..config import get_ssh_keypair
from ..provider import VM, Provider, VMStatus, register_provider


def _run_exedev(*args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    """Run an exe.dev command via SSH."""
    cmd = ["ssh", "exe.dev", *args]
    result = subprocess.run(cmd, capture_output=True, text=True, check=False)  # nosec: B603, B607
    if result.returncode != 0 and check:
        import sys

        print(f"exe.dev command failed: {' '.join(args)}", file=sys.stderr)
        print(f"stderr: {result.stderr}", file=sys.stderr)
        print(f"stdout: {result.stdout}", file=sys.stderr)
        result.check_returncode()
    return result


@register_provider("exedev")
class ExeDevProvider(Provider):
    """exe.dev VM provider using SSH commands."""

    def create(self, name: str, *, wait: bool = True) -> VM:
        result = _run_exedev("new", f"--name={name}", "--no-email", "--json")
        data = json.loads(result.stdout)

        vm = VM(
            id=data["vm_name"],  # exe.dev uses name as ID
            name=data["vm_name"],
            ssh_dest=data["ssh_dest"],
            status=VMStatus.RUNNING,
        )

        if wait:
            if not self.wait_ready(vm):
                raise TimeoutError(f"VM {vm.name} did not become SSH-accessible")
            # Inject ocaptain SSH keypair for VM-to-VM communication
            self._inject_ssh_keys(vm)

        return vm

    def _inject_ssh_keys(self, vm: VM) -> None:
        """Inject the ocaptain SSH keypair into the VM and register with exe.dev."""
        private_key, public_key = get_ssh_keypair()

        with Connection(vm.ssh_dest) as c:
            home = c.run("echo $HOME", hide=True).stdout.strip()
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

            # Register with exe.dev by running 'ssh exe.dev' (completes registration)
            c.run("ssh -o StrictHostKeyChecking=no exe.dev whoami", hide=True, warn=True)

            # Update Claude Code to latest (exe.dev base image may be outdated)
            c.run(
                "curl -fsSL https://claude.ai/install.sh | bash",
                hide=True,
            )

    def destroy(self, vm_id: str) -> None:
        _run_exedev("rm", vm_id)

    def get(self, vm_id: str) -> VM | None:
        vms = self.list()
        return next((vm for vm in vms if vm.id == vm_id), None)

    def list(self, prefix: str | None = None) -> list[VM]:
        result = _run_exedev("ls", "--json")

        # Handle empty list case
        if not result.stdout.strip() or "No VMs found" in result.stdout:
            return []

        data = json.loads(result.stdout)
        vm_list = data.get("vms") or []
        vms = [
            VM(
                id=d["vm_name"],
                name=d["vm_name"],
                ssh_dest=d["ssh_dest"],
                status=VMStatus(d.get("status", "unknown")),
            )
            for d in vm_list
        ]

        if prefix:
            vms = [vm for vm in vms if vm.name.startswith(prefix)]

        return vms

    def wait_ready(self, vm: VM, timeout: int = 300) -> bool:
        """Poll until SSH is accessible."""
        start = time.time()

        while time.time() - start < timeout:
            try:
                with Connection(vm.ssh_dest, connect_timeout=5) as c:
                    c.run("echo ready", hide=True)
                return True
            except Exception:
                time.sleep(5)

        return False
