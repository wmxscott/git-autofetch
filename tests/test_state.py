from pathlib import Path

import pytest

from git_autofetch.state import SNOOZE_BACKOFF, Lock, LockHeldError, State


def test_due_follows_the_interval(state, clock):
    assert state.due(300)
    state.mark_run()
    assert not state.due(300)
    assert state.seconds_until_due(300) == 300
    clock.advance(299)
    assert not state.due(300)
    clock.advance(1)
    assert state.due(300)


def test_snooze_backs_off_and_caps(state, clock):
    delays = [state.bump_snooze("git@github.com", "timed out") for _ in range(6)]
    assert delays == [60, 120, 300, 600, 600, 600]
    assert delays[:4] == list(SNOOZE_BACKOFF)


def test_snooze_expires(state, clock):
    state.bump_snooze("h", "timed out")
    assert state.snooze("h")
    clock.advance(59)
    assert state.snooze("h")
    clock.advance(1)
    assert state.snooze("h") is None


def test_snooze_is_per_host(state):
    state.bump_snooze("a", "timed out")
    assert state.snooze("a")
    assert state.snooze("b") is None


def test_clearing_resets_the_back_off(state):
    state.bump_snooze("h", "x")
    state.bump_snooze("h", "x")
    assert state.clear_snooze("h")
    assert not state.clear_snooze("h")
    assert state.bump_snooze("h", "x") == 60


def test_clear_all(state):
    state.bump_snooze("a", "x")
    state.bump_snooze("b", "x")
    state.clear_all_snoozes()
    assert state.snooze("a") is None
    assert state.snooze("b") is None


def test_round_trip(tmp_path: Path, clock):
    state = State(tmp_path / "s", clock)
    state.mark_run()
    state.bump_snooze("h", "x")
    state.record_cycle({"hosts": [], "results": []})
    state.save()

    again = State(tmp_path / "s", clock)
    assert again.last_run == clock.now
    assert again.snooze("h")
    assert again.last_cycle == {"hosts": [], "results": []}


def test_corrupt_state_starts_fresh(tmp_path: Path):
    (tmp_path / "state.json").write_text("{not json")
    assert State(tmp_path).data == {}
    (tmp_path / "state.json").write_text("[1, 2]")
    assert State(tmp_path).data == {}


def test_lock_is_exclusive(tmp_path: Path):
    path = tmp_path / "lock"
    with Lock(path), pytest.raises(LockHeldError), Lock(path):
        pass
    with Lock(path):
        pass
