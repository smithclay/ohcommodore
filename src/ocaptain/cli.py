"""Typer CLI for ocaptain."""

import json
import re
import subprocess  # nosec B404
from datetime import datetime
from pathlib import Path

import typer
from rich.console import Console
from rich.table import Table

from . import logs as logs_mod
from . import tasks as tasks_mod
from . import voyage as voyage_mod
from .config import CONFIG
from .provider import get_provider
from .secrets import load_tokens, validate_repo_access

app = typer.Typer(
    name="ocaptain",
    help="Minimal control plane for multi-VM Claude Code orchestration",
    no_args_is_help=True,
)
console = Console()


@app.command()
def sail(
    plan: str = typer.Argument(..., help="Path to voyage plan directory"),
    ships: int = typer.Option(None, "--ships", "-n", help="Override recommended ship count"),
    no_telemetry: bool = typer.Option(False, "--no-telemetry", help="Disable OTLP telemetry"),
) -> None:
    """Launch a new voyage from a plan directory."""
    plan_dir = Path(plan)
    if not plan_dir.is_dir():
        console.print(f"[red]Error:[/red] Plan directory not found: {plan}")
        raise typer.Exit(1)

    # Validate plan directory structure
    validation_errors = _validate_plan_dir(plan_dir)
    if validation_errors:
        console.print("[red]Error:[/red] Invalid plan directory:")
        for error in validation_errors:
            console.print(f"  - {error}")
        raise typer.Exit(1)

    # Load voyage.json
    voyage_json = json.loads((plan_dir / "voyage.json").read_text())
    tasks_dir = plan_dir / "tasks"
    task_count = len(list(tasks_dir.glob("*.json")))

    # Get values from voyage.json
    repo = voyage_json["repo"]
    if not ships:
        ships = voyage_json.get("recommended_ships", 3)

    # Load spec.md and verify.sh content
    spec_content = (plan_dir / "spec.md").read_text()
    verify_content = (plan_dir / "verify.sh").read_text()

    # Extract objective from spec
    prompt = _extract_objective_from_spec(spec_content)
    if not prompt:
        console.print("[red]Error:[/red] Could not extract objective from spec.md")
        raise typer.Exit(1)

    # Determine effective telemetry setting
    telemetry = not no_telemetry and CONFIG.telemetry_enabled

    console.print(f"[dim]Plan loaded: {plan_dir.name}[/dim]")
    console.print(f"[dim]  Repo: {repo}[/dim]")
    console.print(f"[dim]  Tasks: {task_count} (pre-created)[/dim]")
    console.print(f"[dim]  Ships: {ships}[/dim]")
    console.print(f"[dim]  Telemetry: {'enabled' if telemetry else 'disabled'}[/dim]")

    # Load and validate tokens before provisioning
    try:
        tokens = load_tokens()
    except ValueError as e:
        console.print(f"[red]Error:[/red] {e}")
        raise typer.Exit(1) from None

    # Validate repository access before provisioning VMs
    with console.status("Validating repository access..."):
        try:
            validate_repo_access(repo, tokens.get("GH_TOKEN"))
        except ValueError as e:
            console.print(f"[red]Error:[/red] {e}")
            raise typer.Exit(1) from None

    with console.status(f"Launching voyage with {ships} ships..."):
        voyage = voyage_mod.sail(
            prompt,
            repo,
            ships,
            tokens,
            spec_content=spec_content,
            verify_content=verify_content,
            tasks_dir=tasks_dir,
            telemetry=telemetry,
        )

    console.print(f"\n[green]✓[/green] Voyage [bold]{voyage.id}[/bold] launched")
    console.print(f"  Repo: {voyage.repo}")
    console.print(f"  Branch: {voyage.branch}")
    console.print(f"  Ships: {voyage.ship_count}")
    console.print(f"  Plan: {Path(plan).name}")
    console.print("\nShips are now autonomous. Check status with:")
    console.print(f"  [dim]ocaptain status {voyage.id}[/dim]")


