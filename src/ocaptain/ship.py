"""Ship VM provisioning and bootstrap."""

import json
import shlex
from importlib.resources import files
from io import BytesIO

from fabric import Connection

from .provider import VM, get_connection, get_provider
from .voyage import Voyage


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

    # Ensure tailscaled is running
    c.run("sudo systemctl enable --now tailscaled", hide=True)

    # Start system sshd on port 2222 for Tailscale access
    # (exe.dev runs custom sshd on 22 that uses their proxy auth)
    c.run(
        "sudo bash -c 'echo Port 2222 > /etc/ssh/sshd_config.d/ocaptain.conf' && "
        "sudo systemctl unmask ssh.socket ssh.service 2>/dev/null; "
        "sudo systemctl enable --now ssh",
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
    index: int,
    tokens: dict[str, str] | None = None,
    telemetry: bool = True,
) -> tuple[VM, str]:
    """Bootstrap a ship with Tailscale.

    Ships are tagged with tag:ocaptain-ship for ACL isolation.
    Returns (ship_vm, ship_tailscale_ip).
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

    with get_connection(ship, provider) as c:
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

        # 8. Install tmux for autonomous Claude sessions
        c.run("sudo apt-get update -qq && sudo apt-get install -y -qq tmux", hide=True)

    return ship, ship_ts_ip
