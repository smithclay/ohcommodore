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
from typing import TYPE_CHECKING, Any

from ..provider import VM, Provider, register_provider

if TYPE_CHECKING:
    import boxlite


def _get_boxlite() -> Any:
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
        self._boxes: dict[str, boxlite.SimpleBox] = {}
        self._vms: dict[str, VM] = {}
        self._loop = asyncio.new_event_loop()

    def create(self, name: str, *, wait: bool = True) -> VM:
        """Create a new BoxLite VM."""
        raise NotImplementedError("BoxLite create not yet implemented")

    def destroy(self, vm_id: str) -> None:
        """Destroy a BoxLite VM."""
        raise NotImplementedError("BoxLite destroy not yet implemented")

    def get(self, vm_id: str) -> VM | None:
        """Get VM by ID."""
        return self._vms.get(vm_id)

    def list(self, prefix: str | None = None) -> list[VM]:
        """List VMs, optionally filtered by name prefix."""
        vms = list(self._vms.values())
        if prefix:
            vms = [v for v in vms if v.name.startswith(prefix)]
        return vms

    def wait_ready(self, vm: VM, timeout: int = 300) -> bool:
        """Wait for VM to be SSH-accessible via Tailscale."""
        raise NotImplementedError("BoxLite wait_ready not yet implemented")
