# ocaptain v2.2: Technical Design

*Python + Fabric + uv implementation*

---

## Overview

This document translates the ocaptain v2.2 architecture into a concrete Python implementation using:

- **Fabric 3.x** for SSH operations
- **Typer** for CLI
- **uv** for build, dependency management, and distribution
- **Rich** for terminal output

**Design philosophy**: Match the architecture document's clarity in code. Each architectural concept maps to exactly one module.

---

## Project Layout

```
ocaptain/
├── pyproject.toml              # uv/PEP 621 config
├── uv.lock                     # Locked dependencies
├── README.md
├── src/
│   └── ocaptain/
│       ├── __init__.py         # Version, public API
│       ├── __main__.py         # python -m ocaptain
│       ├── cli.py              # Typer CLI (~150 LOC)
│       ├── voyage.py           # Voyage lifecycle (~200 LOC)
│       ├── ship.py             # Ship bootstrap (~150 LOC)
│       ├── tasks.py            # Task reading & status derivation (~100 LOC)
│       ├── logs.py             # Log aggregation (~50 LOC)
│       ├── provider.py         # Provider protocol + registry (~50 LOC)
│       ├── providers/
│       │   ├── __init__.py
│       │   └── exedev.py       # exe.dev implementation (~150 LOC)
│       ├── templates/
│       │   ├── ship_prompt.md  # Ship prompt template
│       │   └── on_stop.sh      # Stop hook script
│       └── config.py           # Configuration loading (~50 LOC)
├── tests/
│   ├── conftest.py
│   ├── test_voyage.py
│   ├── test_tasks.py
│   └── test_provider_mock.py
└── scripts/
    └── install.sh              # curl-pipe installer
```

**Estimated total**: ~900-1100 LOC (excluding templates and tests)

---

## pyproject.toml

```toml
[project]
name = "ocaptain"
version = "2.2.0"
description = "Minimal control plane for multi-VM Claude Code orchestration"
readme = "README.md"
requires-python = ">=3.11"
license = { text = "MIT" }
authors = [{ name = "You", email = "you@example.com" }]
keywords = ["claude", "orchestration", "autonomous-agents"]

dependencies = [
    "fabric>=3.2,<4",
    "typer>=0.12,<1",
    "rich>=13.0,<14",
    "pydantic>=2.0,<3",      # For config validation
]

[project.optional-dependencies]
dev = [
    "pytest>=8.0",
    "pytest-mock>=3.0",
    "ruff>=0.4",
    "mypy>=1.10",
]

[project.scripts]
ocaptain = "ocaptain.cli:app"

[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[tool.hatch.build.targets.wheel]
packages = ["src/ocaptain"]

[tool.ruff]
line-length = 100
target-version = "py311"

[tool.ruff.lint]
select = ["E", "F", "I", "N", "W", "UP", "B", "C4", "SIM"]

[tool.mypy]
python_version = "3.11"
strict = true
```

---

## uv Workflows

### Development Setup

```bash
# Clone and setup
git clone https://github.com/you/ocaptain
cd ocaptain

# Create venv and install (one command)
uv sync

# Run in development
uv run ocaptain --help
uv run ocaptain sail "Add auth" --repo=acme/app

# Or activate venv traditionally
source .venv/bin/activate
ocaptain --help
```

### Testing

```bash
# Run tests
uv run pytest

# With coverage
uv run pytest --cov=ocaptain

# Type checking
uv run mypy src/

# Linting
uv run ruff check src/
uv run ruff format src/
```

### Building

```bash
# Build wheel and sdist
uv build

# Output in dist/
#   ocaptain-2.2.0-py3-none-any.whl
#   ocaptain-2.2.0.tar.gz
```

### Distribution Options

**Option 1: pipx/uv tool install (recommended)**

```bash
# End users install with:
uv tool install ocaptain

# Or with pipx
pipx install ocaptain

# Creates isolated env, adds `ocaptain` to PATH
```

**Option 2: Direct run without install**

```bash
# Run directly from git
uvx --from git+https://github.com/you/ocaptain ocaptain sail "prompt"

# Or from PyPI
uvx ocaptain sail "prompt" --repo=acme/app
```

**Option 3: curl-pipe installer**

```bash
# scripts/install.sh
#!/bin/bash
set -e

if command -v uv &> /dev/null; then
    uv tool install ocaptain
elif command -v pipx &> /dev/null; then
    pipx install ocaptain
else
    echo "Installing uv first..."
    curl -LsSf https://astral.sh/uv/install.sh | sh
    uv tool install ocaptain
fi

echo "ocaptain installed! Run 'ocaptain --help' to get started."
```

Users run:
```bash
curl -LsSf https://raw.githubusercontent.com/you/ocaptain/main/scripts/install.sh | bash
```

### Publishing

```bash
# Publish to PyPI
uv publish

# Or to test PyPI first
uv publish --index-url https://test.pypi.org/simple/
```

---

## Module Specifications

### `config.py` - Configuration

```python
"""Configuration loading and defaults."""

from pathlib import Path
from pydantic import BaseModel
import os
import json

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

    data = {}
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
```

### `provider.py` - Provider Abstraction

