"""Integration tests for voyage module with mocked provider."""

from dataclasses import FrozenInstanceError

import pytest

from ocaptain.voyage import Voyage


def test_voyage_create() -> None:
    """Test creating a new voyage."""
    voyage = Voyage.create("Test prompt", "owner/repo", 3)

    assert voyage.id.startswith("voyage-")
    assert voyage.prompt == "Test prompt"
    assert voyage.repo == "owner/repo"
    assert voyage.branch == voyage.id
    assert voyage.task_list_id == f"{voyage.id}-tasks"
    assert voyage.ship_count == 3


def test_voyage_json_roundtrip() -> None:
    """Test voyage serialization and deserialization."""
    original = Voyage.create("Test prompt", "owner/repo", 3)

    json_str = original.to_json()
    restored = Voyage.from_json(json_str)

    assert restored.id == original.id
    assert restored.prompt == original.prompt
    assert restored.repo == original.repo
    assert restored.branch == original.branch
    assert restored.task_list_id == original.task_list_id
    assert restored.ship_count == original.ship_count
    assert restored.created_at == original.created_at


def test_voyage_storage_name() -> None:
    """Test storage VM naming."""
    voyage = Voyage.create("Test", "owner/repo", 3)

    assert voyage.storage_name == f"{voyage.id}-storage"


def test_voyage_ship_name() -> None:
    """Test ship VM naming."""
    voyage = Voyage.create("Test", "owner/repo", 3)

    assert voyage.ship_name(0) == f"{voyage.id}-ship-0"
    assert voyage.ship_name(1) == f"{voyage.id}-ship-1"
    assert voyage.ship_name(2) == f"{voyage.id}-ship-2"


def test_voyage_is_immutable() -> None:
    """Test that voyage is frozen/immutable."""
    voyage = Voyage.create("Test", "owner/repo", 3)

    with pytest.raises(FrozenInstanceError):
        voyage.prompt = "Modified"  # type: ignore[misc]
