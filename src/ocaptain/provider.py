"""VM provider protocol and registry."""

from abc import abstractmethod
from collections.abc import Callable
from contextlib import contextmanager
from dataclasses import dataclass
from enum import Enum
from typing import TYPE_CHECKING, Protocol, runtime_checkable

if TYPE_CHECKING:
    from collections.abc import Generator
    from typing import Any


class VMStatus(str, Enum):
    """VM lifecycle status."""

    CREATING = "creating"
    RUNNING = "running"
    STOPPED = "stopped"
    STARTING = "starting"
    STOPPING = "stopping"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class VM:
    """A provisioned virtual machine."""

    id: str
    name: str
    ssh_dest: str  # user@host or user@ip
    status: VMStatus


@runtime_checkable
class Provider(Protocol):
    """Abstract interface for VM providers."""

    @abstractmethod
    def create(self, name: str, *, wait: bool = True) -> VM:
        """Create a new VM. Blocks until ready if wait=True."""
        ...

    @abstractmethod
    def destroy(self, vm_id: str) -> None:
        """Destroy a VM."""
        ...

    @abstractmethod
    def get(self, vm_id: str) -> VM | None:
        """Get VM by ID."""
        ...

    @abstractmethod
    def list(self, prefix: str | None = None) -> list[VM]:
        """List VMs, optionally filtered by name prefix."""
        ...

    @abstractmethod
    def wait_ready(self, vm: VM, timeout: int = 300) -> bool:
        """Wait for VM to be SSH-accessible."""
        ...


# Provider registry
_PROVIDERS: dict[str, type[Provider]] = {}


def register_provider(name: str) -> Callable[[type[Provider]], type[Provider]]:
    """Decorator to register a provider implementation."""

    def decorator(cls: type[Provider]) -> type[Provider]:
        _PROVIDERS[name] = cls
        return cls

    return decorator


def get_provider(name: str | None = None) -> Provider:
    """Get provider instance by name."""
    # Import providers to trigger registration
    from . import providers  # noqa: F401
    from .config import CONFIG

    name = name or CONFIG.provider
    if name not in _PROVIDERS:
        raise ValueError(f"Unknown provider: {name}. Available: {list(_PROVIDERS.keys())}")

    return _PROVIDERS[name]()


def is_sprite_vm(vm: VM) -> bool:
    """Check if a VM is a sprite (uses sprite:// URI scheme)."""
    return vm.ssh_dest.startswith("sprite://")


@contextmanager
def get_connection(vm: VM, provider: Provider) -> "Generator[Any, None, None]":
    """Get appropriate connection for a VM (Fabric or Sprite).

    Yields a connection object with run() and put() methods.
    """
    if is_sprite_vm(vm):
        from .providers.sprites import SpritesProvider

        if isinstance(provider, SpritesProvider):
            with provider.get_connection(vm) as c:
                yield c
        else:
            raise ValueError(f"Sprite VM requires SpritesProvider, got {type(provider)}")
    else:
        from fabric import Connection

        with Connection(vm.ssh_dest) as c:
            yield c
