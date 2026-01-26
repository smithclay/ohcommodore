"""Ship VM provisioning and bootstrap."""

import json
import shlex
from contextlib import contextmanager
from importlib.resources import files
from io import BytesIO

from fabric import Connection

from .provider import VM, Provider, get_provider
from .voyage import Voyage


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


def _mount_storage_exedev(  # type: ignore[no-untyped-def]
    c, storage: VM, voyage: Voyage, telemetry: bool
) -> None:
    """Mount storage on an exe.dev ship using direct SSH/SSHFS."""
    # Install sshfs, expect (for auto-accepting bypass permissions dialog), and autossh
    c.run(
        "sudo apt-get update -qq && sudo apt-get install -y -qq sshfs expect autossh",
        hide=True,
    )

    # Mount via direct sshfs
    sshfs_opts = "reconnect,ServerAliveInterval=15,ServerAliveCountMax=3,StrictHostKeyChecking=no"
    c.run(f"sshfs {storage.ssh_dest}:voyage ~/voyage -o {sshfs_opts}")
    c.run(
        f"sshfs {storage.ssh_dest}:.claude/tasks/{voyage.task_list_id} "
        f"~/.claude/tasks/{voyage.task_list_id} -o {sshfs_opts}"
    )

    # Establish autossh tunnel for OTLP telemetry
    if telemetry:
        c.run(
            f"autossh -f -N -M 0 "
            f"-o StrictHostKeyChecking=no "
            f"-o ServerAliveInterval=15 "
            f"-o ServerAliveCountMax=3 "
            f"-L 4318:localhost:4318 {storage.ssh_dest}"
        )


def _mount_storage_sprite(  # type: ignore[no-untyped-def]
    c, home: str, voyage: Voyage, storage_chisel_url: str | None, telemetry: bool
) -> None:
    """Mount storage on a sprite ship using chisel tunnel."""
    if not storage_chisel_url:
        raise ValueError("storage_chisel_url is required for sprite ships")

    # Install sshfs, expect, and chisel
    c.run(
        "sudo apt-get update -qq && sudo apt-get install -y -qq sshfs expect",
        hide=True,
    )

    # Install chisel
    arch_cmd = "$(uname -m | sed 's/x86_64/amd64/;s/aarch64/arm64/')"
    c.run(
        f"curl -sL https://github.com/jpillora/chisel/releases/download/v1.10.1/"
        f"chisel_1.10.1_linux_{arch_cmd}.gz | gunzip > /tmp/chisel && "
        "sudo mv /tmp/chisel /usr/local/bin/chisel && "
        "sudo chmod +x /usr/local/bin/chisel",
        hide=True,
    )

    # Create .ocaptain directory for logs
    c.run(f"mkdir -p {home}/.ocaptain")

    # Build chisel tunnel ports: local:remote - ship connects to storage
    # 2222:127.0.0.1:22 means: ship's localhost:2222 -> storage's localhost:22
    tunnel_spec = "2222:127.0.0.1:22"
    if telemetry:
        tunnel_spec += " 4318:127.0.0.1:4318"

    # Start chisel client to connect to storage
    # Storage runs chisel server on port 8080, exposed via public URL
    c.run(
        f"nohup chisel client {storage_chisel_url}:8080 {tunnel_spec} "
        f"> {home}/.ocaptain/chisel-client.log 2>&1 &",
        hide=True,
    )

    # Wait for tunnel to establish
    c.run("sleep 3")

    # Check if chisel is running
    result = c.run("pgrep -f 'chisel client' || echo 'NOT RUNNING'", warn=True)
    if "NOT RUNNING" in result.stdout:
        # Get error from log
        log_result = c.run(f"cat {home}/.ocaptain/chisel-client.log", warn=True)
        raise RuntimeError(f"Chisel client failed to start: {log_result.stdout}")

    # Mount via sshfs through the chisel tunnel (localhost:2222)
    # User is 'sprite' on sprites.dev VMs
    sshfs_opts = "reconnect,ServerAliveInterval=15,ServerAliveCountMax=3,StrictHostKeyChecking=no"
    c.run(f"sshfs -p 2222 sprite@127.0.0.1:voyage ~/voyage -o {sshfs_opts}")
    c.run(
        f"sshfs -p 2222 sprite@127.0.0.1:.claude/tasks/{voyage.task_list_id} "
        f"~/.claude/tasks/{voyage.task_list_id} -o {sshfs_opts}"
    )


