"""Local state so the tracker only reports what changed.

Stored as JSON next to the user's config directory (or wherever
GH_PR_TRACKER_STATE points). Writes are atomic: a crash mid-write leaves the
previous file intact rather than a truncated one.
"""

from __future__ import annotations

import contextlib
import json
import os
import tempfile
from pathlib import Path
from typing import Any

ENV_STATE = "GH_PR_TRACKER_STATE"


def default_state_path() -> Path:
    """State file location, honouring XDG and the env override."""
    override = os.environ.get(ENV_STATE)
    if override:
        return Path(override).expanduser()
    xdg = os.environ.get("XDG_STATE_HOME")
    base = Path(xdg).expanduser() if xdg else Path.home() / ".local" / "state"
    return base / "gh-pr-tracker" / "state.json"


class State:
    """Tiny persisted dict keyed by 'owner/repo#number'."""

    def __init__(self, path: Path | str | None = None) -> None:
        self.path = Path(path).expanduser() if path else default_state_path()
        self._data: dict[str, Any] = {}
        self.load()

    # -- persistence -------------------------------------------------------

    def load(self) -> dict[str, Any]:
        try:
            with self.path.open(encoding="utf-8") as fh:
                data = json.load(fh)
            self._data = data if isinstance(data, dict) else {}
        except (OSError, json.JSONDecodeError):
            self._data = {}
        return self._data

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp_name = tempfile.mkstemp(
            dir=str(self.path.parent), prefix=".state-", suffix=".tmp"
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                json.dump(self._data, fh, indent=2, sort_keys=True)
                fh.write("\n")
            os.replace(tmp_name, self.path)
        except BaseException:
            with contextlib.suppress(OSError):
                os.unlink(tmp_name)
            raise

    # -- mapping-ish -------------------------------------------------------

    def get(self, key: str) -> dict[str, Any] | None:
        value = self._data.get(key)
        return value if isinstance(value, dict) else None

    def set(self, key: str, value: dict[str, Any]) -> None:
        self._data[key] = value

    def drop_missing(self, keep: set[str]) -> list[str]:
        """Forget PRs that are no longer open. Returns the removed keys."""
        removed = [k for k in list(self._data) if k not in keep]
        for key in removed:
            del self._data[key]
        return removed

    def keys(self) -> list[str]:
        return list(self._data)

    def as_dict(self) -> dict[str, Any]:
        return dict(self._data)
