"""Voyage creation and management."""

import json
import secrets
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from importlib.resources import files
from pathlib import Path

from .provider import VM, get_provider


def _is_sprite_vm(vm: VM) -> bool:
    """Check if a VM is a sprite (uses sprite:// URI scheme)."""
    return vm.ssh_dest.startswith("sprite://")


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
    telemetry: bool = True,
) -> Voyage:
    """Launch a new voyage using local storage.

    1. Set up local voyage directory
    2. Clone repository locally
    3. Bootstrap ship VMs with Tailscale
    4. Start Mutagen sync sessions
    5. Launch local tmux session
    """
    import subprocess  # nosec: B404
    from concurrent.futures import ThreadPoolExecutor, as_completed

    from .config import CONFIG
    from .local_storage import setup_local_voyage
    from .mutagen import create_sync
    from .ship import bootstrap_ship

    if tokens is None:
        tokens = {}
    ships = ships or CONFIG.default_ships
    voyage = Voyage.create(prompt, repo, ships)

    # Verify tailscale is ready
    if not CONFIG.tailscale.ip:
        raise RuntimeError("Tailscale IP not detected. Is Tailscale running?")
    if not CONFIG.tailscale.oauth_secret:
        raise RuntimeError("OCAPTAIN_TAILSCALE_OAUTH_SECRET not set")

    # 1. Set up local voyage directory
    voyage_dir = setup_local_voyage(voyage.id, voyage.task_list_id)

    # 2. Clone repository locally
    subprocess.run(  # nosec: B603, B607
        ["gh", "repo", "clone", repo, str(voyage_dir / "workspace")],
        check=True,
    )
    subprocess.run(  # nosec: B603, B607
        ["git", "-C", str(voyage_dir / "workspace"), "checkout", "-b", voyage.branch],
        check=True,
    )

    # 3. Write voyage.json locally
    (voyage_dir / "voyage.json").write_text(voyage.to_json())

    # 4. Write prompt.md locally
    prompt_content = render_ship_prompt(voyage)
    (voyage_dir / "prompt.md").write_text(prompt_content)

    # 5. Write spec and verify if provided
    if spec_content:
        (voyage_dir / "artifacts" / "spec.md").write_text(spec_content)
    if verify_content:
        (voyage_dir / "artifacts" / "verify.sh").write_text(verify_content)
        (voyage_dir / "artifacts" / "verify.sh").chmod(0o755)

    # 6. Copy pre-created tasks if provided
    if tasks_dir:
        tasks_path = Path(tasks_dir)
        task_dest = voyage_dir / ".claude" / "tasks" / voyage.task_list_id
        for task_file in sorted(tasks_path.glob("*.json")):
            task_data = json.loads(task_file.read_text())
            if "metadata" in task_data:
                task_data["metadata"]["voyage"] = voyage.id
            (task_dest / task_file.name).write_text(json.dumps(task_data, indent=2))

    # 7. Write stop hook locally
    hook_content = render_stop_hook()
    (voyage_dir / "on-stop.sh").write_text(hook_content)
    (voyage_dir / "on-stop.sh").chmod(0o755)

    # 8. Bootstrap ships with Tailscale
    import logging

    logger = logging.getLogger(__name__)

    successful_ships: list[tuple[VM, str]] = []  # (vm, tailscale_ip)
    failed_ships: list[tuple[int, Exception]] = []

    with ThreadPoolExecutor(max_workers=ships) as executor:
        futures = {
            executor.submit(bootstrap_ship, voyage, i, tokens, telemetry): i for i in range(ships)
        }

        for future in as_completed(futures):
            ship_idx = futures[future]
            try:
                ship_vm, ship_ts_ip = future.result()
                successful_ships.append((ship_vm, ship_ts_ip))
            except Exception as e:
                logger.warning("Ship-%d bootstrap failed: %s", ship_idx, e)
                failed_ships.append((ship_idx, e))

    if len(failed_ships) == ships:
        raise RuntimeError(f"All {ships} ships failed to bootstrap")

    # 9. Start Mutagen sync sessions
    for ship_vm, ship_ts_ip in successful_ships:
        ship_idx = int(ship_vm.name.split("ship")[-1])
        session_name = f"{voyage.id}-ship-{ship_idx}"

        # Sync workspace
        create_sync(
            local_path=voyage_dir / "workspace",
            remote_user="ubuntu" if not _is_sprite_vm(ship_vm) else "sprite",
            remote_host=ship_ts_ip,
            remote_path="/home/ubuntu/voyage/workspace"
            if not _is_sprite_vm(ship_vm)
            else "/home/sprite/voyage/workspace",
            session_name=f"{session_name}-workspace",
        )

        # Sync tasks
        create_sync(
            local_path=voyage_dir / ".claude" / "tasks" / voyage.task_list_id,
            remote_user="ubuntu" if not _is_sprite_vm(ship_vm) else "sprite",
            remote_host=ship_ts_ip,
            remote_path=f"/home/ubuntu/.claude/tasks/{voyage.task_list_id}"
            if not _is_sprite_vm(ship_vm)
            else f"/home/sprite/.claude/tasks/{voyage.task_list_id}",
            session_name=f"{session_name}-tasks",
        )

        # Copy prompt.md and on-stop.sh (one-time, not synced)
        # These are copied via ssh since they don't need continuous sync
        subprocess.run(  # nosec: B603, B607
            [
                "scp",
                "-o",
                "StrictHostKeyChecking=no",
                str(voyage_dir / "prompt.md"),
                f"ubuntu@{ship_ts_ip}:~/voyage/prompt.md",
            ],
            check=True,
            capture_output=True,
        )
        subprocess.run(  # nosec: B603, B607
            [
                "scp",
                "-o",
                "StrictHostKeyChecking=no",
                str(voyage_dir / "on-stop.sh"),
                f"ubuntu@{ship_ts_ip}:~/.ocaptain/hooks/on-stop.sh",
            ],
            check=True,
            capture_output=True,
        )

    # 10. Launch local tmux session
    from .tmux import launch_fleet

    ship_vms = [vm for vm, _ in successful_ships]
    ship_vms.sort(key=lambda vm: int(vm.name.split("ship")[-1]))
    launch_fleet(voyage, ship_vms, tokens)

    return voyage


def load_voyage(voyage_id: str) -> Voyage:
    """Load voyage from local storage."""
    from .local_storage import get_voyage_dir

    voyage_dir = get_voyage_dir(voyage_id)
    voyage_json = voyage_dir / "voyage.json"

    if not voyage_json.exists():
        raise ValueError(f"Voyage not found: {voyage_id}")

    return Voyage.from_json(voyage_json.read_text())


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
