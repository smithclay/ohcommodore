"""Task list operations and status derivation."""

import json
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import Enum
from io import StringIO

# Forward reference for Voyage to avoid circular import
from typing import TYPE_CHECKING, Any

from fabric import Connection

from .config import CONFIG
from .provider import VM

if TYPE_CHECKING:
    from .voyage import Voyage


class TaskStatus(str, Enum):
    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    COMPLETE = "complete"


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
        return cls(
            id=data["id"],
            title=data["title"],
            description=data.get("description", ""),
            status=TaskStatus(data["status"]),
            blocked_by=data.get("blockedBy", []),
            blocks=data.get("blocks", []),
            created=datetime.fromisoformat(data["created"]),
            updated=datetime.fromisoformat(data["updated"]),
            assignee=metadata.get("assignee"),
            claimed_at=(
                datetime.fromisoformat(metadata["claimed_at"])
                if metadata.get("claimed_at")
                else None
            ),
            completed_by=metadata.get("completed_by"),
            completed_at=(
                datetime.fromisoformat(metadata["completed_at"])
                if metadata.get("completed_at")
                else None
            ),
        )

    def is_stale(self, threshold_minutes: int | None = None) -> bool:
        """Check if an in-progress task is stale."""
        if self.status != TaskStatus.IN_PROGRESS or not self.claimed_at:
            return False

        threshold = threshold_minutes or CONFIG.stale_threshold_minutes
        age = datetime.now(UTC) - self.claimed_at
        return age.total_seconds() > threshold * 60


@dataclass
class ShipStatus:
    """Derived status for a ship."""

    id: str
    state: ShipState
    current_task: str | None
    claimed_at: datetime | None
    completed_count: int


@dataclass
class VoyageStatus:
    """Derived status for a voyage."""

    voyage: "Voyage"
    state: VoyageState
    ships: list[ShipStatus]
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
            except json.JSONDecodeError:
                break

        return tasks


def derive_status(voyage: "Voyage", storage: VM) -> VoyageStatus:
    """Derive full voyage status from task list."""
    tasks = list_tasks(storage, voyage)

    if not tasks:
        return VoyageStatus(
            voyage=voyage,
            state=VoyageState.PLANNING,
            ships=[],
            tasks_complete=0,
            tasks_in_progress=0,
            tasks_pending=0,
            tasks_stale=0,
            tasks_total=0,
        )

    # Count task states
    complete = [t for t in tasks if t.status == TaskStatus.COMPLETE]
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

            if task.status == TaskStatus.COMPLETE:
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

    ships = sorted(ships_map.values(), key=lambda s: s.id)

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
        task_path = f"~/.claude/tasks/{voyage.task_list_id}/{task_id}.json"

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
        c.put(StringIO(json.dumps(task_data, indent=2)), task_path)

        return True


def reset_stale_tasks(storage: VM, voyage: "Voyage") -> int:
    """Reset all stale tasks to pending."""
    tasks = list_tasks(storage, voyage)
    stale = [t for t in tasks if t.is_stale()]

    for task in stale:
        reset_task(storage, voyage, task.id)

    return len(stale)
