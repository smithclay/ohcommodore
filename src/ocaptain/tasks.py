"""Task list operations and status derivation."""

import json
import logging
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import Enum
from io import BytesIO

# Forward reference for Voyage to avoid circular import
from typing import TYPE_CHECKING, Any

from fabric import Connection

from .config import CONFIG
from .provider import VM

if TYPE_CHECKING:
    from .voyage import Voyage

logger = logging.getLogger(__name__)


def _parse_datetime(value: str) -> datetime:
    """Parse ISO datetime string ensuring timezone awareness."""
    dt = datetime.fromisoformat(value)
    if dt.tzinfo is None:
        # Assume UTC for naive datetimes
        dt = dt.replace(tzinfo=UTC)
    return dt


class TaskStatus(str, Enum):
    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"


class ShipState(str, Enum):
    WORKING = "working"
    IDLE = "idle"
    STALE = "stale"
    UNKNOWN = "unknown"


class VoyageState(str, Enum):
    PLANNING = "planning"
    RUNNING = "running"
    STALLED = "stalled"
    COMPLETE = "complete"


@dataclass
class Task:
    """A task from the task list."""

    id: str
    title: str
    description: str
    status: TaskStatus
    blocked_by: list[str]
    blocks: list[str]
    created: datetime
    updated: datetime
    assignee: str | None = None
    claimed_at: datetime | None = None
    completed_by: str | None = None
    completed_at: datetime | None = None

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> "Task":
        metadata = data.get("metadata", {})
        # Claude Code uses "subject" but our pre-created tasks use "title"
        title = data.get("title") or data.get("subject", "")
        # Handle missing timestamps (Claude-created tasks may not have them)
        now = datetime.now(UTC)
        created = _parse_datetime(data["created"]) if "created" in data else now
        updated = _parse_datetime(data["updated"]) if "updated" in data else now
        return cls(
            id=data["id"],
            title=title,
            description=data.get("description", ""),
            status=TaskStatus(data["status"]),
            blocked_by=data.get("blockedBy", []),
            blocks=data.get("blocks", []),
            created=created,
            updated=updated,
            assignee=data.get("owner") or metadata.get("assignee") or metadata.get("ship"),
            claimed_at=(
                _parse_datetime(metadata["claimed_at"]) if metadata.get("claimed_at") else None
            ),
            completed_by=metadata.get("completed_by"),
            completed_at=(
                _parse_datetime(metadata["completed_at"]) if metadata.get("completed_at") else None
            ),
        )

    def is_stale(self, threshold_minutes: int | None = None) -> bool:
        """Check if an in-progress task is stale."""
        if self.status != TaskStatus.IN_PROGRESS or not self.claimed_at:
            return False

        threshold = threshold_minutes or CONFIG.stale_threshold_minutes
        age = datetime.now(UTC) - self.claimed_at
        return age.total_seconds() > threshold * 60


@dataclass(frozen=True)
class ShipStatus:
    """Derived status for a ship."""

    id: str
    state: ShipState
    current_task: str | None
    claimed_at: datetime | None
    completed_count: int


@dataclass(frozen=True)
class VoyageStatus:
    """Derived status for a voyage."""

    voyage: "Voyage"
    state: VoyageState
    ships: tuple[ShipStatus, ...]
    tasks_complete: int
    tasks_in_progress: int
    tasks_pending: int
    tasks_stale: int
    tasks_total: int


def list_tasks(storage: VM, voyage: "Voyage") -> list[Task]:
    """Read all tasks from storage VM."""
    with Connection(storage.ssh_dest) as c:
        # List task files
        result = c.run(
            f"ls ~/.claude/tasks/{voyage.task_list_id}/*.json 2>/dev/null || echo ''",
            hide=True,
        )

        if not result.stdout.strip():
            return []

        # Read all task files
        result = c.run(
            f"cat ~/.claude/tasks/{voyage.task_list_id}/*.json",
            hide=True,
        )

        # Parse JSON objects (one per file, concatenated)
        tasks = []
        decoder = json.JSONDecoder()
        content = result.stdout.strip()
        pos = 0

        while pos < len(content):
            try:
                obj, end = decoder.raw_decode(content, pos)
                tasks.append(Task.from_json(obj))
                pos = end
                # Skip whitespace between objects
                while pos < len(content) and content[pos].isspace():
                    pos += 1
            except json.JSONDecodeError as e:
                logger.warning(
                    "Failed to parse task JSON at position %d: %s. Remaining content: %s",
                    pos,
                    e,
                    content[pos : pos + 100],
                )
                break

        return tasks


def _check_progress_file(storage: VM) -> str | None:
    """Check progress.txt for status indicators. Returns last line if exists."""
    try:
        with Connection(storage.ssh_dest) as c:
            result = c.run(
                "tail -1 ~/voyage/artifacts/progress.txt 2>/dev/null || echo ''",
                hide=True,
            )
            return result.stdout.strip() or None
    except Exception:
        return None


