"""Tests for the diffing logic, which is where the real behaviour lives.

The GitHub client is faked, so these run offline and in milliseconds.
"""

from __future__ import annotations

import json
from pathlib import Path

from gh_pr_tracker.github import GitHub
from gh_pr_tracker.state import State
from gh_pr_tracker.tracker import PullRequest, Tracker


class FakeGitHub(GitHub):
    """GitHub client that returns canned data instead of calling gh."""

    def __init__(self, prs, detail=None, reviews=None, comments=None, checks=None):
        super().__init__()
        self._prs = prs
        self._detail = detail or {}
        self._reviews = reviews or []
        self._comments = comments or []
        self._checks = checks or []
        self.calls = 0

    def viewer(self):
        return "tester"

    def open_prs(self, author, limit=100):
        return self._prs

    def pr_detail(self, repo, number):
        return self._detail.get((repo, number), {})

    def pr_reviews(self, repo, number):
        return self._reviews

    def pr_comments(self, repo, number):
        return self._comments

    def pr_checks(self, repo, number):
        self.calls += 1
        return self._checks


def search_item(repo="acme/widget", number=7, title="Fix the thing"):
    return {
        "number": number,
        "title": title,
        "html_url": f"https://github.com/{repo}/pull/{number}",
        "repository_url": f"https://api.github.com/repos/{repo}",
        "state": "open",
    }


def make_pr(**kwargs) -> PullRequest:
    base: dict = {
        "repo": "acme/widget",
        "number": 7,
        "title": "Fix the thing",
        "url": "https://github.com/acme/widget/pull/7",
        "state": "open",
    }
    base.update(kwargs)
    return PullRequest(**base)


def make_tracker(tmp_path: Path, gh: GitHub, author="tester") -> Tracker:
    return Tracker(github=gh, state=State(tmp_path / "state.json"), author=author)


# -- state ----------------------------------------------------------------


def test_state_roundtrip(tmp_path):
    path = tmp_path / "s.json"
    st = State(path)
    st.set("a/b#1", {"state": "open"})
    st.save()

    again = State(path)
    assert again.get("a/b#1") == {"state": "open"}


def test_state_missing_file_is_empty(tmp_path):
    st = State(tmp_path / "nope.json")
    assert st.as_dict() == {}
    assert st.get("anything") is None


def test_state_corrupt_file_recovers(tmp_path):
    path = tmp_path / "bad.json"
    path.write_text("{not json", encoding="utf-8")
    st = State(path)
    assert st.as_dict() == {}


def test_drop_missing_removes_only_absent():
    st = State(":memory:")
    st.set("keep#1", {"state": "open"})
    st.set("gone#2", {"state": "open"})
    removed = st.drop_missing({"keep#1"})
    assert removed == ["gone#2"]
    assert st.get("gone#2") is None


def test_save_is_atomic_leaves_no_temp_files(tmp_path):
    st = State(tmp_path / "s.json")
    st.set("a#1", {"state": "open"})
    st.save()
    leftovers = [p.name for p in tmp_path.iterdir() if p.name.startswith(".state-")]
    assert leftovers == []


# -- normalisation --------------------------------------------------------


def test_enrich_normalises_detail(tmp_path):
    item = search_item()
    gh = FakeGitHub(
        [item],
        detail={
            ("acme/widget", 7): {
                "title": "Fix the thing",
                "html_url": "https://github.com/acme/widget/pull/7",
                "state": "open",
                "merged": False,
                "draft": False,
                "review_decision": "REVIEW_REQUIRED",
                "mergeable_state": "blocked",
            }
        },
        reviews=[{"user": {"login": "reviewer"}, "submitted_at": "2026-01-01T00:00:00Z"}],
        comments=[{"user": {"login": "commenter"}, "updated_at": "2026-01-02T00:00:00Z"}],
        checks=[
            {"status": "completed", "conclusion": "success"},
            {"status": "completed", "conclusion": "failure"},
            {"status": "in_progress", "conclusion": None},
        ],
    )
    tracker = make_tracker(tmp_path, gh)
    prs = tracker.fetch()
    assert len(prs) == 1
    pr = prs[0]
    assert pr.repo == "acme/widget"
    assert pr.number == 7
    assert pr.review_decision == "review_required"
    assert pr.mergeable_state == "blocked"
    assert pr.reviews == 1
    assert pr.comments == 1
    assert pr.last_actor == "commenter"
    assert (pr.checks_passed, pr.checks_failed, pr.checks_pending) == (1, 1, 1)


def test_enrich_handles_missing_detail(tmp_path):
    gh = FakeGitHub([search_item()], detail={})
    tracker = make_tracker(tmp_path, gh)
    pr = tracker.fetch()[0]
    assert pr.state == "open"
    assert pr.title == "Fix the thing"
    assert pr.checks_failed == 0


# -- diffing --------------------------------------------------------------


def test_first_sighting_is_reported_as_new(tmp_path):
    gh = FakeGitHub([search_item()], detail={("acme/widget", 7): {"state": "open"}})
    tracker = make_tracker(tmp_path, gh)
    changes = tracker.poll()
    assert len(changes) == 1
    assert changes[0].fields == {"new": ("", "tracked")}


