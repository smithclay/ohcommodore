"""Voyage creation and management."""

import json
import secrets
import shlex
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from importlib.resources import files
from io import BytesIO
from pathlib import Path

from fabric import Connection

from .config import CONFIG
from .provider import VM, Provider, get_provider


def _is_sprite_vm(vm: VM) -> bool:
    """Check if a VM is a sprite (uses sprite:// URI scheme)."""
    return vm.ssh_dest.startswith("sprite://")


@contextmanager
def _get_connection(vm: VM, provider: Provider):  # type: ignore[no-untyped-def]
    """Get appropriate connection for a VM (Fabric or Sprite)."""
    if _is_sprite_vm(vm):
        from .providers.sprites import SpritesProvider

        if isinstance(provider, SpritesProvider):
            with provider.get_connection(vm) as c:
                yield c
        else:
            raise ValueError(f"Sprite VM requires SpritesProvider, got {type(provider)}")
    else:
        with Connection(vm.ssh_dest) as c:
            yield c


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
    telemetry: bool = True,
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
        telemetry: Enable OTLP telemetry collection (default True)
    """
    if tokens is None:
        tokens = {}
    ships = ships or CONFIG.default_ships
    voyage = Voyage.create(prompt, repo, ships)
    provider = get_provider()

    # 1. Provision storage VM
    storage = provider.create(voyage.storage_name)

    # For sprites, we need the chisel URL for ship-to-storage tunnels
    storage_chisel_url: str | None = None

    with _get_connection(storage, provider) as c:
        # Get home directory for SFTP operations (c.put doesn't expand ~)
        home = c.run("echo $HOME", hide=True).stdout.strip()
        voyage_dir = f"{home}/voyage"

        # 2. Create directory structure
        c.run("mkdir -p ~/voyage/{workspace,artifacts,logs}")
        c.run(f"mkdir -p ~/.claude/tasks/{voyage.task_list_id}")

        # 2b. Set locale environment variables for proper UTF-8 handling
        c.run("echo 'export LANG=C.UTF-8' >> ~/.bashrc")
        c.run("echo 'export LC_CTYPE=C.UTF-8' >> ~/.bashrc")

        # 2c. Set up chisel server for sprites (ships connect via tunnel)
        if _is_sprite_vm(storage):
            from .providers.sprites import SpritesProvider

            # Install openssh-server and chisel
            c.run(
                "sudo apt-get update -qq && sudo apt-get install -y -qq openssh-server",
                hide=True,
            )
            # Start sshd directly (systemd not available in sprites)
            c.run("sudo /usr/sbin/sshd", hide=True, warn=True)

            # Install chisel
            arch_cmd = "$(uname -m | sed 's/x86_64/amd64/;s/aarch64/arm64/')"
            c.run(
                f"curl -sL https://github.com/jpillora/chisel/releases/download/v1.10.1/"
                f"chisel_1.10.1_linux_{arch_cmd}.gz | gunzip > /tmp/chisel && "
                "sudo mv /tmp/chisel /usr/local/bin/chisel && "
                "sudo chmod +x /usr/local/bin/chisel",
                hide=True,
            )

            # Start chisel server (reverse mode, port 8080)
            c.run(
                "nohup chisel server --port 8080 --reverse "
                f"> {voyage_dir}/logs/chisel-server.log 2>&1 &",
                hide=True,
            )

            # Get public URL for ships to connect
            if isinstance(provider, SpritesProvider):
                storage_chisel_url = provider.get_public_url(storage)
                # Write chisel URL for ships to read
                c.put(
                    BytesIO(storage_chisel_url.encode()),
                    f"{voyage_dir}/chisel_url",
                )

        # 3. Authenticate GitHub CLI if token provided (for private repos)
        if gh_token := tokens.get("GH_TOKEN"):
            c.run(f"echo {shlex.quote(gh_token)} | gh auth login --with-token", hide=True)
            c.run("gh auth setup-git", hide=True)

        # 4. Clone repository (use gh CLI - handles auth properly after gh auth setup-git)
        c.run(f"gh repo clone {repo} ~/voyage/workspace")
        c.run(f"cd ~/voyage/workspace && git checkout -b {voyage.branch}")

        # 4b. Install and launch otlp2parquet for telemetry collection
        if telemetry:
            # Install otlp2parquet from GitHub releases
            arch_cmd = "$(uname -m | sed 's/x86_64/amd64/;s/aarch64/arm64/')"
            c.run(
                "curl -sL https://github.com/smithclay/otlp2parquet/releases/latest/download/"
                f"otlp2parquet-cli-linux-{arch_cmd}.tar.gz "
                "| tar -xzf - --strip-components=1 && sudo mv otlp2parquet /usr/local/bin/",
                hide=True,
            )

            # Create telemetry directory
            c.run("mkdir -p ~/voyage/telemetry")

            # Write config
            otlp_config = f"""[server]
