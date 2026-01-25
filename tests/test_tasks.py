"""Unit tests for task parsing and status derivation."""

from datetime import UTC, datetime, timedelta

from ocaptain.tasks import Task, TaskStatus


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
        status=TaskStatus.COMPLETE,
        blocked_by=[],
        blocks=[],
        created=old_time,
        updated=old_time,
        completed_by="ship-0",
        completed_at=old_time,
    )

    assert not task.is_stale()