def test_second_poll_without_change_is_silent(tmp_path):
    gh = FakeGitHub([search_item()], detail={("acme/widget", 7): {"state": "open"}})
    tracker = make_tracker(tmp_path, gh)
    tracker.poll()
    assert tracker.poll() == []


def test_review_decision_change_is_detected(tmp_path):
    state_path = tmp_path / "state.json"
    item = search_item()

    first = FakeGitHub([item], detail={("acme/widget", 7): {"state": "open"}})
    Tracker(github=first, state=State(state_path), author="tester").poll()

    second = FakeGitHub(
        [item],
        detail={("acme/widget", 7): {"state": "open", "review_decision": "APPROVED"}},
    )
    changes = Tracker(
        github=second, state=State(state_path), author="tester"
    ).poll()
    assert len(changes) == 1
    assert "review_decision" in changes[0].fields
    assert changes[0].fields["review_decision"] == ("", "approved")


def test_failing_checks_change_is_detected(tmp_path):
    state_path = tmp_path / "state.json"
    item = search_item()

    ok = FakeGitHub(
        [item],
        detail={("acme/widget", 7): {"state": "open"}},
        checks=[{"status": "completed", "conclusion": "success"}],
    )
    Tracker(github=ok, state=State(state_path), author="tester").poll()

    bad = FakeGitHub(
        [item],
        detail={("acme/widget", 7): {"state": "open"}},
        checks=[{"status": "completed", "conclusion": "failure"}],
    )
    changes = Tracker(github=bad, state=State(state_path), author="tester").poll()
    assert changes and "checks_failed" in changes[0].fields


def test_closed_pr_is_forgotten(tmp_path):
    state_path = tmp_path / "state.json"
    st = State(state_path)
    st.set("acme/widget#7", {"state": "open"})
    st.save()

    empty = FakeGitHub([])
    tracker = Tracker(github=empty, state=State(state_path), author="tester")
    changes = tracker.poll()
    assert changes == []
    assert State(state_path).get("acme/widget#7") is None


def test_dry_run_does_not_persist(tmp_path):
    state_path = tmp_path / "state.json"
    gh = FakeGitHub([search_item()], detail={("acme/widget", 7): {"state": "open"}})
    tracker = Tracker(github=gh, state=State(state_path), author="tester")
    changes = tracker.poll(persist=False)
    assert len(changes) == 1
    assert not state_path.exists()


# -- rendering ------------------------------------------------------------


def test_change_multiline_contains_key_and_url():
    gh = FakeGitHub([])
    tracker = make_tracker(Path("/tmp"), gh)
    change = tracker.diff([make_pr(review_decision="approved")])[0]
    text = "\n".join(change.multiline())
    assert "acme/widget#7" in text
    assert "https://github.com/acme/widget/pull/7" in text


def test_change_describe_is_single_line():
    gh = FakeGitHub([])
    tracker = make_tracker(Path("/tmp"), gh)
    change = tracker.diff([make_pr()])[0]
    assert "\n" not in change.describe()
    assert change.describe().startswith("acme/widget#7")


def test_snapshot_fields_are_serialisable():
    snap = make_pr().snapshot()
    assert json.loads(json.dumps(snap))["state"] == "open"


# -- cli ------------------------------------------------------------------


def test_cli_list_on_empty_state(tmp_path, capsys):
    from gh_pr_tracker.cli import main

    code = main(["--state", str(tmp_path / "s.json"), "list"])
    out = capsys.readouterr().out
    assert code == 0
    assert "nothing tracked" in out


def test_cli_list_json(tmp_path, capsys):
    from gh_pr_tracker.cli import main

    path = tmp_path / "s.json"
    st = State(path)
    st.set("a/b#1", {"state": "open", "review_decision": "approved"})
    st.save()

    code = main(["--state", str(path), "list", "--json"])
    payload = json.loads(capsys.readouterr().out)
    assert code == 0
    assert payload["a/b#1"]["review_decision"] == "approved"


def test_cli_forget_requires_known_key(tmp_path, capsys):
    from gh_pr_tracker.cli import main

    code = main(["--state", str(tmp_path / "s.json"), "forget", "x/y#1"])
    assert code == 1
    assert "not tracked" in capsys.readouterr().err


def test_cli_threshold_pass(tmp_path, capsys):
    from gh_pr_tracker.cli import main

    path = tmp_path / "s.json"
    st = State(path)
    st.set("a/b#1", {"state": "open", "checks_failed": 0})
    st.save()
    assert main(["--state", str(path), "threshold", "--fail-on-ci"]) == 0


def test_cli_threshold_blocks_on_ci(tmp_path, capsys):
    from gh_pr_tracker.cli import main

    path = tmp_path / "s.json"
    st = State(path)
    st.set("a/b#1", {"state": "open", "checks_failed": 2})
    st.save()
    code = main(["--state", str(path), "threshold", "--fail-on-ci"])
    assert code == 3
    assert "failing check" in capsys.readouterr().out


def test_cli_version(capsys):
    from gh_pr_tracker.cli import main

    try:
        main(["--version"])
    except SystemExit as exc:
        assert exc.code == 0