@app.command()
def status(
    voyage_id: str | None = typer.Argument(None, help="Voyage ID (optional if only one active)"),
) -> None:
    """Show voyage status (derived from task list)."""
    from .local_storage import get_voyage_dir

    # If no voyage_id, try to find the only active one
    if not voyage_id:
        workspace_dir = Path(CONFIG.local.workspace_dir).expanduser()
        if not workspace_dir.exists():
            console.print("[yellow]No active voyages found.[/yellow]")
            raise typer.Exit(1)

        voyage_dirs = [
            d for d in workspace_dir.iterdir() if d.is_dir() and d.name.startswith("voyage-")
        ]

        if len(voyage_dirs) == 0:
            console.print("[yellow]No active voyages found.[/yellow]")
            raise typer.Exit(1)
        elif len(voyage_dirs) > 1:
            console.print("[yellow]Multiple voyages found. Please specify voyage_id:[/yellow]")
            for d in voyage_dirs:
                console.print(f"  {d.name}")
            raise typer.Exit(1)
        else:
            voyage_id = voyage_dirs[0].name

    voyage = voyage_mod.load_voyage(voyage_id)
    voyage_dir = get_voyage_dir(voyage_id)
    voyage_status = tasks_mod.derive_status_local(voyage, voyage_dir)

    # Header
    console.print(f"\n[bold]Voyage:[/bold] {voyage.id}")
    console.print(
        f"[bold]Prompt:[/bold] {voyage.prompt[:80]}{'...' if len(voyage.prompt) > 80 else ''}"
    )
    console.print(f"[bold]Status:[/bold] {_state_style(voyage_status.state)}")

    # Ships table
    if voyage_status.ships:
        console.print("\n[bold]Ships:[/bold]")
        table = Table(show_header=True, header_style="bold")
        table.add_column("Ship")
        table.add_column("State")
        table.add_column("Current Task")
        table.add_column("Completed")

        for ship in voyage_status.ships:
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
    console.print(f"  Complete:    {voyage_status.tasks_complete}")
    console.print(
        f"  In Progress: {voyage_status.tasks_in_progress}"
        + (f" ({voyage_status.tasks_stale} stale)" if voyage_status.tasks_stale else "")
    )
    console.print(f"  Pending:     {voyage_status.tasks_pending}")
    console.print("  ─────────────")
    console.print(f"  Total:       {voyage_status.tasks_total}")


@app.command()
def logs(
    voyage_id: str = typer.Argument(..., help="Voyage ID"),
    ship: str | None = typer.Option(None, "--ship", "-s", help="Filter to specific ship"),
    follow: bool = typer.Option(False, "--follow", "-f", help="Follow log output"),
    grep: str | None = typer.Option(None, "--grep", "-g", help="Filter log lines"),
    tail: int | None = typer.Option(None, "--tail", "-n", help="Show last N lines"),
) -> None:
    """View aggregated logs."""
    from .local_storage import get_voyage_dir

    voyage = voyage_mod.load_voyage(voyage_id)
    voyage_dir = get_voyage_dir(voyage_id)
    logs_mod.view_logs_local(voyage_dir, voyage, ship_id=ship, follow=follow, grep=grep, tail=tail)


@app.command()
def tasks(
    voyage_id: str = typer.Argument(..., help="Voyage ID"),
    status_filter: str | None = typer.Option(None, "--status", "-s", help="Filter by status"),
) -> None:
    """Show task list."""
    from .local_storage import get_voyage_dir

    voyage = voyage_mod.load_voyage(voyage_id)
    voyage_dir = get_voyage_dir(voyage_id)
    all_tasks = tasks_mod.list_tasks_local(voyage_dir, voyage)

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
def shell(
    voyage_id: str = typer.Argument(..., help="Voyage ID"),
    ship_id: str = typer.Argument("ship-0", help="Ship ID (e.g., ship-0)"),
    raw: bool = typer.Option(False, "--raw", "-r", help="SSH directly to ship instead of tmux"),
) -> None:
    """Attach to a ship's tmux session to observe Claude."""
    # Verify voyage exists
    voyage_mod.load_voyage(voyage_id)

    provider = get_provider()
    idx = _parse_ship_index(ship_id)
    ship_name = f"{voyage_id}-ship{idx}"
    vm = next((v for v in provider.list() if v.name == ship_name), None)

    if not vm:
        console.print(f"[red]Ship not found: {ship_name}[/red]")
        raise typer.Exit(1)

    if raw:
        # Direct SSH to ship (for debugging)
        subprocess.run(["ssh", vm.ssh_dest])  # nosec: B603, B607
    else:
        # Attach to ship's tmux session where Claude is running
        subprocess.run(  # nosec: B603, B607
            ["ssh", "-tt", vm.ssh_dest, "tmux", "attach", "-t", "claude"]
        )


