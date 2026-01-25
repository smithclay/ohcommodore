"""Voyage creation and management."""

import json
import secrets
import shlex
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from importlib.resources import files
from io import BytesIO
from pathlib import Path

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
        # exe.dev doesn't allow hyphen before trailing numbers
        return f"{self.id}-ship{index}"


def sail(
    prompt: str,
    repo: str,
    ships: int | None = None,
    tokens: dict[str, str] | None = None,
    spec_content: str | None = None,
    verify_content: str | None = None,
    tasks_dir: "Path | None" = None,
) -> Voyage:
    """
    Launch a new voyage.

    1. Create storage VM
    2. Initialize voyage directory structure
    3. Clone repository
    4. Copy pre-created tasks (if provided)
    5. Bootstrap ship VMs in parallel

    Args:
        prompt: The objective for the voyage
        repo: GitHub repository in "owner/repo" format
        ships: Number of ship VMs to provision
        tokens: Dict with CLAUDE_CODE_OAUTH_TOKEN (required) and optionally GH_TOKEN
        spec_content: Optional spec.md content (design document)
        verify_content: Optional verify.sh content (exit criteria script)
        tasks_dir: Optional path to tasks/ directory with pre-created task JSON files
    """
    if tokens is None:
        tokens = {}
    ships = ships or CONFIG.default_ships
    voyage = Voyage.create(prompt, repo, ships)
    provider = get_provider()

    # 1. Provision storage VM
    storage = provider.create(voyage.storage_name)

    with Connection(storage.ssh_dest) as c:
        # Get home directory for SFTP operations (c.put doesn't expand ~)
        home = c.run("echo $HOME", hide=True).stdout.strip()
        voyage_dir = f"{home}/voyage"

        # 2. Create directory structure
        c.run("mkdir -p ~/voyage/{workspace,artifacts,logs}")
        c.run(f"mkdir -p ~/.claude/tasks/{voyage.task_list_id}")

        # 3. Authenticate GitHub CLI if token provided (for private repos)
        if gh_token := tokens.get("GH_TOKEN"):
            c.run(f"echo {shlex.quote(gh_token)} | gh auth login --with-token", hide=True)
            c.run("gh auth setup-git", hide=True)

        # 4. Clone repository
        c.run(f"git clone https://github.com/{repo}.git ~/voyage/workspace")
        c.run(f"cd ~/voyage/workspace && git checkout -b {voyage.branch}")

        # 5. Write voyage.json
        c.put(BytesIO(voyage.to_json().encode()), f"{voyage_dir}/voyage.json")

        # 6. Write ship prompt template
        prompt_content = render_ship_prompt(voyage, has_tasks=tasks_dir is not None)
        c.put(BytesIO(prompt_content.encode()), f"{voyage_dir}/prompt.md")

        # 7. Write spec.md if provided
        if spec_content:
            c.put(BytesIO(spec_content.encode()), f"{voyage_dir}/artifacts/spec.md")

        # 8. Write verify.sh if provided
        if verify_content:
            c.put(BytesIO(verify_content.encode()), f"{voyage_dir}/artifacts/verify.sh")
            c.run("chmod +x ~/voyage/artifacts/verify.sh")

        # 9. Copy pre-created tasks if provided
        if tasks_dir:
            tasks_path = Path(tasks_dir)
            task_dest = f"{home}/.claude/tasks/{voyage.task_list_id}"
            for task_file in sorted(tasks_path.glob("*.json")):
                # Update task metadata with actual voyage ID
                task_data = json.loads(task_file.read_text())
                if "metadata" in task_data:
                    task_data["metadata"]["voyage"] = voyage.id
                task_json = json.dumps(task_data, indent=2)
                c.put(BytesIO(task_json.encode()), f"{task_dest}/{task_file.name}")

        # 10. Write stop hook
        hook_content = render_stop_hook()
        c.put(BytesIO(hook_content.encode()), f"{voyage_dir}/on-stop.sh")
        c.run("chmod +x ~/voyage/on-stop.sh")

    # 11. Bootstrap ships (sequential for now - parallel has SSH issues)
    import logging
    import time

    from .ship import bootstrap_ship

    logger = logging.getLogger(__name__)

    # Small delay to let SSH multiplexing settle
    time.sleep(1)

    failed_ships: list[tuple[int, Exception]] = []
    for i in range(ships):
        try:
            bootstrap_ship(voyage, storage, i, tokens)
        except Exception as e:
            logger.warning("Ship-%d bootstrap failed: %s", i, e)
            failed_ships.append((i, e))

    if len(failed_ships) == ships:
        raise RuntimeError(
            f"All {ships} ships failed to bootstrap: "
            + ", ".join(f"ship-{i}: {e}" for i, e in failed_ships)
        )

    if failed_ships:
        logger.warning(
            "%d/%d ships failed to bootstrap: %s",
            len(failed_ships),
            ships,
            [i for i, _ in failed_ships],
        )

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
        result = c.run("cat ~/voyage/voyage.json", hide=True)
        voyage = Voyage.from_json(result.stdout)

    return voyage, storage


def abandon(voyage_id: str) -> int:
    """Terminate all ships (but keep storage)."""
    provider = get_provider()

    vms = provider.list(prefix=voyage_id)
    ships = [vm for vm in vms if "-ship" in vm.name and vm.name != f"{voyage_id}-storage"]

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


def render_ship_prompt(voyage: Voyage, has_tasks: bool = False) -> str:
    """Render the ship prompt template."""
    if has_tasks:
        # Use simplified prompt when tasks are pre-created
        template = files("ocaptain.templates").joinpath("ship_prompt_with_tasks.md").read_text()
    else:
        template = files("ocaptain.templates").joinpath("ship_prompt.md").read_text()

    return template.format(
        voyage_id=voyage.id,
        repo=voyage.repo,
        prompt=voyage.prompt,
        ship_count=voyage.ship_count,
        task_list_id=voyage.task_list_id,
    )


def render_verify_script(exit_criteria: list[str]) -> str:
    """Render the verify.sh script from exit criteria commands."""
    lines = [
        "#!/bin/bash",
        "# Exit criteria verification script",
        "# Generated by ocaptain from voyage plan",
        "",
        "set -e  # Exit on first failure",
        "cd ~/voyage/workspace",
        "",
    ]

    for cmd in exit_criteria:
        lines.append(f'echo "Running: {cmd}"')
        lines.append(cmd)
        lines.append('echo "  PASSED"')
        lines.append("")

    lines.append('echo ""')
    lines.append('echo "All exit criteria passed!"')

    return "\n".join(lines)


def render_stop_hook() -> str:
    """Render the stop hook script."""
    return files("ocaptain.templates").joinpath("on_stop.sh").read_text()
