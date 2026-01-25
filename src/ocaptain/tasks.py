"""Task list operations and status derivation."""

import json
import logging
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import Enum

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
                logger.error(
                    "Failed to parse task JSON for voyage %s at position %d, "
                    "returning %d of potentially more tasks: %s",
                    voyage.task_list_id,
                    pos,
                    len(tasks),
                    e,
                )
                break

        return tasks


def _check_voyage_artifacts(storage: VM) -> tuple[bool, bool]:
    """Check voyage artifacts for status indicators.

    Returns:
        Tuple of (has_completion_marker, has_progress_file)
    """
    try:
        with Connection(storage.ssh_dest) as c:
            check_cmd = (
                "test -f ~/voyage/artifacts/voyage-complete.marker "
                "&& echo marker:yes || echo marker:no; "
                "test -f ~/voyage/artifacts/progress.txt "
                "&& echo progress:yes || echo progress:no"
            )
            result = c.run(check_cmd, hide=True)
            output = result.stdout.strip()
            has_marker = "marker:yes" in output
            has_progress = "progress:yes" in output
            return has_marker, has_progress
    except Exception as e:
        logger.warning(
            "Failed to check voyage artifacts on storage %s: %s",
            storage.ssh_dest,
            e,
        )
        return False, False


def derive_status(voyage: "Voyage", storage: VM) -> VoyageStatus:
    """Derive full voyage status from task list."""
    tasks = list_tasks(storage, voyage)

    if not tasks:
        # No tasks - check artifacts for status
        has_marker, has_progress = _check_voyage_artifacts(storage)
        if has_marker:
            inferred_state = VoyageState.COMPLETE
        elif has_progress:
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

    # Derive ship states from task metadata using mutable intermediate state
    ship_data: dict[str, dict[str, Any]] = {}

    for task in tasks:
        if not task.assignee:
            continue

        if task.assignee not in ship_data:
            ship_data[task.assignee] = {
                "state": ShipState.UNKNOWN,
                "current_task": None,
                "claimed_at": None,
                "completed_count": 0,
            }

        data = ship_data[task.assignee]
        if task.status == TaskStatus.COMPLETED:
            data["completed_count"] += 1
        elif task.status == TaskStatus.IN_PROGRESS:
            data["state"] = ShipState.STALE if task.is_stale() else ShipState.WORKING
            data["current_task"] = task.id
            data["claimed_at"] = task.claimed_at

    # Ships with no in-progress task but completed tasks are idle
    for data in ship_data.values():
        if data["state"] == ShipState.UNKNOWN and data["completed_count"] > 0:
            data["state"] = ShipState.IDLE

    # Convert to frozen ShipStatus objects
    ships = tuple(
        sorted(
            (
                ShipStatus(
                    id=ship_id,
                    state=data["state"],
                    current_task=data["current_task"],
                    claimed_at=data["claimed_at"],
                    completed_count=data["completed_count"],
                )
                for ship_id, data in ship_data.items()
            ),
            key=lambda s: s.id,
        )
    )

    # Derive voyage state
    voyage_state: VoyageState
    if len(complete) == len(tasks):
        voyage_state = VoyageState.COMPLETE
    elif len(in_progress) == 0 and pending:
        voyage_state = VoyageState.PLANNING
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
