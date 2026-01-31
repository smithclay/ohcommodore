"""VM provider implementations."""

import contextlib

from . import (
    exedev,  # noqa: F401 - triggers registration
    sprites,  # noqa: F401 - triggers registration
)

# Optional provider - only register if boxlite is installed
with contextlib.suppress(ImportError):
    from . import boxlite  # noqa: F401 - triggers registration
