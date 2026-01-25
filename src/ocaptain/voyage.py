"""Voyage creation and management."""

import json
import secrets
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from importlib.resources import files
from io import StringIO

from fabric import Connection

from .config import CONFIG
from .provider import VM, get_provider


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

    from .ship import bootstrap_ship

    with ThreadPoolExecutor(max_workers=ships) as executor:
        futures = {executor.submit(bootstrap_ship, voyage, storage, i): i for i in range(ships)}

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
    template = files("ocaptain.templates").joinpath("ship_prompt.md").read_text()

    return template.format(
        voyage_id=voyage.id,
        repo=voyage.repo,
        prompt=voyage.prompt,
        ship_count=voyage.ship_count,
        task_list_id=voyage.task_list_id,
    )


def render_stop_hook() -> str:
    """Render the stop hook script."""
    return files("ocaptain.templates").joinpath("on_stop.sh").read_text()
