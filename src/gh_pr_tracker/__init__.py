"""gh-pr-tracker: track the pull requests you opened on GitHub.

Reads GitHub data through the `gh` CLI, so there is no token to manage. It
keeps a small local state file and only reports what actually changed since
the last run, which makes it usable from cron or a git hook without spamming.

The public surface is small on purpose:

    from gh_pr_tracker import GitHub, Tracker

    gh = GitHub()
    tracker = Tracker(gh)
    for change in tracker.poll():
        print(change.describe())
"""

from .github import GitHub, GitHubError
from .state import State
from .tracker import Change, PullRequest, Tracker, TrackerError

__all__ = [
    "GitHub",
    "GitHubError",
    "State",
    "Tracker",
    "TrackerError",
    "PullRequest",
    "Change",
]

__version__ = "0.1.0"
