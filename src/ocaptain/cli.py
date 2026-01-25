"""Typer CLI for ocaptain."""

import subprocess  # nosec B404
from datetime import datetime

import typer
from rich.console import Console
from rich.table import Table

from . import logs as logs_mod
from . import tasks as tasks_mod
from . import voyage as voyage_mod
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
) -> None:
    """Launch a new voyage."""

    with console.status(f"Launching voyage with {ships} ships..."):
        voyage = voyage_mod.sail(prompt, repo, ships)

    console.print(f"\n[green]✓[/green] Voyage [bold]{voyage.id}[/bold] launched")
    console.print(f"  Repo: {voyage.repo}")
    console.print(f"  Branch: {voyage.branch}")
    console.print(f"  Ships: {voyage.ship_count}")
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
def abandon(
    voyage_id: str = typer.Argument(..., help="Voyage ID"),
) -> None:
    """Terminate all ships (keep storage)."""

    count = voyage_mod.abandon(voyage_id)
    console.print(f"[green]✓[/green] Terminated {count} ships. Storage preserved.")


@app.command(name="reset-task")
def reset_task(
    voyage_id: str = typer.Argument(..., help="Voyage ID"),
    task_id: str | None = typer.Argument(None, help="Task ID (or --all-stale)"),
    all_stale: bool = typer.Option(False, "--all-stale", help="Reset all stale tasks"),
) -> None:
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
) -> None:
    """Add ships to an incomplete voyage."""

    voyage, storage = voyage_mod.load_voyage(voyage_id)
    voyage_status = tasks_mod.derive_status(voyage, storage)

    # Determine starting index
    existing_ships = len(voyage_status.ships)
    start_index = existing_ships

    from .ship import add_ships

    new_ships = add_ships(voyage, storage, ships, start_index)

    console.print(f"[green]✓[/green] Added {len(new_ships)} ships to voyage.")


@app.command()
def shell(
    voyage_id: str = typer.Argument(..., help="Voyage ID"),
    ship_id: str = typer.Argument(..., help="Ship ID (e.g., ship-0)"),
) -> None:
    """SSH into a ship for debugging."""

    provider = get_provider()
    ship_name = f"{voyage_id}-{ship_id}"
    vm = next((v for v in provider.list() if v.name == ship_name), None)

    if not vm:
        console.print(f"[red]Ship not found: {ship_name}[/red]")
        raise typer.Exit(1)

    subprocess.run(["ssh", vm.ssh_dest])  # nosec: B603, B607


@app.command()
def sink(
    voyage_id: str | None = typer.Argument(None, help="Voyage ID"),
    all_voyages: bool = typer.Option(False, "--all", help="Destroy ALL ocaptain VMs"),
    force: bool = typer.Option(False, "--force", "-f", help="Skip confirmation"),
) -> None:
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
    if status.value == "complete":
        return "[green]complete[/green]"
    elif status.value == "in_progress":
        if is_stale:
            return "[red]in_progress (stale)[/red]"
        return "[blue]in_progress[/blue]"
    else:
        return "[dim]pending[/dim]"


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


def main() -> None:
    app()


if __name__ == "__main__":
    main()
