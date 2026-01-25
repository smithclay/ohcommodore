"""exe.dev VM provider implementation."""

import time

import httpx
from fabric import Connection

from ..config import CONFIG
from ..provider import VM, Provider, register_provider


@register_provider("exedev")
class ExeDevProvider(Provider):
    """exe.dev VM provider."""

    def __init__(self) -> None:
        self.api_url = CONFIG.exedev_api_url
        self.api_key = CONFIG.exedev_api_key
        if not self.api_key:
            raise ValueError("EXEDEV_API_KEY not set")

        self.client = httpx.Client(
            base_url=self.api_url,
            headers={"Authorization": f"Bearer {self.api_key}"},
            timeout=60.0,
        )

    def create(self, name: str, *, wait: bool = True) -> VM:
        resp = self.client.post("/vms", json={"name": name})
        resp.raise_for_status()
        data = resp.json()

        vm = VM(
            id=data["id"],
            name=name,
            ssh_dest=data["ssh_dest"],
            status=data["status"],
        )

        if wait:
            self.wait_ready(vm)

        return vm

    def destroy(self, vm_id: str) -> None:
        resp = self.client.delete(f"/vms/{vm_id}")
        resp.raise_for_status()

    def get(self, vm_id: str) -> VM | None:
        resp = self.client.get(f"/vms/{vm_id}")
        if resp.status_code == 404:
            return None
        resp.raise_for_status()
        data = resp.json()
        return VM(
            id=data["id"],
            name=data["name"],
            ssh_dest=data["ssh_dest"],
            status=data["status"],
        )

    def list(self, prefix: str | None = None) -> list[VM]:
        resp = self.client.get("/vms")
        resp.raise_for_status()

        vms = [
            VM(id=d["id"], name=d["name"], ssh_dest=d["ssh_dest"], status=d["status"])
            for d in resp.json()["vms"]
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
