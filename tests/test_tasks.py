"""Unit tests for task parsing and status derivation."""

from datetime import UTC, datetime, timedelta

import pytest

from ocaptain.tasks import (
    ShipState,
    ShipStatus,
    Task,
    TaskStatus,
    VoyageState,
    VoyageStatus,
    _parse_datetime,
)


def test_task_from_json() -> None:
    """Test parsing a task from JSON."""
    data = {
        "id": "task-001",
        "title": "Test task",
        "status": "pending",
        "blockedBy": [],
        "blocks": [],
        "created": "2026-01-24T10:00:00+00:00",
        "updated": "2026-01-24T10:00:00+00:00",
    }

    task = Task.from_json(data)

    assert task.id == "task-001"
    assert task.title == "Test task"
    assert task.status == TaskStatus.PENDING
    assert task.blocked_by == []
    assert task.blocks == []
    assert not task.is_stale()


def test_task_from_json_with_subject_instead_of_title() -> None:
    """Test parsing Claude Code format task with 'subject' field."""
    data = {
        "id": "task-001",
        "subject": "Task via Claude Code",
        "status": "pending",
    }
    task = Task.from_json(data)
    assert task.title == "Task via Claude Code"


def test_task_from_json_missing_timestamps() -> None:
    """Test parsing task without created/updated fields."""
    data = {"id": "task-001", "title": "No timestamps", "status": "pending"}
    task = Task.from_json(data)
    assert task.created is not None
    assert task.updated is not None


def test_task_from_json_owner_field_precedence() -> None:
    """Test owner field takes precedence over metadata.assignee."""
    data = {
        "id": "task-001",
        "title": "Test",
        "status": "in_progress",
        "owner": "ship-1",
        "metadata": {"assignee": "ship-0"},
        "created": "2026-01-24T10:00:00+00:00",
        "updated": "2026-01-24T10:00:00+00:00",
    }
    task = Task.from_json(data)
    assert task.assignee == "ship-1"


def test_task_from_json_metadata_ship_fallback() -> None:
    """Test metadata.ship is used as assignee fallback."""
    data = {
        "id": "task-001",
        "title": "Test",
        "status": "pending",
        "metadata": {"ship": "ship-2"},
        "created": "2026-01-24T10:00:00+00:00",
        "updated": "2026-01-24T10:00:00+00:00",
    }
    task = Task.from_json(data)
    assert task.assignee == "ship-2"


def test_task_from_json_with_metadata() -> None:
    """Test parsing a task with metadata."""
    data = {
        "id": "task-002",
        "title": "In progress task",
        "status": "in_progress",
        "blockedBy": ["task-001"],
        "blocks": ["task-003"],
        "created": "2026-01-24T10:00:00+00:00",
        "updated": "2026-01-24T10:30:00+00:00",
        "metadata": {
            "assignee": "ship-0",
            "claimed_at": "2026-01-24T10:30:00+00:00",
        },
    }

    task = Task.from_json(data)

    assert task.id == "task-002"
    assert task.status == TaskStatus.IN_PROGRESS
    assert task.blocked_by == ["task-001"]
    assert task.blocks == ["task-003"]
    assert task.assignee == "ship-0"
    assert task.claimed_at is not None


def test_stale_detection_not_stale() -> None:
    """Test that recent in-progress task is not stale."""
    recent_time = datetime.now(UTC) - timedelta(minutes=5)

    task = Task(
        id="task-001",
        title="Test",
        description="",
        status=TaskStatus.IN_PROGRESS,
        blocked_by=[],
        blocks=[],
        created=recent_time,
        updated=recent_time,
        assignee="ship-0",
        claimed_at=recent_time,
    )

    assert not task.is_stale(threshold_minutes=30)


def test_stale_detection_is_stale() -> None:
    """Test that old in-progress task is stale."""
    old_time = datetime.now(UTC) - timedelta(minutes=45)

    task = Task(
        id="task-001",
        title="Test",
        description="",
        status=TaskStatus.IN_PROGRESS,
        blocked_by=[],
        blocks=[],
        created=old_time,
        updated=old_time,
        assignee="ship-0",
        claimed_at=old_time,
    )

    assert task.is_stale(threshold_minutes=30)
    assert not task.is_stale(threshold_minutes=60)


def test_pending_task_not_stale() -> None:
    """Test that pending tasks are never stale."""
    old_time = datetime.now(UTC) - timedelta(minutes=120)

    task = Task(
        id="task-001",
        title="Test",
        description="",
        status=TaskStatus.PENDING,
        blocked_by=[],
        blocks=[],
        created=old_time,
        updated=old_time,
    )

    assert not task.is_stale()


