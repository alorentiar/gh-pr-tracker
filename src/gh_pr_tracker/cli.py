"""Command line interface for gh-pr-tracker.

Designed to be quiet by default: `poll` prints nothing when no PR changed, so
it can sit in a cron job or a shell prompt without noise.
"""

from __future__ import annotations

import argparse
import json
import sys

from . import __version__
from .github import GitHub, GitHubError
from .state import ENV_STATE, State, default_state_path
from .tracker import Tracker, TrackerError

EXIT_OK = 0
EXIT_CHANGES = 0
EXIT_ERROR = 1
EXIT_NO_AUTH = 2
EXIT_QUIET_NO_CHANGES = 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="gh-pr-tracker",
        description=(
            "Track the pull requests you opened. Reports only what changed, "
            "using the gh CLI for API access."
        ),
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    parser.add_argument(
        "--state",
        metavar="PATH",
        help=f"state file to use (default: {default_state_path()}, env {ENV_STATE})",
    )
    parser.add_argument(
        "--author",
        metavar="LOGIN",
        help="track this user instead of the authenticated one",
    )

    sub = parser.add_subparsers(dest="command", required=True)

    poll = sub.add_parser("poll", help="fetch, report changes, and save state")
    poll.add_argument("--json", action="store_true", help="emit JSON instead of text")
    poll.add_argument(
        "--all",
        dest="show_all",
        action="store_true",
        help="also print PRs that did not change",
    )
    poll.add_argument(
        "--dry-run",
        action="store_true",
        help="report changes but do not save state",
    )

    snap = sub.add_parser(
        "snapshot", help="record current state silently (first run helper)"
    )
    snap.add_argument("--json", action="store_true", help="emit JSON summary")

    lst = sub.add_parser("list", help="show tracked PRs from local state")
    lst.add_argument("--json", action="store_true")

    forget = sub.add_parser("forget", help="drop a PR or all state")
    forget.add_argument("key", nargs="?", help="'owner/repo#123', or omit with --all")
    forget.add_argument("--all", action="store_true", help="clear all stored state")

    thr = sub.add_parser("threshold", help="exit non-zero when conditions are met")
    thr.add_argument(
        "--fail-on-ci",
        action="store_true",
        help="exit 3 if any tracked PR has failing checks",
    )
    thr.add_argument(
        "--fail-on-review",
        action="store_true",
        help="exit 3 if any tracked PR has changes requested",
    )

    sub.add_parser("whoami", help="show the authenticated GitHub login")

    return parser


def _make_tracker(args: argparse.Namespace) -> Tracker:
    state = State(args.state) if args.state else State()
    return Tracker(github=GitHub(), state=state, author=args.author)


def _print_changes(changes, as_json: bool) -> None:
    if as_json:
        payload = [
            {
                "repo": c.pull_request.repo,
                "number": c.pull_request.number,
                "title": c.pull_request.title,
                "url": c.pull_request.url,
                "changes": {
                    name: {"from": old, "to": new}
                    for name, (old, new) in c.fields.items()
                },
            }
            for c in changes
        ]
        print(json.dumps(payload, indent=2))
        return
    for change in changes:
        print("\n".join(change.multiline()))


def cmd_poll(args: argparse.Namespace) -> int:
    tracker = _make_tracker(args)
    try:
        changes = tracker.poll(author=args.author, persist=not args.dry_run)
    except GitHubError as exc:
        print(f"gh error: {exc}", file=sys.stderr)
        return EXIT_NO_AUTH if "auth" in str(exc).lower() else EXIT_ERROR
    except TrackerError as exc:
        print(f"tracker error: {exc}", file=sys.stderr)
        return EXIT_ERROR

    if changes:
        _print_changes(changes, args.json)
        return EXIT_CHANGES
    if args.show_all:
        tracked = tracker.state.as_dict()
        if args.json:
            print(json.dumps(list(tracked.keys()), indent=2))
        elif tracked:
            for key in sorted(tracked):
                print(f"{key}  (no change)")
        else:
            print("no pull requests tracked")
    return EXIT_QUIET_NO_CHANGES


def cmd_snapshot(args: argparse.Namespace) -> int:
    tracker = _make_tracker(args)
    try:
        count = tracker.snapshot_only(author=args.author)
    except GitHubError as exc:
        print(f"gh error: {exc}", file=sys.stderr)
        return EXIT_NO_AUTH if "auth" in str(exc).lower() else EXIT_ERROR
    if args.json:
        print(json.dumps({"tracked": count, "state": str(tracker.state.path)}))
    else:
        print(f"recorded {count} pull request(s) in {tracker.state.path}")
    return EXIT_OK


def cmd_list(args: argparse.Namespace) -> int:
    state = State(args.state) if args.state else State()
    data = state.as_dict()
    if args.json:
        print(json.dumps(data, indent=2, sort_keys=True))
        return EXIT_OK
    if not data:
        print(f"nothing tracked yet (state file: {state.path})")
        return EXIT_OK
    for key in sorted(data):
        entry = data[key]
        print(f"{key:44} {entry.get('state', '?'):8} {entry.get('review_decision') or '-'}")
    return EXIT_OK


def cmd_forget(args: argparse.Namespace) -> int:
    state = State(args.state) if args.state else State()
    if args.all:
        removed = len(state.as_dict())
        state._data = {}
        state.save()
        print(f"cleared {removed} entr(y/ies)")
        return EXIT_OK
    if not args.key:
        print("give a key like owner/repo#123, or use --all", file=sys.stderr)
        return EXIT_ERROR
    if state.get(args.key) is None:
        print(f"{args.key} is not tracked", file=sys.stderr)
        return EXIT_ERROR
    state._data.pop(args.key, None)
    state.save()
    print(f"forgot {args.key}")
    return EXIT_OK


def cmd_threshold(args: argparse.Namespace) -> int:
    state = State(args.state) if args.state else State()
    data = state.as_dict()
    problems: list[str] = []
    for key, entry in sorted(data.items()):
        if args.fail_on_ci and int(entry.get("checks_failed") or 0) > 0:
            problems.append(f"{key}: {entry.get('checks_failed')} failing check(s)")
        if args.fail_on_review and entry.get("review_decision") == "changes_requested":
            problems.append(f"{key}: changes requested")
    if problems:
        for line in problems:
            print(line)
        return 3
    print("no blocking conditions")
    return EXIT_OK


def cmd_whoami(args: argparse.Namespace) -> int:
    ok, message = GitHub().check_auth()
    if not ok:
        print(message, file=sys.stderr)
        return EXIT_NO_AUTH
    print(message)
    return EXIT_OK


COMMANDS = {
    "poll": cmd_poll,
    "snapshot": cmd_snapshot,
    "list": cmd_list,
    "forget": cmd_forget,
    "threshold": cmd_threshold,
    "whoami": cmd_whoami,
}


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    handler = COMMANDS.get(args.command)
    if handler is None:
        parser.print_help()
        return EXIT_ERROR
    try:
        return handler(args)
    except KeyboardInterrupt:
        return 130
    except BrokenPipeError:
        return EXIT_OK


if __name__ == "__main__":
    raise SystemExit(main())
