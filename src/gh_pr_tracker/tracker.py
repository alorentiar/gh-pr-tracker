"""Compare current pull request state against the last run.

The point of this module is the diff, not the fetching. Every PR is reduced to
a flat snapshot of the fields worth watching; two snapshots that differ produce
a human readable Change.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from .github import GitHub
from .state import State

# Fields stored per PR. Adding one here is enough to start tracking it.
SNAPSHOT_FIELDS = (
    "state",
    "merged",
    "draft",
    "review_decision",
    "mergeable_state",
    "comments",
    "reviews",
    "last_actor",
    "checks_failed",
    "checks_pending",
    "checks_passed",
)


class TrackerError(RuntimeError):
    """Raised for problems that should stop a poll outright."""


@dataclass(frozen=True)
class PullRequest:
    """A pull request reduced to what the tracker cares about."""

    repo: str
    number: int
    title: str
    url: str
    state: str
    merged: bool = False
    draft: bool = False
    review_decision: str = ""
    mergeable_state: str = ""
    comments: int = 0
    reviews: int = 0
    last_actor: str = ""
    checks_failed: int = 0
    checks_pending: int = 0
    checks_passed: int = 0

    @property
    def key(self) -> str:
        return f"{self.repo}#{self.number}"

    def snapshot(self) -> dict[str, Any]:
        return {name: getattr(self, name) for name in SNAPSHOT_FIELDS}

    def describe(self) -> str:
        return f"{self.key} {self.title}"


@dataclass
class Change:
    """One or more differences for a single pull request."""

    pull_request: PullRequest
    fields: dict[str, tuple[Any, Any]] = field(default_factory=dict)

    def __bool__(self) -> bool:
        return bool(self.fields)

    def describe(self) -> str:
        """Short single line summary, suitable for logs or chat output."""
        bits = "; ".join(
            f"{name}: {_fmt(old)} -> {_fmt(new)}"
            for name, (old, new) in self.fields.items()
        )
        return f"{self.pull_request.key}: {bits}"

    def multiline(self) -> list[str]:
        """Readable block: headline plus one line per changed field."""
        pr = self.pull_request
        lines = [f"{pr.repo}#{pr.number} — {pr.title}"]
        for name, (old, new) in self.fields.items():
            lines.append(f"    {name}: {_fmt(old)} -> {_fmt(new)}")
        lines.append(f"    {pr.url}")
        return lines


def _fmt(value: Any) -> str:
    if value is None or value == "":
        return "none"
    if value is True:
        return "yes"
    if value is False:
        return "no"
    return str(value)


class Tracker:
    """Poll GitHub and emit the changes since the previous poll."""

    def __init__(
        self,
        github: GitHub | None = None,
        state: State | None = None,
        author: str | None = None,
    ) -> None:
        self.github = github or GitHub()
        self.state = state or State()
        self._author = author

    @property
    def author(self) -> str:
        if not self._author:
            self._author = self.github.viewer()
        return self._author

    # -- fetching ----------------------------------------------------------

    def fetch(self, author: str | None = None) -> list[PullRequest]:
        """All open PRs for the author, in a normalised form."""
        login = author or self.author
        results: list[PullRequest] = []
        for item in self.github.open_prs(login):
            repo = self.github.split_repo(item)
            number = int(item.get("number") or 0)
            if not number:
                continue
            results.append(self._enrich(repo, number, item))
        return results

    def _enrich(self, repo: str, number: int, item: dict[str, Any]) -> PullRequest:
        detail = self.github.pr_detail(repo, number)

        # A PR that just got merged or closed is no longer returned by the open
        # search, but detail can still report it; keep the merged flag accurate.
        merged = bool(detail.get("merged") or detail.get("merged_at"))
        state = (detail.get("state") or item.get("state") or "open").lower()

        reviews = self.github.pr_reviews(repo, number)
        comments = self.github.pr_comments(repo, number)
        checks = self.github.pr_checks(repo, number)

        failed = pending = passed = 0
        for run in checks:
            status = (run.get("status") or "").lower()
            conclusion = (run.get("conclusion") or "").lower()
            if status and status != "completed":
                pending += 1
            elif conclusion in ("success", "neutral", "skipped"):
                passed += 1
            else:
                failed += 1

        last_actor = ""
        timestamps: list[tuple[str, str]] = []
        for entry in comments:
            timestamps.append(
                (entry.get("updated_at") or "", (entry.get("user") or {}).get("login") or "")
            )
        for entry in reviews:
            timestamps.append(
                (entry.get("submitted_at") or "", (entry.get("user") or {}).get("login") or "")
            )
        if timestamps:
            timestamps.sort()
            last_actor = timestamps[-1][1]

        return PullRequest(
            repo=repo,
            number=number,
            title=(detail.get("title") or item.get("title") or "").strip(),
            url=detail.get("html_url") or item.get("html_url") or "",
            state=state,
            merged=merged,
            draft=bool(detail.get("draft")),
            review_decision=str(detail.get("review_decision") or "").lower(),
            mergeable_state=str(detail.get("mergeable_state") or "").lower(),
            comments=len(comments),
            reviews=len(reviews),
            last_actor=last_actor,
            checks_failed=failed,
            checks_pending=pending,
            checks_passed=passed,
        )

    # -- diffing -----------------------------------------------------------

    def diff(self, current: list[PullRequest]) -> list[Change]:
        """Compare against stored state without writing anything."""
        changes: list[Change] = []
        for pr in current:
            previous = self.state.get(pr.key)
            if previous is None:
                changes.append(Change(pr, {"new": ("", "tracked")}))
                continue
            now = pr.snapshot()
            fields = {
                name: (previous.get(name), now.get(name))
                for name in SNAPSHOT_FIELDS
                if previous.get(name) != now.get(name)
            }
            if fields:
                changes.append(Change(pr, fields))
        return changes

    def poll(self, author: str | None = None, persist: bool = True) -> list[Change]:
        """Fetch, diff, and (by default) store the new state."""
        current = self.fetch(author)
        changes = self.diff(current)
        if persist:
            for pr in current:
                self.state.set(pr.key, pr.snapshot())
            self.state.drop_missing({pr.key for pr in current})
            self.state.save()
        return changes

    def snapshot_only(self, author: str | None = None) -> int:
        """Record current state and return the number of PRs, no output."""
        current = self.fetch(author)
        for pr in current:
            self.state.set(pr.key, pr.snapshot())
        self.state.drop_missing({pr.key for pr in current})
        self.state.save()
        return len(current)


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")
