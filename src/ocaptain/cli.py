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
    if plan:
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
    from . import zellij as zellij_mod
    from .ship import add_ships

    # Load tokens before provisioning
    try:
        tokens = load_tokens()
    except ValueError as e:
        console.print(f"[red]Error:[/red] {e}")
        raise typer.Exit(1) from None

    voyage, storage = voyage_mod.load_voyage(voyage_id)
    provider = get_provider()

    # Determine starting index from existing VMs, not task assignments.
    vms = provider.list(prefix=voyage_id)
    ship_indices = [
        index for vm in vms if (index := _ship_index_from_name(voyage_id, vm.name)) is not None
    ]
    start_index = max(ship_indices, default=-1) + 1

    new_ships = add_ships(voyage, storage, ships, start_index, tokens)

    # Launch new ships via zellij (adds panes to existing session)
    zellij_mod.add_ships_to_session(voyage, storage, new_ships, start_index, tokens)

    console.print(f"[green]✓[/green] Added {len(new_ships)} ships to voyage.")


@app.command()
def shell(
    voyage_id: str = typer.Argument(..., help="Voyage ID"),
    ship_id: str | None = typer.Argument(None, help="Ship ID to focus (e.g., ship-0)"),
    raw: bool = typer.Option(False, "--raw", "-r", help="SSH directly to ship instead of zellij"),
) -> None:
    """Attach to voyage's zellij session to observe ships."""
    from . import zellij as zellij_mod

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
        # Attach to zellij session
        focus_pane = _parse_ship_index(ship_id) if ship_id else None
        cmd = zellij_mod.attach_session(storage, voyage_id, focus_pane)
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
    from . import zellij as zellij_mod

    if all_voyages:
        if not force:
            confirm = typer.confirm("Destroy ALL ocaptain VMs?")
            if not confirm:
                raise typer.Abort()

        count = voyage_mod.sink_all()
        console.print(f"[green]✓[/green] Destroyed {count} VMs.")
    elif voyage_id:
        # Kill zellij session first (if storage is accessible)
        try:
            voyage, storage = voyage_mod.load_voyage(voyage_id)
            zellij_mod.kill_session(storage, voyage_id)
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


def _ship_index_from_name(voyage_id: str, name: str) -> int | None:
    """Extract ship index from a VM name."""
    prefix = f"{voyage_id}-ship"
    if not name.startswith(prefix):
        return None

    suffix = name[len(prefix) :]
    if not suffix.isdigit():
        return None

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


def _validate_plan(content: str) -> list[str]:
    """Validate plan file has required sections. Returns list of errors."""
    errors = []
    required_sections = ["## Objective", "## Tasks", "## Exit Criteria", "## Requirements"]

    for section in required_sections:
        if section not in content:
            errors.append(f"Missing required section: {section}")

    # Check that Tasks section has at least one numbered task
    if "## Tasks" in content:
        tasks_match = re.search(r"## Tasks\s*\n(.*?)(?=\n## |\Z)", content, re.DOTALL)
        if tasks_match:
            tasks_text = tasks_match.group(1)
            if not re.search(r"^\d+\.", tasks_text, re.MULTILINE):
                errors.append("Tasks section has no numbered tasks")

    # Check that Exit Criteria has a code block
    if "## Exit Criteria" in content:
        criteria_match = re.search(r"## Exit Criteria\s*\n(.*?)(?=\n## |\Z)", content, re.DOTALL)
        if criteria_match:
            criteria_text = criteria_match.group(1)
            if "```" not in criteria_text:
                errors.append("Exit Criteria section missing code block with commands")

    return errors


def _extract_objective(content: str) -> str | None:
    """Extract objective from plan file."""
    match = re.search(r"## Objective\s*\n(.*?)(?=\n## |\Z)", content, re.DOTALL)
    if match:
        objective = match.group(1).strip()
        # Take first paragraph or first 500 chars
        first_para = objective.split("\n\n")[0]
        return first_para[:500] if len(first_para) > 500 else first_para
    return None


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


def _extract_exit_criteria(content: str) -> list[str]:
    """Extract exit criteria commands from plan file."""
    criteria_match = re.search(r"## Exit Criteria\s*\n(.*?)(?=\n## |\Z)", content, re.DOTALL)
    if not criteria_match:
        return []

    criteria_text = criteria_match.group(1)

    # Find code block
    code_match = re.search(r"```(?:bash|sh)?\s*\n(.*?)```", criteria_text, re.DOTALL)
    if not code_match:
        return []

    code_block = code_match.group(1)

    # Extract non-comment, non-empty lines
    commands = []
    for line in code_block.strip().split("\n"):
        line = line.strip()
        if line and not line.startswith("#"):
            commands.append(line)

    return commands


def _count_tasks(content: str) -> int:
    """Count numbered tasks in plan file."""
    tasks_match = re.search(r"## Tasks\s*\n(.*?)(?=\n## |\Z)", content, re.DOTALL)
    if not tasks_match:
        return 0

    tasks_text = tasks_match.group(1)
    return len(re.findall(r"^\d+\.", tasks_text, re.MULTILINE))


def main() -> None:
    app()


if __name__ == "__main__":
    main()