```python
"""VM provider protocol and registry."""

from typing import Protocol, runtime_checkable
from dataclasses import dataclass
from abc import abstractmethod


@dataclass(frozen=True)
class VM:
    """A provisioned virtual machine."""
    id: str
    name: str
    ssh_dest: str  # user@host or user@ip
    status: str    # "running", "stopped", etc.


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


def register_provider(name: str):
    """Decorator to register a provider implementation."""
    def decorator(cls: type[Provider]) -> type[Provider]:
        _PROVIDERS[name] = cls
        return cls
    return decorator


def get_provider(name: str | None = None) -> Provider:
    """Get provider instance by name."""
    from .config import CONFIG

    name = name or CONFIG.provider
    if name not in _PROVIDERS:
        raise ValueError(f"Unknown provider: {name}. Available: {list(_PROVIDERS.keys())}")

    return _PROVIDERS[name]()
```

### `providers/exedev.py` - exe.dev Implementation

```python
"""exe.dev VM provider implementation."""

import httpx
import time
from fabric import Connection

from ..provider import Provider, VM, register_provider
from ..config import CONFIG


@register_provider("exedev")
class ExeDevProvider(Provider):
    """exe.dev VM provider."""

    def __init__(self):
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
```

### `voyage.py` - Voyage Lifecycle

```python
"""Voyage creation and management."""

from dataclasses import dataclass, asdict
from datetime import datetime, UTC
from pathlib import Path
import secrets
import json
from io import StringIO

from fabric import Connection

from .provider import get_provider, VM
from .ship import bootstrap_ship
from .config import CONFIG


@dataclass(frozen=True)
class Voyage:
    """An immutable voyage definition."""

    id: str
    prompt: str
    repo: str
    branch: str
    task_list_id: str
    ship_count: int
    created_at: str  # ISO format

    @classmethod
    def create(cls, prompt: str, repo: str, ships: int) -> "Voyage":
        vid = f"voyage-{secrets.token_hex(6)}"
        return cls(
            id=vid,
            prompt=prompt,
            repo=repo,
            branch=vid,
            task_list_id=f"{vid}-tasks",
            ship_count=ships,
            created_at=datetime.now(UTC).isoformat(),
        )

    def to_json(self) -> str:
        return json.dumps(asdict(self), indent=2)

    @classmethod
    def from_json(cls, data: str) -> "Voyage":
        return cls(**json.loads(data))

    @property
    def storage_name(self) -> str:
        return f"{self.id}-storage"

    def ship_name(self, index: int) -> str:
        return f"{self.id}-ship-{index}"


def sail(prompt: str, repo: str, ships: int | None = None) -> Voyage:
    """
    Launch a new voyage.

    1. Create storage VM
    2. Initialize voyage directory structure
    3. Clone repository
    4. Bootstrap ship VMs in parallel
    """
    ships = ships or CONFIG.default_ships
    voyage = Voyage.create(prompt, repo, ships)
    provider = get_provider()

    # 1. Provision storage VM
    storage = provider.create(voyage.storage_name)

    with Connection(storage.ssh_dest) as c:
        # 2. Create directory structure
        c.run("mkdir -p /voyage/{workspace,artifacts,logs}")
        c.run(f"mkdir -p ~/.claude/tasks/{voyage.task_list_id}")

        # 3. Clone repository
        c.run(f"git clone --branch main git@github.com:{repo}.git /voyage/workspace")
        c.run(f"cd /voyage/workspace && git checkout -b {voyage.branch}")

        # 4. Write voyage.json
        c.put(StringIO(voyage.to_json()), "/voyage/voyage.json")

        # 5. Write ship prompt template
        prompt_content = render_ship_prompt(voyage)
        c.put(StringIO(prompt_content), "/voyage/prompt.md")

        # 6. Write stop hook
        hook_content = render_stop_hook()
        c.put(StringIO(hook_content), "/voyage/on-stop.sh")
        c.run("chmod +x /voyage/on-stop.sh")

    # 7. Bootstrap ships (parallel)
    from concurrent.futures import ThreadPoolExecutor, as_completed

    with ThreadPoolExecutor(max_workers=ships) as executor:
        futures = {
            executor.submit(bootstrap_ship, voyage, storage, i): i
            for i in range(ships)
        }

        for future in as_completed(futures):
            ship_index = futures[future]
            try:
                future.result()
            except Exception as e:
                # Log but continue - partial success is acceptable
                print(f"Warning: ship-{ship_index} bootstrap failed: {e}")

    return voyage


def load_voyage(voyage_id: str) -> tuple[Voyage, VM]:
    """Load voyage from storage VM."""
    provider = get_provider()

    # Find storage VM
    storage_name = f"{voyage_id}-storage"
    vms = provider.list(prefix=voyage_id)
    storage = next((vm for vm in vms if vm.name == storage_name), None)

    if not storage:
        raise ValueError(f"Voyage not found: {voyage_id}")

    with Connection(storage.ssh_dest) as c:
        result = c.run("cat /voyage/voyage.json", hide=True)
        voyage = Voyage.from_json(result.stdout)

    return voyage, storage


def abandon(voyage_id: str) -> int:
    """Terminate all ships (but keep storage)."""
    provider = get_provider()

    vms = provider.list(prefix=voyage_id)
    ships = [vm for vm in vms if "-ship-" in vm.name]

    for ship in ships:
        provider.destroy(ship.id)

    return len(ships)


def sink(voyage_id: str) -> int:
    """Destroy all VMs for a voyage."""
    provider = get_provider()

    vms = provider.list(prefix=voyage_id)
    for vm in vms:
        provider.destroy(vm.id)

    return len(vms)


def sink_all() -> int:
    """Destroy all ocaptain VMs."""
    provider = get_provider()

    vms = provider.list(prefix="voyage-")
    for vm in vms:
        provider.destroy(vm.id)

    return len(vms)


def render_ship_prompt(voyage: Voyage) -> str:
    """Render the ship prompt template."""
    template_path = Path(__file__).parent / "templates" / "ship_prompt.md"
    template = template_path.read_text()

    return template.format(
        voyage_id=voyage.id,
        repo=voyage.repo,
        prompt=voyage.prompt,
        ship_count=voyage.ship_count,
        task_list_id=voyage.task_list_id,
    )


def render_stop_hook() -> str:
    """Render the stop hook script."""
    template_path = Path(__file__).parent / "templates" / "on_stop.sh"
    return template_path.read_text()
```

