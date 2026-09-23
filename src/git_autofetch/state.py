"""What git-autofetch remembers between runs, and the lock that keeps runs apart."""

from __future__ import annotations

import fcntl
import json
import os
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

# Back-off after a login times out with nobody there to finish it: 1, 2, 5, 10 minutes.
SNOOZE_BACKOFF = (60, 120, 300, 600)


class State:
    def __init__(self, directory: Path, clock: Callable[[], float] = time.time):
        self.directory = directory
        self.path = directory / "state.json"
        self.clock = clock
        self.data = self._read()

    def _read(self) -> dict[str, Any]:
        try:
            data = json.loads(self.path.read_text())
        except (OSError, ValueError):
            return {}
        return data if isinstance(data, dict) else {}

    def reload(self) -> None:
        self.data = self._read()

    def save(self) -> None:
        self.directory.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(".tmp")
        temporary.write_text(json.dumps(self.data, indent=2, sort_keys=True) + "\n")
        os.replace(temporary, self.path)

    # -- schedule ---------------------------------------------------------

    @property
    def last_run(self) -> float:
        return float(self.data.get("last_run", 0))

    def seconds_until_due(self, interval: int) -> float:
        return max(0.0, self.last_run + interval - self.clock())

    def due(self, interval: int) -> bool:
        return self.seconds_until_due(interval) == 0

    def mark_run(self) -> None:
        self.data["last_run"] = self.clock()

    @property
    def last_resume(self) -> float:
        return float(self.data.get("last_resume", 0))

    def mark_resume(self) -> None:
        self.data["last_resume"] = self.clock()

    # -- snooze, per ssh host ---------------------------------------------

    def _snoozes(self) -> dict[str, Any]:
        return self.data.setdefault("snooze", {})

    def snooze(self, target: str) -> dict[str, Any] | None:
        entry = self._snoozes().get(target)
        if entry and self.clock() < float(entry.get("until", 0)):
            return entry
        return None

    def bump_snooze(self, target: str, reason: str) -> int:
        """Back off one step further for this host. Returns the delay in seconds."""
        entry = self._snoozes().get(target) or {}
        level = int(entry.get("level", 0))
        delay = SNOOZE_BACKOFF[min(level, len(SNOOZE_BACKOFF) - 1)]
        self._snoozes()[target] = {
            "until": self.clock() + delay,
            "level": level + 1,
            "reason": reason,
        }
        return delay

    def clear_snooze(self, target: str) -> bool:
        return self._snoozes().pop(target, None) is not None

    def clear_all_snoozes(self) -> None:
        self.data["snooze"] = {}

    # -- the last cycle, for status and for logging only what changed ------

    @property
    def last_cycle(self) -> dict[str, Any]:
        return self.data.get("last_cycle", {})

    def record_cycle(self, report: dict[str, Any]) -> None:
        self.data["last_cycle"] = report


class LockHeldError(Exception):
    pass


class Lock:
    """An exclusive, non-blocking lock so two runs never fetch at the same time."""

    def __init__(self, path: Path):
        self.path = path
        self.file: Any = None

    def __enter__(self) -> Lock:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.file = self.path.open("w")
        try:
            fcntl.flock(self.file, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            self.file.close()
            raise LockHeldError(str(self.path)) from None
        self.file.write(f"{os.getpid()}\n")
        self.file.flush()
        return self

    def __exit__(self, *exc: object) -> None:
        fcntl.flock(self.file, fcntl.LOCK_UN)
        self.file.close()
