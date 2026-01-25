"""ocaptain - Minimal control plane for multi-VM Claude Code orchestration."""

__version__ = "2.2.0"

from .provider import VM, Provider, get_provider
from .tasks import derive_status, list_tasks
from .voyage import Voyage, abandon, load_voyage, sail, sink

__all__ = [
    "Voyage",
    "sail",
    "load_voyage",
    "abandon",
    "sink",
    "derive_status",
    "list_tasks",
    "Provider",
    "VM",
    "get_provider",
]