### `ship.py` - Ship Bootstrap

```python
"""Ship VM provisioning and bootstrap."""

from io import StringIO
import json

from fabric import Connection

from .provider import get_provider, VM
from .voyage import Voyage


def bootstrap_ship(voyage: Voyage, storage: VM, index: int) -> VM:
    """
    Bootstrap a single ship VM.

    1. Provision VM
    2. Mount shared storage via SSHFS
    3. Write ship identity
    4. Install stop hook
    5. Configure Claude Code
    6. Start Claude Code
    """
    provider = get_provider()
    ship_id = f"ship-{index}"
    ship_name = voyage.ship_name(index)

    # 1. Provision
    ship = provider.create(ship_name)

    with Connection(ship.ssh_dest) as c:
        # 2. Mount shared storage
        c.run("mkdir -p ~/voyage ~/tasks")
        c.run(
            f"sshfs {storage.ssh_dest}:/voyage ~/voyage "
            "-o reconnect,ServerAliveInterval=15,ServerAliveCountMax=3"
        )
        c.run(
            f"sshfs {storage.ssh_dest}:~/.claude/tasks/{voyage.task_list_id} ~/tasks "
            "-o reconnect,ServerAliveInterval=15,ServerAliveCountMax=3"
        )

        # 3. Write ship identity
        c.run("mkdir -p ~/.ocaptain/hooks")
        c.put(StringIO(ship_id), "~/.ocaptain/ship_id")
        c.put(StringIO(voyage.id), "~/.ocaptain/voyage_id")
        c.put(StringIO(storage.ssh_dest), "~/.ocaptain/storage_ssh")

        # 4. Install stop hook
        c.run("cp ~/voyage/on-stop.sh ~/.ocaptain/hooks/on-stop.sh")

        # 5. Configure Claude Code
        c.run("mkdir -p ~/.claude")

        settings = {
            "env": {
                "CLAUDE_CODE_TASK_LIST_ID": voyage.task_list_id,
            },
            "hooks": {
                "Stop": [{
                    "matcher": {},
                    "hooks": [{
                        "type": "command",
                        "command": "~/.ocaptain/hooks/on-stop.sh"
                    }]
                }]
            }
        }
        c.put(StringIO(json.dumps(settings, indent=2)), "~/.claude/settings.json")

        # 6. Start Claude Code (detached)
        c.run(
            f"nohup claude --prompt-file ~/voyage/prompt.md "
            f"&> ~/voyage/logs/{ship_id}.log &",
            disown=True
        )

    return ship


def add_ships(voyage: Voyage, storage: VM, count: int, start_index: int) -> list[VM]:
    """Add additional ships to a running voyage."""
    from concurrent.futures import ThreadPoolExecutor, as_completed

    ships = []

    with ThreadPoolExecutor(max_workers=count) as executor:
        futures = {
            executor.submit(bootstrap_ship, voyage, storage, start_index + i): i
            for i in range(count)
        }

        for future in as_completed(futures):
            try:
                ships.append(future.result())
            except Exception as e:
                print(f"Warning: ship bootstrap failed: {e}")

    return ships
```

### `tasks.py` - Task Reading & Status Derivation