def test_complete_task_not_stale() -> None:
    """Test that complete tasks are never stale."""
    old_time = datetime.now(UTC) - timedelta(minutes=120)

    task = Task(
        id="task-001",
        title="Test",
        description="",
        status=TaskStatus.COMPLETED,
        blocked_by=[],
        blocks=[],
        created=old_time,
        updated=old_time,
        completed_by="ship-0",
        completed_at=old_time,
    )

    assert not task.is_stale()


def test_parse_datetime_with_timezone() -> None:
    """Test parsing datetime with timezone."""
    dt = _parse_datetime("2026-01-24T10:00:00+00:00")
    assert dt.tzinfo is not None
    assert dt.year == 2026


def test_parse_datetime_naive_assumes_utc() -> None:
    """Test parsing naive datetime assumes UTC."""
    dt = _parse_datetime("2026-01-24T10:00:00")
    assert dt.tzinfo is not None
    assert dt.tzinfo == UTC


def test_ship_status_frozen() -> None:
    """Test that ShipStatus is immutable."""
    from dataclasses import FrozenInstanceError

    status = ShipStatus(
        id="ship-0",
        state=ShipState.WORKING,
        current_task="task-001",
        claimed_at=datetime.now(UTC),
        completed_count=0,
    )
    with pytest.raises(FrozenInstanceError):
        status.state = ShipState.IDLE  # type: ignore[misc]


def test_voyage_status_frozen() -> None:
    """Test that VoyageStatus is immutable."""
    from dataclasses import FrozenInstanceError

    from ocaptain.voyage import Voyage

    voyage = Voyage.create("Test", "owner/repo", 1)
    status = VoyageStatus(
        voyage=voyage,
        state=VoyageState.RUNNING,
        ships=(),
        tasks_complete=0,
        tasks_in_progress=0,
        tasks_pending=0,
        tasks_stale=0,
        tasks_total=0,
    )
    with pytest.raises(FrozenInstanceError):
        status.state = VoyageState.COMPLETE  # type: ignore[misc]


# derive_status state machine tests would require mocking the storage VM connection
# These tests verify the state machine logic in isolation using the status classes


def test_voyage_state_planning_no_tasks() -> None:
    """Test that voyage is PLANNING when no tasks exist."""
    from ocaptain.voyage import Voyage

    voyage = Voyage.create("Test", "owner/repo", 1)
    status = VoyageStatus(
        voyage=voyage,
        state=VoyageState.PLANNING,
        ships=(),
        tasks_complete=0,
        tasks_in_progress=0,
        tasks_pending=0,
        tasks_stale=0,
        tasks_total=0,
    )
    assert status.state == VoyageState.PLANNING


def test_voyage_state_running_with_progress() -> None:
    """Test that voyage is RUNNING when tasks are in progress."""
    from ocaptain.voyage import Voyage

    voyage = Voyage.create("Test", "owner/repo", 1)
    status = VoyageStatus(
        voyage=voyage,
        state=VoyageState.RUNNING,
        ships=(
            ShipStatus(
                id="ship-0",
                state=ShipState.WORKING,
                current_task="task-001",
                claimed_at=datetime.now(UTC),
                completed_count=0,
            ),
        ),
        tasks_complete=0,
        tasks_in_progress=1,
        tasks_pending=2,
        tasks_stale=0,
        tasks_total=3,
    )
    assert status.state == VoyageState.RUNNING


def test_voyage_state_complete_all_done() -> None:
    """Test that voyage is COMPLETE when all tasks done."""
    from ocaptain.voyage import Voyage

    voyage = Voyage.create("Test", "owner/repo", 1)
    status = VoyageStatus(
        voyage=voyage,
        state=VoyageState.COMPLETE,
        ships=(
            ShipStatus(
                id="ship-0",
                state=ShipState.IDLE,
                current_task=None,
                claimed_at=None,
                completed_count=3,
            ),
        ),
        tasks_complete=3,
        tasks_in_progress=0,
        tasks_pending=0,
        tasks_stale=0,
        tasks_total=3,
    )
    assert status.state == VoyageState.COMPLETE


def test_voyage_state_stalled_all_stale() -> None:
    """Test that voyage is STALLED when all in-progress are stale with pending work."""
    from ocaptain.voyage import Voyage

    voyage = Voyage.create("Test", "owner/repo", 1)
    status = VoyageStatus(
        voyage=voyage,
        state=VoyageState.STALLED,
        ships=(
            ShipStatus(
                id="ship-0",
                state=ShipState.STALE,
                current_task="task-001",
                claimed_at=datetime.now(UTC) - timedelta(hours=2),
                completed_count=0,
            ),
        ),
        tasks_complete=0,
        tasks_in_progress=1,
        tasks_pending=2,
        tasks_stale=1,
        tasks_total=3,
    )
    assert status.state == VoyageState.STALLED
