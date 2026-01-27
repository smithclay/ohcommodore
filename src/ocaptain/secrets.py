"""Token loading and repository validation."""

import os
import subprocess  # nosec B404

from .config import CONFIG  # Ensures .env files are loaded


def load_tokens() -> dict[str, str]:
    """Load tokens from environment (including .env files loaded by config).

    Returns dict with available tokens.
    CLAUDE_CODE_OAUTH_TOKEN is required (raises ValueError if missing).
    GH_TOKEN is optional.
    """
    # Force config load to ensure .env files are processed
    _ = CONFIG

    tokens: dict[str, str] = {}

    # Check for CLAUDE_CODE_OAUTH_TOKEN
    if claude_token := os.environ.get("CLAUDE_CODE_OAUTH_TOKEN"):
        tokens["CLAUDE_CODE_OAUTH_TOKEN"] = claude_token
    else:
        raise ValueError(
            "CLAUDE_CODE_OAUTH_TOKEN is required. "
            "Set it in environment or in .env file (cwd or ~/.config/ocaptain/.env)"
        )

    # Check for GH_TOKEN (optional)
    if gh_token := os.environ.get("GH_TOKEN"):
        tokens["GH_TOKEN"] = gh_token

    return tokens


def validate_repo_access(repo: str, gh_token: str | None = None) -> None:
    """Validate access to GitHub repository before provisioning.

    Args:
        repo: GitHub repository in "owner/repo" format
        gh_token: Optional GitHub token for private repos

    Raises:
        ValueError: If repo is inaccessible
    """
    # Validate repo format
    if "/" not in repo or repo.count("/") != 1:
        raise ValueError(f"Invalid repo format: {repo}. Expected 'owner/repo'")

    if gh_token:
        # Use gh CLI with token
        result = subprocess.run(  # nosec B603, B607
            ["gh", "api", f"repos/{repo}", "--silent"],
            env={**os.environ, "GH_TOKEN": gh_token},
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            stderr = result.stderr.strip()
            if "404" in stderr or "Not Found" in stderr:
                raise ValueError(f"Repository not found: {repo}")
            elif "401" in stderr or "403" in stderr:
                raise ValueError(
                    f"Access denied to repository: {repo}. Check GH_TOKEN permissions."
                )
            else:
                raise ValueError(f"Failed to access repository {repo}: {stderr}")
    else:
        # Try unauthenticated access for public repos
        result = subprocess.run(  # nosec B603, B607
            ["curl", "-sf", f"https://api.github.com/repos/{repo}"],
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            raise ValueError(
                f"Cannot access repository: {repo}. "
                "For private repos, set GH_TOKEN in environment or .env file."
            )