```python
"""Task list operations and status derivation."""

from dataclasses import dataclass
from datetime import datetime, UTC
from enum import Enum
import json

from fabric import Connection

from .provider import VM
from .voyage import Voyage
from .config import CONFIG


class TaskStatus(str, Enum):
    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    COMPLETE = "complete"


class ShipState(str, Enum):
    WORKING = "working"
    IDLE = "idle"
    STALE = "stale"
    UNKNOWN = "unknown"


class VoyageState(str, Enum):
    PLANNING = "planning"
    RUNNING = "running"
    STALLED = "stalled"
    COMPLETE = "complete"


@dataclass
class Task:
    """A task from the task list."""
    id: str
    title: str
    description: str
    status: TaskStatus
    blocked_by: list[str]
    blocks: list[str]
    created: datetime
    updated: datetime
    assignee: str | None = None
    claimed_at: datetime | None = None
    completed_by: str | None = None
    completed_at: datetime | None = None

    @classmethod
    def from_json(cls, data: dict) -> "Task":
        metadata = data.get("metadata", {})
        return cls(
            id=data["id"],
            title=data["title"],
            description=data.get("description", ""),
            status=TaskStatus(data["status"]),
            blocked_by=data.get("blockedBy", []),
            blocks=data.get("blocks", []),
            created=datetime.fromisoformat(data["created"]),
            updated=datetime.fromisoformat(data["updated"]),
            assignee=metadata.get("assignee"),
            claimed_at=datetime.fromisoformat(metadata["claimed_at"]) if metadata.get("claimed_at") else None,
            completed_by=metadata.get("completed_by"),
            completed_at=datetime.fromisoformat(metadata["completed_at"]) if metadata.get("completed_at") else None,
        )

    def is_stale(self, threshold_minutes: int | None = None) -> bool:
        """Check if an in-progress task is stale."""
        if self.status != TaskStatus.IN_PROGRESS or not self.claimed_at:
            return False

        threshold = threshold_minutes or CONFIG.stale_threshold_minutes
        age = datetime.now(UTC) - self.claimed_at
        return age.total_seconds() > threshold * 60


@dataclass
class ShipStatus:
    """Derived status for a ship."""
    id: str
    state: ShipState
    current_task: str | None
    claimed_at: datetime | None
    completed_count: int


@dataclass
class VoyageStatus:
    """Derived status for a voyage."""
    voyage: Voyage
    state: VoyageState
    ships: list[ShipStatus]
    tasks_complete: int
    tasks_in_progress: int
    tasks_pending: int
    tasks_stale: int
    tasks_total: int


def list_tasks(storage: VM, voyage: Voyage) -> list[Task]:
    """Read all tasks from storage VM."""
    with Connection(storage.ssh_dest) as c:
        # List task files
        result = c.run(
            f"ls ~/.claude/tasks/{voyage.task_list_id}/*.json 2>/dev/null || echo ''",
            hide=True
        )

        if not result.stdout.strip():
            return []

        # Read all task files
        result = c.run(
            f"cat ~/.claude/tasks/{voyage.task_list_id}/*.json",
            hide=True
        )

        # Parse JSON objects (one per file, concatenated)
        tasks = []
        decoder = json.JSONDecoder()
        content = result.stdout.strip()
        pos = 0

        while pos < len(content):
            try:
                obj, end = decoder.raw_decode(content, pos)
                tasks.append(Task.from_json(obj))
                pos = end
                # Skip whitespace between objects
                while pos < len(content) and content[pos].isspace():
                    pos += 1
            except json.JSONDecodeError:
                break

        return tasks


def derive_status(voyage: Voyage, storage: VM) -> VoyageStatus:
    """Derive full voyage status from task list."""
    tasks = list_tasks(storage, voyage)

    if not tasks:
        return VoyageStatus(
            voyage=voyage,
            state=VoyageState.PLANNING,
            ships=[],
            tasks_complete=0,
            tasks_in_progress=0,
            tasks_pending=0,
            tasks_stale=0,
            tasks_total=0,
        )

    # Count task states
    complete = [t for t in tasks if t.status == TaskStatus.COMPLETE]
    in_progress = [t for t in tasks if t.status == TaskStatus.IN_PROGRESS]
    pending = [t for t in tasks if t.status == TaskStatus.PENDING]
    stale = [t for t in in_progress if t.is_stale()]

    # Derive ship states from task metadata
    ships_map: dict[str, ShipStatus] = {}

    for task in tasks:
        if task.assignee and task.assignee not in ships_map:
            ships_map[task.assignee] = ShipStatus(
                id=task.assignee,
                state=ShipState.UNKNOWN,
                current_task=None,
                claimed_at=None,
                completed_count=0,
            )

        if task.assignee:
            ship = ships_map[task.assignee]

            if task.status == TaskStatus.COMPLETE:
                ships_map[task.assignee] = ShipStatus(
                    id=ship.id,
                    state=ship.state,
                    current_task=ship.current_task,
                    claimed_at=ship.claimed_at,
                    completed_count=ship.completed_count + 1,
                )
            elif task.status == TaskStatus.IN_PROGRESS:
                state = ShipState.STALE if task.is_stale() else ShipState.WORKING
                ships_map[task.assignee] = ShipStatus(
                    id=ship.id,
                    state=state,
                    current_task=task.id,
                    claimed_at=task.claimed_at,
                    completed_count=ship.completed_count,
                )

    # Ships with no in-progress task but completed tasks are idle
    for ship_id, ship in ships_map.items():
        if ship.state == ShipState.UNKNOWN and ship.completed_count > 0:
            ships_map[ship_id] = ShipStatus(
                id=ship.id,
                state=ShipState.IDLE,
                current_task=None,
                claimed_at=None,
                completed_count=ship.completed_count,
            )

    ships = sorted(ships_map.values(), key=lambda s: s.id)

    # Derive voyage state
    if len(complete) == len(tasks):
        state = VoyageState.COMPLETE
    elif len(stale) == len(in_progress) and pending:
        state = VoyageState.STALLED
    else:
        state = VoyageState.RUNNING

    return VoyageStatus(
        voyage=voyage,
        state=state,
        ships=ships,
        tasks_complete=len(complete),
        tasks_in_progress=len(in_progress),
        tasks_pending=len(pending),
        tasks_stale=len(stale),
        tasks_total=len(tasks),
    )


def reset_task(storage: VM, voyage: Voyage, task_id: str) -> bool:
    """Reset a stale task to pending."""
    with Connection(storage.ssh_dest) as c:
        task_path = f"~/.claude/tasks/{voyage.task_list_id}/{task_id}.json"

        # Read current task
        result = c.run(f"cat {task_path}", hide=True)
        task_data = json.loads(result.stdout)

        # Update status
        task_data["status"] = "pending"
        task_data["updated"] = datetime.now(UTC).isoformat()
        if "metadata" in task_data:
            task_data["metadata"].pop("assignee", None)
            task_data["metadata"].pop("claimed_at", None)

        # Write back
        c.put(StringIO(json.dumps(task_data, indent=2)), task_path)

        return True


def reset_stale_tasks(storage: VM, voyage: Voyage) -> int:
    """Reset all stale tasks to pending."""
    tasks = list_tasks(storage, voyage)
    stale = [t for t in tasks if t.is_stale()]

    for task in stale:
        reset_task(storage, voyage, task.id)

    return len(stale)
```