def _bootstrap_tailscale(c: Connection, ship_name: str, oauth_secret: str, ship_tag: str) -> str:
    """Install Tailscale and join tailnet with ACL isolation. Returns ship's Tailscale IP.

    Ships are:
    - Ephemeral: Auto-removed when destroyed
    - Preauthorized: No manual approval needed
    - Tagged: Isolated by ACL policy (can only reach laptop's OTLP port)
    """
    # Install Tailscale
    c.run(
        "curl -fsSL https://tailscale.com/install.sh | sh",
        hide=True,
    )

    # Join tailnet with OAuth secret + URL parameters for ephemeral/preauthorized
    # The OAuth secret acts as an auth key when used with ?ephemeral=true&preauthorized=true
    auth_key = f"{oauth_secret}?ephemeral=true&preauthorized=true"
    c.run(
        f"sudo tailscale up "
        f"--authkey={shlex.quote(auth_key)} "
        f"--hostname={ship_name} "
        f"--advertise-tags={ship_tag}",
        hide=True,
    )

    # Get assigned IP
    result = c.run("tailscale ip -4", hide=True)
    return str(result.stdout.strip())


def bootstrap_ship(
    voyage: Voyage,
    storage: VM,
    index: int,
    tokens: dict[str, str] | None = None,
    telemetry: bool = True,
    storage_chisel_url: str | None = None,
) -> VM:
    """
    Bootstrap a single ship VM.

    1. Provision VM
    2. Mount shared storage via SSHFS (or chisel tunnel for sprites)
    3. Write ship identity
    4. Install stop hook
    5. Configure Claude Code (with secure token injection)
    6. Authenticate GitHub CLI (if GH_TOKEN provided)

    Note: Claude is launched separately via tmux from the storage VM.

    Args:
        voyage: The voyage configuration
        storage: The storage VM to mount
        index: Ship index number
        tokens: Dict with CLAUDE_CODE_OAUTH_TOKEN and optionally GH_TOKEN
        telemetry: Enable OTLP telemetry tunnel to storage VM (default True)
        storage_chisel_url: For sprites, the chisel server URL on storage VM
    """
    if tokens is None:
        tokens = {}
    provider = get_provider()
    ship_id = f"ship-{index}"
    ship_name = voyage.ship_name(index)

    # 1. Provision
    ship = provider.create(ship_name)

    with _get_connection(ship, provider) as c:
        # Get home directory for SFTP operations (c.put doesn't expand ~)
        home = c.run("echo $HOME", hide=True).stdout.strip()

        # 2. Install dependencies and mount storage
        c.run(f"mkdir -p ~/voyage ~/.claude/tasks/{voyage.task_list_id}")

        if _is_sprite_vm(ship):
            # Sprites: use chisel tunnel for ship-to-storage communication
            _mount_storage_sprite(c, home, voyage, storage_chisel_url, telemetry)
        else:
            # exe.dev: use direct SSH/SSHFS
            _mount_storage_exedev(c, storage, voyage, telemetry)

        # 3. Write ship identity
        c.run("mkdir -p ~/.ocaptain/hooks")
        c.put(BytesIO(ship_id.encode()), f"{home}/.ocaptain/ship_id")
        c.put(BytesIO(voyage.id.encode()), f"{home}/.ocaptain/voyage_id")
        c.put(BytesIO(storage.ssh_dest.encode()), f"{home}/.ocaptain/storage_ssh")

        # 4. Install stop hook
        c.run("cp ~/voyage/on-stop.sh ~/.ocaptain/hooks/on-stop.sh")

        # 4b. Install expect script for auto-accepting bypass permissions dialog
        expect_script = files("ocaptain.templates").joinpath("run_claude.exp").read_text()
        c.put(BytesIO(expect_script.encode()), f"{home}/.ocaptain/run-claude.exp")
        c.run("chmod +x ~/.ocaptain/run-claude.exp")

        # 5. Configure Claude Code
        c.run("mkdir -p ~/.claude")
        # Skip onboarding
        c.run("echo '{\"hasCompletedOnboarding\":true}' > ~/.claude.json")

        # 5b. Set locale environment variables for proper UTF-8 handling
        c.run("echo 'export LANG=C.UTF-8' >> ~/.bashrc")
        c.run("echo 'export LC_CTYPE=C.UTF-8' >> ~/.bashrc")

        env_vars = {"CLAUDE_CODE_TASK_LIST_ID": voyage.task_list_id}

        if telemetry:
            env_vars.update(
                {
                    "CLAUDE_CODE_ENABLE_TELEMETRY": "1",
                    "OTEL_METRICS_EXPORTER": "otlp",
                    "OTEL_LOGS_EXPORTER": "otlp",
                    "OTEL_EXPORTER_OTLP_PROTOCOL": "http/protobuf",
                    "OTEL_EXPORTER_OTLP_ENDPOINT": "http://localhost:4318",
                    "OTEL_RESOURCE_ATTRIBUTES": f"voyage.id={voyage.id},ship.id={ship_id}",
                }
            )

        settings = {
            "env": env_vars,
            "hooks": {
                "Stop": [
                    {
                        "matcher": "",  # Empty string matches all
                        "hooks": [
                            {
                                "type": "command",
                                "command": f"{home}/.ocaptain/hooks/on-stop.sh",
                            }
                        ],
                    }
                ]
            },
        }
        c.put(BytesIO(json.dumps(settings, indent=2).encode()), f"{home}/.claude/settings.json")

        # 6. Authenticate GitHub CLI (if GH_TOKEN provided, for git push)
        if gh_token := tokens.get("GH_TOKEN"):
            c.run(f"echo {shlex.quote(gh_token)} | gh auth login --with-token", hide=True)
            c.run("gh auth setup-git", hide=True)

        # Ship is now ready - Claude will be launched via zellij from storage

    return ship


