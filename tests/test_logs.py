"""Unit tests for log viewing."""

from ocaptain.logs import _SHIP_ID_PATTERN


def test_valid_ship_id_pattern() -> None:
    """Test valid ship_id patterns."""
    assert _SHIP_ID_PATTERN.match("ship-0")
    assert _SHIP_ID_PATTERN.match("ship-1")
    assert _SHIP_ID_PATTERN.match("ship-99")
    assert _SHIP_ID_PATTERN.match("ship-123")


def test_invalid_ship_id_pattern() -> None:
    """Test invalid ship_id patterns are rejected."""
    # Path traversal attempts
    assert not _SHIP_ID_PATTERN.match("../etc/passwd")
    assert not _SHIP_ID_PATTERN.match("ship-0/../etc/passwd")
    assert not _SHIP_ID_PATTERN.match("ship-0; rm -rf /")

    # Wrong format
    assert not _SHIP_ID_PATTERN.match("ship-")
    assert not _SHIP_ID_PATTERN.match("ship")
    assert not _SHIP_ID_PATTERN.match("boat-0")
    assert not _SHIP_ID_PATTERN.match("SHIP-0")
    assert not _SHIP_ID_PATTERN.match("ship-abc")
    assert not _SHIP_ID_PATTERN.match(" ship-0")
    assert not _SHIP_ID_PATTERN.match("ship-0 ")