### `logs.py` - Log Aggregation

```python
"""Log viewing and aggregation."""

from fabric import Connection

from .provider import VM
from .voyage import Voyage


def view_logs(
    storage: VM,
    voyage: Voyage,
    ship_id: str | None = None,
    follow: bool = False,
    grep: str | None = None,
    tail: int | None = None,
) -> None:
    """View aggregated logs from storage VM."""

    pattern = f"{ship_id}.log" if ship_id else "*.log"
    path = f"/voyage/logs/{pattern}"

    # Build command
    if follow:
        cmd = f"tail -f {path}"
    elif tail:
        cmd = f"tail -n {tail} {path}"
    else:
        cmd = f"cat {path}"

    if grep:
        cmd = f"{cmd} | grep --color=always '{grep}'"

    with Connection(storage.ssh_dest) as c:
        # Use pty=True for follow mode to handle Ctrl+C properly
        c.run(cmd, pty=follow)
```

### `cli.py` - Command Line Interface

```python
"""Typer CLI for ocaptain."""

import typer
from rich.console import Console
from rich.table import Table
from typing import Optional

from . import voyage as voyage_mod
from . import tasks as tasks_mod
from . import logs as logs_mod
from .provider import get_provider

app = typer.Typer(
    name="ocaptain",
    help="Minimal control plane for multi-VM Claude Code orchestration",
    no_args_is_help=True,
)
console = Console()


@app.command()
def sail(
    prompt: str = typer.Argument(..., help="The objective for the voyage"),
    repo: str = typer.Option(..., "--repo", "-r", help="GitHub repo (owner/name)"),
    ships: int = typer.Option(3, "--ships", "-n", help="Number of ships to provision"),
):
    """Launch a new voyage."""

    with console.status(f"Launching voyage with {ships} ships..."):
        voyage = voyage_mod.sail(prompt, repo, ships)

    console.print(f"\n[green]✓[/green] Voyage [bold]{voyage.id}[/bold] launched")
    console.print(f"  Repo: {voyage.repo}")
    console.print(f"  Branch: {voyage.branch}")
    console.print(f"  Ships: {voyage.ship_count}")
    console.print(f"\nShips are now autonomous. Check status with:")
    console.print(f"  [dim]ocaptain status {voyage.id}[/dim]")


@app.command()
def status(
    voyage_id: Optional[str] = typer.Argument(None, help="Voyage ID (optional if only one active)"),
):
    """Show voyage status (derived from task list)."""

    # If no voyage_id, try to find the only active one
    if not voyage_id:
        provider = get_provider()
        vms = provider.list(prefix="voyage-")
        storage_vms = [vm for vm in vms if vm.name.endswith("-storage")]

        if len(storage_vms) == 0:
            console.print("[yellow]No active voyages found.[/yellow]")
            raise typer.Exit(1)
        elif len(storage_vms) > 1:
            console.print("[yellow]Multiple voyages found. Please specify voyage_id:[/yellow]")
            for vm in storage_vms:
                vid = vm.name.replace("-storage", "")
                console.print(f"  {vid}")
            raise typer.Exit(1)
        else:
            voyage_id = storage_vms[0].name.replace("-storage", "")

    voyage, storage = voyage_mod.load_voyage(voyage_id)
    status = tasks_mod.derive_status(voyage, storage)

    # Header
    console.print(f"\n[bold]Voyage:[/bold] {voyage.id}")
    console.print(f"[bold]Prompt:[/bold] {voyage.prompt[:80]}{'...' if len(voyage.prompt) > 80 else ''}")
    console.print(f"[bold]Status:[/bold] {_state_style(status.state)}")

    # Ships table
    if status.ships:
        console.print("\n[bold]Ships:[/bold]")
        table = Table(show_header=True, header_style="bold")
        table.add_column("Ship")
        table.add_column("State")
        table.add_column("Current Task")
        table.add_column("Completed")

        for ship in status.ships:
            state_str = _state_style(ship.state)
            task_str = ship.current_task or "—"
            if ship.claimed_at:
                age = _format_age(ship.claimed_at)
                task_str = f"{task_str} ({age} ago)"

            table.add_row(
                ship.id,
                state_str,
                task_str,
                str(ship.completed_count),
            )

        console.print(table)

    # Tasks summary
    console.print("\n[bold]Tasks:[/bold]")
    console.print(f"  Complete:    {status.tasks_complete}")
    console.print(f"  In Progress: {status.tasks_in_progress}" +
                  (f" ({status.tasks_stale} stale)" if status.tasks_stale else ""))
    console.print(f"  Pending:     {status.tasks_pending}")
    console.print(f"  ─────────────")
    console.print(f"  Total:       {status.tasks_total}")


@app.command()
def logs(
    voyage_id: str = typer.Argument(..., help="Voyage ID"),
    ship: Optional[str] = typer.Option(None, "--ship", "-s", help="Filter to specific ship"),
    follow: bool = typer.Option(False, "--follow", "-f", help="Follow log output"),
    grep: Optional[str] = typer.Option(None, "--grep", "-g", help="Filter log lines"),
    tail: Optional[int] = typer.Option(None, "--tail", "-n", help="Show last N lines"),
):
    """View aggregated logs."""

    voyage, storage = voyage_mod.load_voyage(voyage_id)
    logs_mod.view_logs(storage, voyage, ship_id=ship, follow=follow, grep=grep, tail=tail)


@app.command()
def tasks(
    voyage_id: str = typer.Argument(..., help="Voyage ID"),
    status_filter: Optional[str] = typer.Option(None, "--status", "-s", help="Filter by status"),
):
    """Show task list."""

    voyage, storage = voyage_mod.load_voyage(voyage_id)
    all_tasks = tasks_mod.list_tasks(storage, voyage)

    if status_filter:
        all_tasks = [t for t in all_tasks if t.status.value == status_filter]

    table = Table(show_header=True, header_style="bold")
    table.add_column("ID")
    table.add_column("Title")
    table.add_column("Status")
    table.add_column("Assignee")
    table.add_column("Blocked By")

    for task in sorted(all_tasks, key=lambda t: t.id):
        status_str = _task_status_style(task.status, task.is_stale())
        blocked = ", ".join(task.blocked_by) if task.blocked_by else "—"

        table.add_row(
            task.id,
            task.title[:40] + ("..." if len(task.title) > 40 else ""),
            status_str,
            task.assignee or "—",
            blocked,
        )

    console.print(table)


@app.command()
def abandon(
    voyage_id: str = typer.Argument(..., help="Voyage ID"),
):
    """Terminate all ships (keep storage)."""

    count = voyage_mod.abandon(voyage_id)
    console.print(f"[green]✓[/green] Terminated {count} ships. Storage preserved.")


@app.command(name="reset-task")
def reset_task(
    voyage_id: str = typer.Argument(..., help="Voyage ID"),
    task_id: Optional[str] = typer.Argument(None, help="Task ID (or --all-stale)"),
    all_stale: bool = typer.Option(False, "--all-stale", help="Reset all stale tasks"),
):
    """Reset stale task(s) to pending."""

    voyage, storage = voyage_mod.load_voyage(voyage_id)

    if all_stale:
        count = tasks_mod.reset_stale_tasks(storage, voyage)
        console.print(f"[green]✓[/green] Reset {count} stale tasks to pending.")
    elif task_id:
        tasks_mod.reset_task(storage, voyage, task_id)
        console.print(f"[green]✓[/green] Reset {task_id} to pending.")
    else:
        console.print("[red]Specify task_id or --all-stale[/red]")
        raise typer.Exit(1)


@app.command()
def resume(
    voyage_id: str = typer.Argument(..., help="Voyage ID"),
    ships: int = typer.Option(1, "--ships", "-n", help="Number of ships to add"),
):
    """Add ships to an incomplete voyage."""

    voyage, storage = voyage_mod.load_voyage(voyage_id)
    status = tasks_mod.derive_status(voyage, storage)

    # Determine starting index
    existing_ships = len(status.ships)
    start_index = existing_ships

    from .ship import add_ships
    new_ships = add_ships(voyage, storage, ships, start_index)

    console.print(f"[green]✓[/green] Added {len(new_ships)} ships to voyage.")


@app.command()
def shell(
    voyage_id: str = typer.Argument(..., help="Voyage ID"),
    ship_id: str = typer.Argument(..., help="Ship ID (e.g., ship-0)"),
):
    """SSH into a ship for debugging."""

    import subprocess

    provider = get_provider()
    ship_name = f"{voyage_id}-{ship_id}"
    vm = next((v for v in provider.list() if v.name == ship_name), None)

    if not vm:
        console.print(f"[red]Ship not found: {ship_name}[/red]")
        raise typer.Exit(1)

    subprocess.run(["ssh", vm.ssh_dest])


@app.command()
def sink(
    voyage_id: Optional[str] = typer.Argument(None, help="Voyage ID"),
    all_voyages: bool = typer.Option(False, "--all", help="Destroy ALL ocaptain VMs"),
    force: bool = typer.Option(False, "--force", "-f", help="Skip confirmation"),
):
    """Destroy voyage VMs."""

    if all_voyages:
        if not force:
            confirm = typer.confirm("Destroy ALL ocaptain VMs?")
            if not confirm:
                raise typer.Abort()

        count = voyage_mod.sink_all()
        console.print(f"[green]✓[/green] Destroyed {count} VMs.")
    elif voyage_id:
        if not force:
            confirm = typer.confirm(f"Destroy all VMs for {voyage_id}?")
            if not confirm:
                raise typer.Abort()

        count = voyage_mod.sink(voyage_id)
        console.print(f"[green]✓[/green] Destroyed {count} VMs.")
    else:
        console.print("[red]Specify voyage_id or --all[/red]")
        raise typer.Exit(1)


# Helper functions

def _state_style(state) -> str:
    """Apply Rich styling to state."""
    styles = {
        "running": "[blue]running[/blue]",
        "complete": "[green]complete[/green]",
        "stalled": "[red]stalled[/red]",
        "planning": "[yellow]planning[/yellow]",
        "working": "[blue]working[/blue]",
        "idle": "[dim]idle[/dim]",
        "stale": "[red]stale[/red] ⚠️",
        "unknown": "[dim]unknown[/dim]",
    }
    return styles.get(str(state.value), str(state.value))


def _task_status_style(status, is_stale: bool) -> str:
    """Apply Rich styling to task status."""
    if status.value == "complete":
        return "[green]complete[/green]"
    elif status.value == "in_progress":
        if is_stale:
            return "[red]in_progress (stale)[/red]"
        return "[blue]in_progress[/blue]"
    else:
        return "[dim]pending[/dim]"


def _format_age(dt) -> str:
    """Format datetime as human-readable age."""
    from datetime import datetime, UTC

    delta = datetime.now(UTC) - dt
    minutes = int(delta.total_seconds() / 60)

    if minutes < 1:
        return "<1m"
    elif minutes < 60:
        return f"{minutes}m"
    else:
        hours = minutes // 60
        return f"{hours}h{minutes % 60}m"


def main():
    app()


if __name__ == "__main__":
    main()
```