listen_addr = "127.0.0.1:4318"
log_level = "info"
log_format = "text"

[storage]
backend = "fs"

[storage.fs]
path = "{home}/voyage/telemetry"

[batch]
max_rows = 10000
max_bytes = 67108864
max_age_secs = 30
enabled = true
"""
            c.put(BytesIO(otlp_config.encode()), f"{voyage_dir}/otlp2parquet.toml")

            # Start in background
            c.run(
                f"nohup otlp2parquet --config {voyage_dir}/otlp2parquet.toml "
                f"> {voyage_dir}/logs/otlp2parquet.log 2>&1 &",
                hide=True,
            )

        # 5. Write voyage.json
        c.put(BytesIO(voyage.to_json().encode()), f"{voyage_dir}/voyage.json")

        # 6. Write ship prompt template
        prompt_content = render_ship_prompt(voyage)
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

    # 11. tmux is pre-installed on most Linux systems, no install needed

    # 12. Bootstrap ships in parallel
    import logging
    from concurrent.futures import ThreadPoolExecutor, as_completed

    from .ship import bootstrap_ship

    logger = logging.getLogger(__name__)

    successful_ships: list[VM] = []
    failed_ships: list[tuple[int, Exception]] = []
    with ThreadPoolExecutor(max_workers=ships) as executor:
        futures = {
            executor.submit(
                bootstrap_ship, voyage, storage, i, tokens, telemetry, storage_chisel_url
            ): i
            for i in range(ships)
        }

        for future in as_completed(futures):
            ship_idx = futures[future]
            try:
                ship_vm = future.result()
                successful_ships.append(ship_vm)
            except Exception as e:
                logger.warning("Ship-%d bootstrap failed: %s", ship_idx, e)
                failed_ships.append((ship_idx, e))

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

    # 13. Launch fleet via zellij on storage
    # Sort ships by index for consistent pane ordering
    successful_ships.sort(key=lambda vm: int(vm.name.split("ship")[-1]))

    from .tmux import launch_fleet

    launch_fleet(voyage, storage, successful_ships, tokens)

    return voyage


def sail_new(
    prompt: str,
    repo: str,
    ships: int | None = None,
    tokens: dict[str, str] | None = None,
    spec_content: str | None = None,
    verify_content: str | None = None,
    tasks_dir: "Path | None" = None,
    telemetry: bool = True,
) -> Voyage:
    """Launch a new voyage using local storage (new implementation).

    1. Set up local voyage directory
    2. Clone repository locally
    3. Bootstrap ship VMs with Tailscale
    4. Start Mutagen sync sessions
    5. Launch local tmux session

    Note: Will be renamed to sail() in Task 10.
    """
    import subprocess  # nosec: B404
    from concurrent.futures import ThreadPoolExecutor, as_completed

    from .config import CONFIG
    from .local_storage import setup_local_voyage
    from .mutagen import create_sync
    from .ship import bootstrap_ship_new

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
            executor.submit(bootstrap_ship_new, voyage, i, tokens, telemetry): i
            for i in range(ships)
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
    from .tmux import launch_fleet_new  # type: ignore[attr-defined]

    ship_vms = [vm for vm, _ in successful_ships]
    ship_vms.sort(key=lambda vm: int(vm.name.split("ship")[-1]))
    launch_fleet_new(voyage, ship_vms, tokens)

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

    with _get_connection(storage, provider) as c:
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
