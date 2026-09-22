# gh-pr-tracker

Track the pull requests you opened on GitHub, and report **only what changed**.

`gh-pr-tracker` remembers the last state of each open PR and prints a diff on
the next run: review decisions, merge conflicts, CI results, new comments,
whether it got merged or closed. When nothing moved, it says nothing, which
makes it safe to run from cron, a git hook, or your shell prompt.

```
$ gh-pr-tracker poll
alorentiar/awesome-lib#42 — Fix off-by-one in the parser
    review_decision: none -> approved
    checks_passed: 0 -> 4
    https://github.com/alorentiar/awesome-lib/pull/42
```

## Why another PR tool

The GitHub web UI and mobile app are fine for browsing. They are poor at
answering the one question that matters when you are contributing: *did anything
change on my open PRs since I last looked?* Notifications are noisy, and the
API has no "what changed" endpoint, so this keeps a small local state file and
computes that diff itself.

It also avoids the usual friction of API tools: **there is no token to
configure.** Everything goes through the `gh` CLI you already have.

## Requirements

- Python 3.9 or newer
- [`gh`](https://cli.github.com) installed and authenticated (`gh auth login`)

Check both at once:

```bash
gh-pr-tracker whoami
```

## Install

From a clone:

```bash
git clone https://github.com/alorentiar/gh-pr-tracker
cd gh-pr-tracker
python -m venv .venv && . .venv/bin/activate
pip install -e .
```

Or run it without installing, straight from source:

```bash
python -m gh_pr_tracker poll
```

## Commands

| Command | What it does |
| --- | --- |
| `poll` | Fetch, print changes, save new state. Silent when nothing changed. |
| `snapshot` | Record current state without printing. Use once before your first `poll`. |
| `list` | Show what is currently tracked, from the local state file. |
| `forget <key>` | Stop tracking one PR, or `--all` to clear everything. |
| `threshold` | Exit non-zero when a condition holds. For CI or scripts. |
| `whoami` | Print the authenticated login, or explain what is missing. |

Global options: `--state PATH`, `--author LOGIN`, `--version`.

Useful flags:

- `poll --json` — machine readable output
- `poll --dry-run` — report changes but keep the old state
- `poll --all` — also list PRs that did not change
- `threshold --fail-on-ci` — exit 3 when any tracked PR has failing checks
- `threshold --fail-on-review` — exit 3 when changes were requested

## First run

The first `poll` reports every open PR as new, because there is nothing to
compare against. If you would rather start quiet:

```bash
gh-pr-tracker snapshot   # adopt current state
gh-pr-tracker poll       # now only real changes appear
```

## Running on a schedule

The quiet-when-unchanged behaviour is the point. Cron entry, every 30 minutes:

```cron
*/30 * * * * gh-pr-tracker poll >> ~/pr-changes.log 2>&1
```

Exit codes are stable, so a wrapper can decide whether to notify:

| Code | Meaning |
| --- | --- |
| 0 | No changes, or `threshold` found nothing |
| 1 | Error (bad state file, unexpected gh failure) |
| 2 | Not authenticated, or `gh` missing |
| 3 | `threshold` condition met |

A simple notify-when-changed shell loop:

```bash
out=$(gh-pr-tracker poll)
[ -n "$out" ] && notify-send "PR updates" "$out"
```

## What is tracked

For each open PR, these fields are compared between runs:

`state`, `merged`, `draft`, `review_decision`, `mergeable_state`, `comments`,
`reviews`, `last_actor`, `checks_failed`, `checks_pending`, `checks_passed`.

A PR that disappears from the open list (merged or closed) is reported once and
then dropped from the state file.

## State file

Default location:

```
$XDG_STATE_HOME/gh-pr-tracker/state.json     # or ~/.local/state/...
```

Override with `--state PATH` or the `GH_PR_TRACKER_STATE` environment variable.
Writes are atomic, and a corrupt or missing file is treated as empty rather than
crashing.

## Library use

The internals are importable if you want the diffing without the CLI:

```python
from gh_pr_tracker import GitHub, State, Tracker

tracker = Tracker(github=GitHub(), state=State())
for change in tracker.poll():
    print(change.describe())          # one line
    print("\n".join(change.multiline()))  # readable block
```

`Tracker.diff(prs)` compares against stored state without writing, and
`Tracker.snapshot_only()` records without reporting.

## Development

```bash
pip install -e ".[dev]"
pytest              # ~25 tests, no network needed
ruff check .
```

The tests fake the GitHub client, so they run offline and finish in under a
second.

## Limitations

- Tracks PRs by author search, capped at 100 per run.
- Review decision and mergeable state come from the REST PR endpoint; the
  GraphQL equivalent is not used.
- No notifications of its own. It reports to stdout and lets you route that
  however you like, which is deliberate.

## License

MIT. See [LICENSE](LICENSE).