### `__init__.py`

```python
"""ocaptain - Minimal control plane for multi-VM Claude Code orchestration."""

__version__ = "2.2.0"

from .voyage import Voyage, sail, load_voyage, abandon, sink
from .tasks import derive_status, list_tasks
from .provider import Provider, VM, get_provider

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
```

### `__main__.py`

```python
"""Allow running as `python -m ocaptain`."""

from .cli import main

if __name__ == "__main__":
    main()
```

---

## Templates

### `templates/ship_prompt.md`

```markdown
You are an autonomous coding agent on voyage {voyage_id}.

Ship ID: Read from ~/.ocaptain/ship_id
Repository: {repo}
Objective: {prompt}

## Environment

- Workspace: ~/voyage/workspace (shared with other ships via SSHFS)
- Artifacts: ~/voyage/artifacts (CLAUDE.md, progress.txt)
- Logs: ~/voyage/logs/ (your log is auto-captured)
- Tasks: Native Claude Code tasks (CLAUDE_CODE_TASK_LIST_ID={task_list_id})

## Startup Protocol

1. Read your ship ID: `cat ~/.ocaptain/ship_id`

2. Check TaskList() for existing work

3. If NO tasks exist, you are the first ship:
   a. Analyze the codebase thoroughly
   b. Create ~/voyage/artifacts/CLAUDE.md:
      - Project overview and tech stack
      - Build/test/lint commands
      - Code conventions discovered
   c. Create ~/voyage/artifacts/init.sh:
      - Install dependencies
      - Any setup steps needed
   d. Create 20-50 tasks using TaskCreate():
      - Each completable in <30 minutes
      - Clear titles and descriptions
      - Proper blockedBy dependencies
      - Aim for 3-4 parallelizable waves
   e. Initialize ~/voyage/artifacts/progress.txt with planning summary
   f. Proceed to work loop

4. If tasks exist but all claimed or blocked:
   - Wait 30 seconds, retry (planner may still be creating)

5. If tasks have claimable work:
   - Read ~/voyage/artifacts/CLAUDE.md for conventions
   - Run ~/voyage/artifacts/init.sh if it exists and you haven't
   - Proceed to work loop

## Work Loop

1. TaskList() — see all tasks

2. Find claimable task (priority order):
   - status: "pending"
   - blockedBy: empty OR all blockers have status "complete"
   - Not claimed by another ship

3. Claim it:
   ```
   TaskUpdate(id,
     status="in_progress",
     metadata.assignee="<your-ship-id>",
     metadata.claimed_at="<ISO-timestamp>")
   ```

4. Do the work:
   - Read relevant code
   - Implement the change
   - Run tests — task isn't done until tests pass
   - Commit with message: "[task-XXX] <description>"

5. Complete it:
   ```
   TaskUpdate(id,
     status="complete",
     metadata.completed_by="<your-ship-id>",
     metadata.completed_at="<ISO-timestamp>")
   ```

6. Append summary to ~/voyage/artifacts/progress.txt

7. Repeat from step 1

## Discovered Work

If you find bugs, edge cases, or missing requirements:

1. TaskCreate() with appropriate blockedBy/blocks
2. Note discovery in progress.txt
3. Either claim it yourself or leave for another ship

## Coordination with Other Ships

- {ship_count} ships are working on this voyage
- Task broadcast: your TaskCreate/TaskUpdate calls are visible to others in real-time
- Task dependencies prevent conflicts on sequential work
- For parallel-safe work, first to claim wins
- When unsure about state, call TaskList()

## Exit Conditions

Exit (let Claude Code terminate) when ALL are true:
- No tasks with status "pending"
- No tasks with status "in_progress" (except stale ones >30min old)
- You've completed at least one task

Your stop hook will automatically:
- Commit any uncommitted work
- Append exit note to progress.txt

## Important Conventions

- Always run tests before marking complete
- Commit frequently with descriptive messages
- Update progress.txt after each task
- If stuck >15 minutes, add notes to task and move on
- Respect existing code style in CLAUDE.md
```