@app.command()
def clone(
    voyage_id: str | None = typer.Argument(None, help="Voyage ID (optional if only one active)"),
    dest: str | None = typer.Option(None, "--dest", "-d", help="Destination directory"),
) -> None:
    """Clone the workspace from local voyage storage."""
    import shutil

    from .local_storage import get_voyage_dir

    # If no voyage_id, try to find the only active one
    if not voyage_id:
        workspace_dir = Path(CONFIG.local.workspace_dir).expanduser()
        if not workspace_dir.exists():
            console.print("[yellow]No voyages adrift to salvage from.[/yellow]")
            raise typer.Exit(1)

        voyage_dirs = [
            d for d in workspace_dir.iterdir() if d.is_dir() and d.name.startswith("voyage-")
        ]

        if len(voyage_dirs) == 0:
            console.print("[yellow]No voyages adrift to salvage from.[/yellow]")
            raise typer.Exit(1)
        elif len(voyage_dirs) > 1:
            console.print(
                "[yellow]Multiple voyages found. Specify which vessel to plunder:[/yellow]"
            )
            for d in voyage_dirs:
                console.print(f"  {d.name}")
            raise typer.Exit(1)
        else:
            voyage_id = voyage_dirs[0].name

    voyage = voyage_mod.load_voyage(voyage_id)
    voyage_dir = get_voyage_dir(voyage_id)

    # Determine destination directory
    repo_name = voyage.repo.split("/")[-1]
    dest_dir = dest or f"{repo_name}-{voyage_id}"

    if Path(dest_dir).exists():
        console.print(f"[red]Destination already exists:[/red] {dest_dir}")
        raise typer.Exit(1)

    source_workspace = voyage_dir / "workspace"
    if not source_workspace.exists():
        console.print(f"[red]Workspace not found:[/red] {source_workspace}")
        raise typer.Exit(1)

    console.print(f"[dim]Hauling cargo from {source_workspace}...[/dim]")

    # Copy workspace directory
    shutil.copytree(source_workspace, dest_dir)

    console.print(f"[green]✓[/green] Cargo secured at [bold]{dest_dir}[/bold]")
    console.print(f"  Branch: {voyage.branch}")


@app.command()
def sink(
    voyage_id: str | None = typer.Argument(None, help="Voyage ID"),
    all_voyages: bool = typer.Option(False, "--all", help="Destroy ALL ocaptain VMs"),
    force: bool = typer.Option(False, "--force", "-f", help="Skip confirmation"),
) -> None:
    """Destroy voyage VMs and clean up local session."""
    from . import mutagen as mutagen_mod

    if all_voyages:
        if not force:
            confirm = typer.confirm("Destroy ALL ocaptain VMs?")
            if not confirm:
                raise typer.Abort()

        count = voyage_mod.sink_all()
        console.print(f"[green]✓[/green] Destroyed {count} VMs.")
    elif voyage_id:
        # Clean up Mutagen sessions
        mutagen_mod.terminate_voyage_syncs(voyage_id)

        if not force:
            confirm = typer.confirm(f"Destroy all VMs for {voyage_id}?")
            if not confirm:
                raise typer.Abort()

        count = voyage_mod.sink(voyage_id)
        console.print(f"[green]✓[/green] Destroyed {count} VMs.")
    else:
        console.print("[red]Specify voyage_id or --all[/red]")
        raise typer.Exit(1)


