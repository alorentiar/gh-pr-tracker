"""Thin wrapper around the `gh` CLI.

Everything goes through `gh api` rather than raw HTTP so users do not have to
create a personal access token: if `gh auth status` passes, this works. The
wrapper stays deliberately small, it is not a general GitHub SDK.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from collections.abc import Iterable
from typing import Any

DEFAULT_TIMEOUT = 60


class GitHubError(RuntimeError):
    """Raised when gh is missing, unauthenticated, or the API call fails."""


class GitHub:
    """Run GitHub API calls through the gh CLI."""

    def __init__(self, gh_binary: str = "gh", timeout: int = DEFAULT_TIMEOUT) -> None:
        self.gh_binary = gh_binary
        self.timeout = timeout

    # -- internals ---------------------------------------------------------

    def _run(self, args: list[str]) -> str:
        if shutil.which(self.gh_binary) is None:
            raise GitHubError(
                f"'{self.gh_binary}' was not found on PATH. "
                "Install it from https://cli.github.com and run 'gh auth login'."
            )
        try:
            proc = subprocess.run(
                [self.gh_binary, *args],
                capture_output=True,
                text=True,
                timeout=self.timeout,
            )
        except subprocess.TimeoutExpired as exc:
            raise GitHubError(f"gh timed out after {self.timeout}s") from exc

        if proc.returncode != 0:
            msg = (proc.stderr or proc.stdout or "").strip()
            raise GitHubError(msg or f"gh exited with code {proc.returncode}")
        return proc.stdout

    def _api(self, path: str, params: dict[str, Any] | None = None) -> Any:
        args = ["api", path]
        for key, value in (params or {}).items():
            args += ["-f", f"{key}={value}"]
        out = self._run(args)
        try:
            return json.loads(out) if out.strip() else None
        except json.JSONDecodeError as exc:
            raise GitHubError(f"could not parse gh output as JSON: {exc}") from exc

    @staticmethod
    def _paginate(path: str, params: dict[str, Any] | None = None) -> list[str]:
        args = ["api", "--paginate", path]
        for key, value in (params or {}).items():
            args += ["-f", f"{key}={value}"]
        return args

    # -- public API --------------------------------------------------------

    def viewer(self) -> str:
        """Login of the authenticated user."""
        out = self._run(["api", "user", "--jq", ".login"])
        login = out.strip()
        if not login:
            raise GitHubError(
                "gh is not authenticated. Run 'gh auth login' first."
            )
        return login

    def check_auth(self) -> tuple[bool, str]:
        """Return (ok, message) instead of raising, for friendly CLI output."""
        try:
            return True, self.viewer()
        except GitHubError as exc:
            return False, str(exc)

    def open_prs(self, author: str, limit: int = 100) -> list[dict[str, Any]]:
        """Pull requests opened by `author` that are still open."""
        items: list[dict[str, Any]] = []
        page = 1
        while len(items) < limit:
            path = (
                "search/issues?q="
                f"author:{author}+type:pr+is:open&per_page=100&page={page}"
            )
            data = self._api(path)
            batch = (data or {}).get("items") or []
            if not batch:
                break
            items.extend(batch)
            if len(batch) < 100:
                break
            page += 1
        return items[:limit]

    def pr_detail(self, repo: str, number: int) -> dict[str, Any]:
        """Single pull request with review decision and merge state."""
        return self._api(f"repos/{repo}/pulls/{number}") or {}

    def pr_reviews(self, repo: str, number: int) -> list[dict[str, Any]]:
        return self._api(f"repos/{repo}/pulls/{number}/reviews") or []

    def pr_comments(self, repo: str, number: int) -> list[dict[str, Any]]:
        return self._api(f"repos/{repo}/issues/{number}/comments") or []

    def pr_checks(self, repo: str, number: int) -> list[dict[str, Any]]:
        """Check runs for the head commit, empty when none are configured."""
        detail = self.pr_detail(repo, number)
        sha = ((detail.get("head") or {}).get("sha")) or ""
        if not sha:
            return []
        data = self._api(f"repos/{repo}/commits/{sha}/check-runs")
        return (data or {}).get("check_runs") or []

    def split_repo(self, item: dict[str, Any]) -> str:
        """'owner/name' out of a search result item."""
        url = item.get("repository_url") or ""
        marker = "/repos/"
        if marker in url:
            return url.split(marker, 1)[1]
        full = item.get("repository", {}).get("full_name")
        if full:
            return str(full)
        raise GitHubError("could not determine repository for a search result")

    def iter_open_prs(self, author: str) -> Iterable[dict[str, Any]]:
        """Yield open PRs one at a time, for callers that stream."""
        yield from self.open_prs(author)