### `templates/on_stop.sh`

```bash
#!/bin/bash
# Ship stop hook - commits work and exits cleanly

set -e

SHIP_ID=$(cat ~/.ocaptain/ship_id 2>/dev/null || echo "unknown")
TIMESTAMP=$(date -Iseconds)

# Commit any uncommitted work
cd ~/voyage/workspace 2>/dev/null || exit 0

if [[ -n $(git status --porcelain 2>/dev/null) ]]; then
    git add -A
    git commit -m "[$SHIP_ID] Final commit on exit - $TIMESTAMP" || true
fi

# Append to progress.txt
if [[ -f ~/voyage/artifacts/progress.txt ]]; then
    echo "" >> ~/voyage/artifacts/progress.txt
    echo "## $TIMESTAMP - $SHIP_ID exited" >> ~/voyage/artifacts/progress.txt
    echo "" >> ~/voyage/artifacts/progress.txt
fi

exit 0
```

---

## Testing Strategy

### Unit Tests (no VMs)

```python
# tests/test_tasks.py
import pytest
from ocaptain.tasks import Task, TaskStatus, derive_status

def test_task_from_json():
    data = {
        "id": "task-001",
        "title": "Test task",
        "status": "pending",
        "blockedBy": [],
        "blocks": [],
        "created": "2026-01-24T10:00:00+00:00",
        "updated": "2026-01-24T10:00:00+00:00",
    }

    task = Task.from_json(data)

    assert task.id == "task-001"
    assert task.status == TaskStatus.PENDING
    assert not task.is_stale()


def test_stale_detection():
    from datetime import datetime, timedelta, UTC

    old_time = datetime.now(UTC) - timedelta(minutes=45)

    task = Task(
        id="task-001",
        title="Test",
        description="",
        status=TaskStatus.IN_PROGRESS,
        blocked_by=[],
        blocks=[],
        created=old_time,
        updated=old_time,
        assignee="ship-0",
        claimed_at=old_time,
    )

    assert task.is_stale(threshold_minutes=30)
    assert not task.is_stale(threshold_minutes=60)
```

