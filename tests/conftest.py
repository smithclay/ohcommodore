"""Pytest fixtures for ocaptain tests."""

from unittest.mock import MagicMock

import pytest

from ocaptain.provider import VM, VMStatus


@pytest.fixture  # type: ignore[untyped-decorator]
def mock_vm() -> VM:
    """Create a mock VM for testing."""
    return VM(
        id="vm-123",
        name="test-storage",
        ssh_dest="test@localhost",
        status=VMStatus.RUNNING,
    )


@pytest.fixture  # type: ignore[untyped-decorator]
def mock_provider(mock_vm: VM) -> MagicMock:
    """Create a mock provider for testing."""
    provider = MagicMock()
    provider.create.return_value = mock_vm
    provider.list.return_value = [mock_vm]
    provider.get.return_value = mock_vm
    provider.wait_ready.return_value = True
    return provider
