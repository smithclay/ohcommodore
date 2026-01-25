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

    console.print(f"[dim]Plan loaded: {plan_dir.name}[/dim]")
    console.print(f"[dim]  Repo: {repo}[/dim]")
    console.print(f"[dim]  Tasks: {task_count} (pre-created)[/dim]")
    console.print(f"[dim]  Ships: {ships}[/dim]")

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
    voyage_status = tasks_mod.derive_status(voyage, storage)

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

    voyage, storage = voyage_mod.load_voyage(voyage_id)
    logs_mod.view_logs(storage, voyage, ship_id=ship, follow=follow, grep=grep, tail=tail)


@app.command()
def tasks(
    voyage_id: str = typer.Argument(..., help="Voyage ID"),
    status_filter: str | None = typer.Option(None, "--status", "-s", help="Filter by status"),
) -> None:
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
def shell(
    voyage_id: str = typer.Argument(..., help="Voyage ID"),
    ship_id: str | None = typer.Argument(None, help="Ship ID to focus (e.g., ship-0)"),
    raw: bool = typer.Option(False, "--raw", "-r", help="SSH directly to ship instead of tmux"),
) -> None:
    """Attach to voyage's tmux session to observe ships."""
    from . import tmux as tmux_mod

    voyage, storage = voyage_mod.load_voyage(voyage_id)

    if raw and ship_id:
        # Direct SSH to ship (for debugging)
        provider = get_provider()
        idx = _parse_ship_index(ship_id)
        ship_name = f"{voyage_id}-ship{idx}"
        vm = next((v for v in provider.list() if v.name == ship_name), None)

        if not vm:
            console.print(f"[red]Ship not found: {ship_name}[/red]")
            raise typer.Exit(1)

        subprocess.run(["ssh", vm.ssh_dest])  # nosec: B603, B607
    else:
        # Attach to tmux session
        focus_pane = _parse_ship_index(ship_id) if ship_id else None
        cmd = tmux_mod.attach_session(storage, voyage_id, focus_pane)
        subprocess.run(cmd)  # nosec: B603, B607


@app.command()
def sink(
    voyage_id: str | None = typer.Argument(None, help="Voyage ID"),
    all_voyages: bool = typer.Option(False, "--all", help="Destroy ALL ocaptain VMs"),
    include_storage: bool = typer.Option(
        False, "--include-storage", "-s", help="Also destroy storage VM"
    ),
    force: bool = typer.Option(False, "--force", "-f", help="Skip confirmation"),
) -> None:
    """Destroy voyage VMs (keeps storage by default)."""
    from . import tmux as tmux_mod

    if all_voyages:
        if not force:
            confirm = typer.confirm("Destroy ALL ocaptain VMs?")
            if not confirm:
                raise typer.Abort()

        count = voyage_mod.sink_all()
        console.print(f"[green]✓[/green] Destroyed {count} VMs.")
    elif voyage_id:
        # Kill tmux session first (if storage is accessible)
        try:
            voyage, storage = voyage_mod.load_voyage(voyage_id)
            tmux_mod.kill_session(storage, voyage_id)
        except Exception:  # nosec: B110 - Storage may already be gone
            pass

        if include_storage:
            if not force:
                confirm = typer.confirm(f"Destroy all VMs for {voyage_id} (including storage)?")
                if not confirm:
                    raise typer.Abort()

            count = voyage_mod.sink(voyage_id)
            console.print(f"[green]✓[/green] Destroyed {count} VMs.")
        else:
            # Default: destroy ships only, keep storage (no confirmation needed)
            count = voyage_mod.abandon(voyage_id)
            console.print(f"[green]✓[/green] Terminated {count} ships. Storage preserved.")
    else:
        console.print("[red]Specify voyage_id or --all[/red]")
        raise typer.Exit(1)


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