### Integration Tests (mock provider)

```python
# tests/test_voyage.py
import pytest
from unittest.mock import MagicMock, patch

@pytest.fixture
def mock_provider():
    provider = MagicMock()
    provider.create.return_value = VM(
        id="vm-123",
        name="test-storage",
        ssh_dest="test@localhost",
        status="running",
    )
    return provider


def test_sail_creates_storage(mock_provider):
    with patch("ocaptain.voyage.get_provider", return_value=mock_provider):
        with patch("ocaptain.voyage.Connection"):
            with patch("ocaptain.voyage.bootstrap_ship"):
                voyage = sail("Test prompt", "owner/repo", ships=1)

    assert voyage.id.startswith("voyage-")
    mock_provider.create.assert_called()
```

### End-to-End Tests (real VMs, CI only)

```python
# tests/e2e/test_full_voyage.py
import pytest
import os

@pytest.mark.skipif(
    not os.environ.get("OCAPTAIN_E2E"),
    reason="E2E tests require OCAPTAIN_E2E=1"
)
def test_full_voyage_lifecycle():
    """Spin up real VMs, run a minimal voyage, verify completion."""
    # This runs against a test repo with simple tasks
    ...
```

---

## Development Workflow

```bash
# Initial setup
git clone https://github.com/you/ocaptain
cd ocaptain
uv sync

# Make changes
uv run ruff check src/
uv run ruff format src/
uv run mypy src/
uv run pytest

# Test CLI locally
uv run ocaptain --help
uv run ocaptain sail "Test" --repo=you/test-repo --ships=1

# Build and publish
uv build
uv publish
```

---

## Future Considerations

### v2.2.1: Retry Logic
- Exponential backoff on SSH failures
- Provider API retry with jitter

### v2.2.2: Better Observability
- Structured logging with structlog
- Optional OpenTelemetry spans

### v2.3: Events System
- `events.jsonl` on storage VM
- `ocaptain events` command

### v2.4: Cost Tracking
- Token counts in task metadata
- `ocaptain cost` command

---

## Summary

| Component | LOC (est) | Complexity |
|-----------|-----------|------------|
| cli.py | 200 | Low (Typer handles heavy lifting) |
| voyage.py | 200 | Medium (orchestration logic) |
| ship.py | 100 | Low (bootstrap sequence) |
| tasks.py | 150 | Medium (parsing, derivation) |
| logs.py | 50 | Low |
| provider.py | 50 | Low (protocol definition) |
| providers/exedev.py | 150 | Medium (API integration) |
| config.py | 50 | Low |
| **Total** | **~950** | |

Dependencies: 4 runtime (fabric, typer, rich, pydantic)

The implementation mirrors the architecture: stateless CLI, passive storage, emergent coordination via native tasks.

---

*Document version: 2.2.0*
*Implementation target: Python 3.11+ with uv*