def bootstrap_ship_new(
    voyage: Voyage,
    index: int,
    tokens: dict[str, str] | None = None,
    telemetry: bool = True,
) -> tuple[VM, str]:
    """Bootstrap a ship with Tailscale (new implementation).

    Ships are tagged with tag:ocaptain-ship for ACL isolation.
    Returns (ship_vm, ship_tailscale_ip).

    Note: Will be renamed to bootstrap_ship in Task 10.
    """
    from .config import CONFIG

    if tokens is None:
        tokens = {}

    provider = get_provider()
    ship_id = f"ship-{index}"
    ship_name = voyage.ship_name(index)

    # Verify tailscale config
    if not CONFIG.tailscale.oauth_secret:
        raise ValueError("Tailscale OAuth secret required. Set OCAPTAIN_TAILSCALE_OAUTH_SECRET")
    if not CONFIG.tailscale.ip:
        raise ValueError("Tailscale IP not detected. Is Tailscale running?")

    # 1. Create ship VM
    ship = provider.create(ship_name)

    with _get_connection(ship, provider) as c:
        home = c.run("echo $HOME", hide=True).stdout.strip()

        # 2. Bootstrap Tailscale with ACL tag isolation
        ship_ts_ip = _bootstrap_tailscale(
            c, ship_name, CONFIG.tailscale.oauth_secret, CONFIG.tailscale.ship_tag
        )

        # 3. Create directories for Mutagen sync target
        c.run("mkdir -p ~/voyage/workspace ~/voyage/artifacts ~/voyage/logs")
        c.run(f"mkdir -p ~/.claude/tasks/{voyage.task_list_id}")

        # 4. Write ship identity
        c.run("mkdir -p ~/.ocaptain/hooks")
        c.put(BytesIO(ship_id.encode()), f"{home}/.ocaptain/ship_id")
        c.put(BytesIO(voyage.id.encode()), f"{home}/.ocaptain/voyage_id")

        # 5. Install expect script
        expect_script = files("ocaptain.templates").joinpath("run_claude.exp").read_text()
        c.put(BytesIO(expect_script.encode()), f"{home}/.ocaptain/run-claude.exp")
        c.run("chmod +x ~/.ocaptain/run-claude.exp")

        # 6. Configure Claude
        c.run("mkdir -p ~/.claude")
        c.run("echo '{\"hasCompletedOnboarding\":true}' > ~/.claude.json")
        c.run("echo 'export LANG=C.UTF-8' >> ~/.bashrc")
        c.run("echo 'export LC_CTYPE=C.UTF-8' >> ~/.bashrc")

        env_vars = {"CLAUDE_CODE_TASK_LIST_ID": voyage.task_list_id}
        if telemetry:
            # Point OTLP directly to laptop via Tailscale
            env_vars.update(
                {
                    "CLAUDE_CODE_ENABLE_TELEMETRY": "1",
                    "OTEL_METRICS_EXPORTER": "otlp",
                    "OTEL_LOGS_EXPORTER": "otlp",
                    "OTEL_EXPORTER_OTLP_PROTOCOL": "http/protobuf",
                    "OTEL_EXPORTER_OTLP_ENDPOINT": f"http://{CONFIG.tailscale.ip}:{CONFIG.local.otlp_port}",
                    "OTEL_RESOURCE_ATTRIBUTES": f"voyage.id={voyage.id},ship.id={ship_id}",
                }
            )

        settings = {
            "env": env_vars,
            "hooks": {
                "Stop": [
                    {
                        "matcher": "",
                        "hooks": [
                            {"type": "command", "command": f"{home}/.ocaptain/hooks/on-stop.sh"}
                        ],
                    }
                ]
            },
        }
        c.put(BytesIO(json.dumps(settings, indent=2).encode()), f"{home}/.claude/settings.json")

        # 7. GitHub auth
        if gh_token := tokens.get("GH_TOKEN"):
            c.run(f"echo {shlex.quote(gh_token)} | gh auth login --with-token", hide=True)
            c.run("gh auth setup-git", hide=True)

    return ship, ship_ts_ip
