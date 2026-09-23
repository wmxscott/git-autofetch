import logging
from pathlib import Path

import pytest
from conftest import FakeSystem, make_repo

from git_autofetch.config import Config, RepoEntry
from git_autofetch.cycle import run_cycle
from git_autofetch.repos import SshTarget

GITHUB = "git@github.com:o/{}.git"
GITLAB = "git@gitlab.com:o/{}.git"
GH = SshTarget("github.com", "git")
GL = SshTarget("gitlab.com", "git")


@pytest.fixture
def root(tmp_path: Path) -> Path:
    return (tmp_path / "src").resolve()


def setup(root: Path, **repos: list[str]) -> tuple[Config, FakeSystem]:
    """A config watching `root` and a fake system where each named repo has these remote URLs."""
    system = FakeSystem()
    for name, urls in repos.items():
        system.add(make_repo(root / name), *(url.format(name) for url in urls))
    config = Config(hardware_key="0x1050", repos=(RepoEntry(root, recursive=True),))
    return config, system


def statuses(report) -> dict[str, str]:
    return {Path(r.repo).name: r.status for r in report.results}


def test_one_canary_per_host_then_everything_fetches(root, state):
    config, system = setup(root, a=[GITHUB], b=[GITHUB], c=[GITLAB])
    report = run_cycle(config, system, state)

    assert [h.target for h in report.hosts] == ["git@github.com", "git@gitlab.com"]
    assert all(h.ready for h in report.hosts)
    assert system.called("canary") == [("canary", "git@github.com"), ("canary", "git@gitlab.com")]
    assert sorted(c[1] for c in system.called("fetch")) == ["a", "b", "c"]
    assert statuses(report) == {"a": "ok", "b": "ok", "c": "ok"}


def test_repos_without_ssh_remotes_need_no_canary(root, state):
    config, system = setup(root, web=["https://example.com/{}.git"], local=["/srv/{}.git"])
    report = run_cycle(config, system, state)

    assert report.hosts == []
    assert system.called("canary") == []
    assert statuses(report) == {"local": "ok", "web": "ok"}


def test_warm_connection_skips_key_and_idle_checks(root, state):
    config, system = setup(root, a=[GITHUB])
    system.alive.add(GH)
    system.key_present = False
    system.idle = 10_000

    report = run_cycle(config, system, state)

    assert report.hosts[0].ready
    assert system.called("key") == []
    assert system.called("idle") == []


def test_cold_connection_waits_for_the_hardware_key(root, state):
    config, system = setup(root, a=[GITHUB], b=["https://example.com/{}.git"])
    system.key_present = False

    report = run_cycle(config, system, state)

    host = report.hosts[0]
    assert not host.ready
    assert host.reason == "hardware key not plugged in"
    assert system.called("canary") == []
    assert statuses(report) == {"a": "skipped", "b": "ok"}
    assert "hardware key" in report.results[0].detail


def test_no_hardware_key_configured_means_no_key_check(root, state):
    config, system = setup(root, a=[GITHUB])
    config = Config(repos=config.repos)
    system.key_present = False

    assert run_cycle(config, system, state).hosts[0].ready
    assert system.called("key") == []


def test_cold_connection_waits_for_the_user(root, state):
    config, system = setup(root, a=[GITHUB])
    system.idle = config.idle_threshold + 1

    report = run_cycle(config, system, state)

    assert report.hosts[0].reason == "nobody at the computer to log in"
    assert system.called("canary") == []


def test_unknown_idle_time_does_not_block(root, state):
    config, system = setup(root, a=[GITHUB])
    system.idle = None
    assert run_cycle(config, system, state).hosts[0].ready


def test_unreachable_host_is_skipped_without_touching_ssh(root, state):
    config, system = setup(root, a=[GITHUB], c=[GITLAB])
    system.unreachable.add("github.com")

    report = run_cycle(config, system, state)

    assert report.hosts[0].reason == "unreachable"
    assert ("check", "git@github.com") not in system.calls
    assert statuses(report) == {"a": "skipped", "c": "ok"}