def _check_completion_marker(storage: VM) -> bool:
    """Check if voyage-complete.marker exists."""
    try:
        with Connection(storage.ssh_dest) as c:
            result = c.run(
                "test -f ~/voyage/artifacts/voyage-complete.marker && echo yes || echo no",
                hide=True,
            )
            return str(result.stdout).strip() == "yes"
    except Exception:
        return False


def derive_status(voyage: "Voyage", storage: VM) -> VoyageStatus:
    """Derive full voyage status from task list."""
    tasks = list_tasks(storage, voyage)

    if not tasks:
        # No tasks - check for completion marker
        if _check_completion_marker(storage):
            inferred_state = VoyageState.COMPLETE
        elif _check_progress_file(storage):
            # Progress exists but no marker - still working
            inferred_state = VoyageState.RUNNING
        else:
            inferred_state = VoyageState.PLANNING

        return VoyageStatus(
            voyage=voyage,
            state=inferred_state,
            ships=(),
            tasks_complete=0,
            tasks_in_progress=0,
            tasks_pending=0,
            tasks_stale=0,
            tasks_total=0,
        )

    # Count task states
    complete = [t for t in tasks if t.status == TaskStatus.COMPLETED]
    in_progress = [t for t in tasks if t.status == TaskStatus.IN_PROGRESS]
    pending = [t for t in tasks if t.status == TaskStatus.PENDING]
    stale = [t for t in in_progress if t.is_stale()]

    # Derive ship states from task metadata
    ships_map: dict[str, ShipStatus] = {}

    for task in tasks:
        if task.assignee and task.assignee not in ships_map:
            ships_map[task.assignee] = ShipStatus(
                id=task.assignee,
                state=ShipState.UNKNOWN,
                current_task=None,
                claimed_at=None,
                completed_count=0,
            )

        if task.assignee:
            ship = ships_map[task.assignee]

            if task.status == TaskStatus.COMPLETED:
                ships_map[task.assignee] = ShipStatus(
                    id=ship.id,
                    state=ship.state,
                    current_task=ship.current_task,
                    claimed_at=ship.claimed_at,
                    completed_count=ship.completed_count + 1,
                )
            elif task.status == TaskStatus.IN_PROGRESS:
                state = ShipState.STALE if task.is_stale() else ShipState.WORKING
                ships_map[task.assignee] = ShipStatus(
                    id=ship.id,
                    state=state,
                    current_task=task.id,
                    claimed_at=task.claimed_at,
                    completed_count=ship.completed_count,
                )

    # Ships with no in-progress task but completed tasks are idle
    for ship_id, ship in ships_map.items():
        if ship.state == ShipState.UNKNOWN and ship.completed_count > 0:
            ships_map[ship_id] = ShipStatus(
                id=ship.id,
                state=ShipState.IDLE,
                current_task=None,
                claimed_at=None,
                completed_count=ship.completed_count,
            )

    ships = tuple(sorted(ships_map.values(), key=lambda s: s.id))

    # Derive voyage state
    voyage_state: VoyageState
    if len(complete) == len(tasks):
        voyage_state = VoyageState.COMPLETE
    elif len(stale) == len(in_progress) and len(in_progress) > 0 and pending:
        voyage_state = VoyageState.STALLED
    else:
        voyage_state = VoyageState.RUNNING

    return VoyageStatus(
        voyage=voyage,
        state=voyage_state,
        ships=ships,
        tasks_complete=len(complete),
        tasks_in_progress=len(in_progress),
        tasks_pending=len(pending),
        tasks_stale=len(stale),
        tasks_total=len(tasks),
    )


def reset_task(storage: VM, voyage: "Voyage", task_id: str) -> bool:
    """Reset a stale task to pending."""
    with Connection(storage.ssh_dest) as c:
        # Get home directory for SFTP operations
        home = c.run("echo $HOME", hide=True).stdout.strip()
        task_path = f"{home}/.claude/tasks/{voyage.task_list_id}/{task_id}.json"

        # Read current task
        result = c.run(f"cat {task_path}", hide=True)
        task_data = json.loads(result.stdout)

        # Update status
        task_data["status"] = "pending"
        task_data["updated"] = datetime.now(UTC).isoformat()
        if "metadata" in task_data:
            task_data["metadata"].pop("assignee", None)
            task_data["metadata"].pop("claimed_at", None)

        # Write back
        c.put(BytesIO(json.dumps(task_data, indent=2).encode()), task_path)

        return True


def reset_stale_tasks(storage: VM, voyage: "Voyage") -> int:
    """Reset all stale tasks to pending."""
    tasks = list_tasks(storage, voyage)
    stale = [t for t in tasks if t.is_stale()]

    for task in stale:
        reset_task(storage, voyage, task.id)

    return len(stale)