def _find_tool(name: str) -> str | None:
    """Find a tool, including macOS app bundles."""
    import shutil

    # Try standard PATH first
    path = shutil.which(name)
    if path:
        return path

    # Check macOS app bundle locations
    if name == "tailscale":
        macos_paths = [
            "/Applications/Tailscale.app/Contents/MacOS/Tailscale",
            "/usr/local/bin/tailscale",
        ]
        for p in macos_paths:
            if Path(p).exists():
                return p

    return None


@app.command()
def doctor() -> None:
    """Check system prerequisites and configuration."""
    import os

    # CONFIG import ensures .env files are loaded
    _ = CONFIG

    all_ok = True

    console.print("\n[bold]Checking prerequisites...[/bold]\n")

    # Check tools
    tools = [
        ("tailscale", "brew install tailscale (or https://tailscale.com/download)"),
        ("mutagen", "brew install mutagen-io/mutagen/mutagen"),
        ("otlp2parquet", "cargo install otlp2parquet (or GitHub releases)"),
    ]

    for tool, install_hint in tools:
        if _find_tool(tool):
            console.print(f"  [green]✓[/green] {tool}")
        else:
            console.print(f"  [red]✗[/red] {tool} — {install_hint}")
            all_ok = False

    # Check tailscale connection
    console.print()
    tailscale_path = _find_tool("tailscale")
    try:
        result = subprocess.run(  # nosec B603, B607
            [tailscale_path or "tailscale", "status", "--json"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if result.returncode == 0:
            import json as json_mod

            ts_status = json_mod.loads(result.stdout)
            if ts_status.get("Self", {}).get("Online"):
                ts_ip = ts_status.get("Self", {}).get("TailscaleIPs", ["?"])[0]
                console.print(f"  [green]✓[/green] Tailscale connected ({ts_ip})")
            else:
                console.print("  [red]✗[/red] Tailscale not connected — run: tailscale up")
                all_ok = False
        else:
            console.print("  [red]✗[/red] Tailscale not running — run: tailscale up")
            all_ok = False
    except (subprocess.TimeoutExpired, FileNotFoundError):
        console.print("  [red]✗[/red] Tailscale not available")
        all_ok = False

    # Check mutagen daemon
    try:
        result = subprocess.run(  # nosec B603, B607
            ["mutagen", "daemon", "status"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if "running" in result.stdout.lower() or result.returncode == 0:
            console.print("  [green]✓[/green] Mutagen daemon running")
        else:
            console.print(
                "  [yellow]![/yellow] Mutagen daemon not running — run: mutagen daemon start"
            )
            all_ok = False
    except subprocess.TimeoutExpired:
        console.print("  [yellow]![/yellow] Mutagen daemon status check timed out")
        all_ok = False
    except FileNotFoundError:
        pass  # Already reported as missing tool above

    # Check environment variables (already loaded from .env by CONFIG)
    console.print("\n[bold]Checking environment...[/bold]\n")

    env_vars = [
        ("OCAPTAIN_TAILSCALE_OAUTH_SECRET", True),
        ("CLAUDE_CODE_OAUTH_TOKEN", True),
        ("GH_TOKEN", False),  # Optional
    ]

    for var, required in env_vars:
        value = os.environ.get(var)

        if value:
            masked = value[:12] + "..." if len(value) > 15 else "***"
            console.print(f"  [green]✓[/green] {var} ({masked})")
        elif required:
            console.print(f"  [red]✗[/red] {var} — required")
            all_ok = False
        else:
            console.print(f"  [yellow]![/yellow] {var} — optional, not set")

    # Summary
    console.print()
    if all_ok:
        console.print("[green]All systems ready. You may set sail![/green]")
    else:
        console.print("[red]Some issues found. Please address them before sailing.[/red]")
        raise typer.Exit(1)


@app.command()
def telemetry_start() -> None:
    """Start the local telemetry collector."""
    from importlib.resources import as_file, files

    script = files("ocaptain.scripts").joinpath("start-telemetry.sh")
    with as_file(script) as script_path:
        subprocess.run(["bash", str(script_path)], check=True)  # nosec: B603, B607


@app.command()
def telemetry_stop() -> None:
    """Stop the local telemetry collector."""
    from importlib.resources import as_file, files

    script = files("ocaptain.scripts").joinpath("stop-telemetry.sh")
    with as_file(script) as script_path:
        subprocess.run(["bash", str(script_path)], check=True)  # nosec: B603, B607


# Helper functions


def _state_style(state: tasks_mod.VoyageState | tasks_mod.ShipState) -> str:
    """Apply Rich styling to state."""
    styles = {
        "running": "[blue]running[/blue]",
        "complete": "[green]complete[/green]",
        "stalled": "[red]stalled[/red]",
        "planning": "[yellow]planning[/yellow]",
        "working": "[blue]working[/blue]",
        "idle": "[dim]idle[/dim]",
        "stale": "[red]stale[/red]",
        "unknown": "[dim]unknown[/dim]",
    }
    return styles.get(str(state.value), str(state.value))


def _task_status_style(status: tasks_mod.TaskStatus, is_stale: bool) -> str:
    """Apply Rich styling to task status."""
    if status == tasks_mod.TaskStatus.COMPLETED:
        return "[green]completed[/green]"
    elif status == tasks_mod.TaskStatus.IN_PROGRESS:
        if is_stale:
            return "[red]in_progress (stale)[/red]"
        return "[blue]in_progress[/blue]"
    else:
        return "[dim]pending[/dim]"


def _parse_ship_index(ship_id: str) -> int:
    """Parse ship index from "ship-<n>" or "ship<n>"."""
    if ship_id.startswith("ship-"):
        suffix = ship_id[5:]
    elif ship_id.startswith("ship"):
        suffix = ship_id[4:]
    else:
        suffix = ""

    if not suffix.isdigit():
        console.print(f"[red]Invalid ship id: {ship_id}[/red]")
        raise typer.Exit(1)

    return int(suffix)


def _format_age(dt: datetime) -> str:
    """Format datetime as human-readable age."""
    from datetime import UTC, datetime

    delta = datetime.now(UTC) - dt
    minutes = int(delta.total_seconds() / 60)

    if minutes < 1:
        return "<1m"
    elif minutes < 60:
        return f"{minutes}m"
    else:
        hours = minutes // 60
        return f"{hours}h{minutes % 60}m"


def _validate_plan_dir(plan_dir: Path) -> list[str]:
    """Validate plan directory has required artifacts. Returns list of errors."""
    errors = []

    required_files = ["spec.md", "verify.sh", "voyage.json"]
    for fname in required_files:
        if not (plan_dir / fname).exists():
            errors.append(f"Missing required file: {fname}")

    tasks_dir = plan_dir / "tasks"
    if not tasks_dir.is_dir():
        errors.append("Missing tasks/ directory")
    elif not list(tasks_dir.glob("*.json")):
        errors.append("tasks/ directory has no JSON files")

    # Validate voyage.json structure
    voyage_json_path = plan_dir / "voyage.json"
    if voyage_json_path.exists():
        try:
            voyage_json = json.loads(voyage_json_path.read_text())
            if "repo" not in voyage_json:
                errors.append("voyage.json missing 'repo' field")
            if "recommended_ships" not in voyage_json:
                errors.append("voyage.json missing 'recommended_ships' field")
        except json.JSONDecodeError as e:
            errors.append(f"voyage.json is invalid JSON: {e}")

    return errors


def _extract_objective_from_spec(content: str) -> str | None:
    """Extract objective from spec.md file."""
    # Try "## Objective" first, then "# ... Specification" title
    match = re.search(r"## Objective\s*\n(.*?)(?=\n## |\Z)", content, re.DOTALL)
    if match:
        objective = match.group(1).strip()
        first_para = objective.split("\n\n")[0]
        return first_para[:500] if len(first_para) > 500 else first_para

    # Try first H1 as fallback
    match = re.search(r"^# (.+?)$", content, re.MULTILINE)
    if match:
        return match.group(1).replace(" Specification", "").strip()

    return None


def main() -> None:
    app()


if __name__ == "__main__":
    main()