def test_proxied_hosts_are_not_probed(root, state):
    config, system = setup(root, a=[GITHUB])
    system.proxied.add("github.com")
    system.unreachable.add("github.com")

    assert run_cycle(config, system, state).hosts[0].ready
    assert system.called("reachable") == []


def test_one_blocked_host_only_holds_back_its_own_repos(root, state):
    config, system = setup(root, a=[GITHUB], b=[GITLAB], both=[GITHUB, GITLAB])
    system.canary_status[GL] = "failed"

    report = run_cycle(config, system, state)

    assert statuses(report) == {"a": "ok", "b": "skipped", "both": "skipped"}


def test_stale_connection_is_reset(root, state):
    config, system = setup(root, a=[GITHUB])
    system.alive.add(GH)
    system.canary_status[GH] = "timed_out"

    report = run_cycle(config, system, state)

    assert report.hosts[0].reason == "stale connection reset"
    assert ("exit", "git@github.com") in system.calls
    assert state.snooze("git@github.com") is None


def test_unfinished_login_backs_off(root, state, clock):
    config, system = setup(root, a=[GITHUB])
    system.canary_status[GH] = "timed_out"

    first = run_cycle(config, system, state)
    assert first.hosts[0].reason == "login timed out, retrying in 60s"

    clock.advance(30)
    second = run_cycle(config, system, state)
    assert second.hosts[0].reason == "waiting after an unfinished login"
    assert len(system.called("canary")) == 1

    clock.advance(30)
    third = run_cycle(config, system, state)
    assert third.hosts[0].reason == "login timed out, retrying in 120s"
    assert len(system.called("canary")) == 2


def test_a_warm_connection_ends_the_back_off(root, state):
    config, system = setup(root, a=[GITHUB])
    state.bump_snooze("git@github.com", "login timed out")
    system.alive.add(GH)

    report = run_cycle(config, system, state)

    assert report.hosts[0].ready
    assert state.snooze("git@github.com") is None


def test_failed_canary_reports_why(root, state):
    config, system = setup(root, a=[GITHUB])
    system.canary_status[GH] = "failed"

    report = run_cycle(config, system, state)

    assert report.hosts[0].reason == "canary fetch failed: failed detail"
    assert state.snooze("git@github.com") is None


def test_fetch_failures_are_reported_per_repo(root, state):
    config, system = setup(root, a=[GITHUB], b=[GITHUB])
    system.fetch_status[root / "b"] = "failed"

    assert statuses(run_cycle(config, system, state)) == {"a": "ok", "b": "failed"}


def test_cycle_is_recorded(root, state, clock):
    config, system = setup(root, a=[GITHUB])
    run_cycle(config, system, state)

    assert state.last_run == clock.now
    assert state.last_cycle["hosts"][0]["target"] == "git@github.com"
    assert state.last_cycle["results"][0]["status"] == "ok"


def test_quiet_when_nothing_changes(root, state, caplog):
    config, system = setup(root, a=[GITHUB], b=[GITHUB])
    with caplog.at_level(logging.INFO):
        run_cycle(config, system, state)
    assert "git@github.com: ready" in caplog.text
    assert "watching 2 repositories across 1 ssh hosts" in caplog.text

    caplog.clear()
    with caplog.at_level(logging.INFO):
        run_cycle(config, system, state)
    assert caplog.records == []


def test_logs_failures_once_and_recovery(root, state, caplog):
    config, system = setup(root, a=[GITHUB])
    run_cycle(config, system, state)

    system.fetch_status[root / "a"] = "failed"
    with caplog.at_level(logging.INFO):
        run_cycle(config, system, state)
        run_cycle(config, system, state)
    assert caplog.text.count("failed failed detail") == 1

    caplog.clear()
    del system.fetch_status[root / "a"]
    with caplog.at_level(logging.INFO):
        run_cycle(config, system, state)
    assert "fetching again" in caplog.text
