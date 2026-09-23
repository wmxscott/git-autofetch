"""One fetch cycle: check each ssh host, then fetch every repository that can go ahead."""

from __future__ import annotations

import concurrent.futures
import logging
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Protocol

from git_autofetch.config import Config
from git_autofetch.repos import Remote, SshTarget, discover
from git_autofetch.ssh import Resolved
from git_autofetch.state import State
from git_autofetch.system import FetchResult

log = logging.getLogger(__name__)


class SystemLike(Protocol):
    def remotes(self, repo: Path) -> list[Remote]: ...
    def resolve(self, target: SshTarget) -> Resolved | None: ...
    def reachable(self, hostname: str, port: int, timeout: float = 3.0) -> bool: ...
    def master_alive(self, target: SshTarget) -> bool: ...
    def master_exit(self, target: SshTarget) -> None: ...
    def hardware_key_present(self, spec: str) -> bool: ...
    def idle_seconds(self) -> float | None: ...
    def fetch(
        self, repo: Path, *, timeout: int, canary: bool = False, remote: str | None = None
    ) -> FetchResult: ...


@dataclass(frozen=True)
class HostStatus:
    target: str
    ready: bool
    reason: str
    connection: str  # warm, down or unknown


@dataclass
class CycleReport:
    started: float
    duration: float
    hosts: list[HostStatus]
    results: list[FetchResult]

    def to_dict(self) -> dict[str, Any]:
        return {
            "started": self.started,
            "duration": self.duration,
            "hosts": [asdict(host) for host in self.hosts],
            "results": [asdict(result) for result in self.results],
        }


def run_cycle(config: Config, system: SystemLike, state: State) -> CycleReport:
    started = state.clock()
    clock_start = time.monotonic()
    state.mark_run()

    repos = discover(config.repos)
    remotes = {repo: system.remotes(repo) for repo in repos}

    # The first repository using each host (and the remote that uses it) does its canary fetch.
    canaries: dict[SshTarget, tuple[Path, str]] = {}
    for repo in repos:
        for remote in remotes[repo]:
            if remote.target and remote.target not in canaries:
                canaries[remote.target] = (repo, remote.name)

    hosts = {
        target: _check_host(target, repo, remote, config, system, state)
        for target, (repo, remote) in sorted(canaries.items())
    }

    results: list[FetchResult] = []
    batch: list[Path] = []
    for repo in repos:
        blocked = [hosts[r.target] for r in remotes[repo] if r.target and not hosts[r.target].ready]
        if blocked:
            first = blocked[0]
            results.append(
                FetchResult(str(repo), "skipped", 0.0, f"{first.target}: {first.reason}")
            )
        else:
            batch.append(repo)
    results += _fetch_all(batch, config, system)
    results.sort(key=lambda result: result.repo)

    report = CycleReport(
        started, round(time.monotonic() - clock_start, 2), list(hosts.values()), results
    )
    _log_changes(state.last_cycle, report)
    state.record_cycle(report.to_dict())
    return report


def _check_host(
    target: SshTarget,
    repo: Path,
    remote: str,
    config: Config,
    system: SystemLike,
    state: State,
) -> HostStatus:
    name = str(target)

    resolved = system.resolve(target)
    if resolved and not resolved.proxied and not system.reachable(resolved.hostname, resolved.port):
        return HostStatus(name, False, "unreachable", "unknown")

    alive = system.master_alive(target)
    connection = "warm" if alive else "down"

    if state.snooze(name):
        if not alive:
            return HostStatus(name, False, "waiting after an unfinished login", connection)
        state.clear_snooze(name)

    # A warm connection needs no login, so the key and the user needn't be there.
    if not alive:
        if config.hardware_key and not system.hardware_key_present(config.hardware_key):
            return HostStatus(name, False, "hardware key not plugged in", connection)
        idle = system.idle_seconds()
        if idle is not None and idle > config.idle_threshold:
            return HostStatus(name, False, "nobody at the computer to log in", connection)

    result = system.fetch(repo, timeout=config.connect_timeout, canary=True, remote=remote)
    if result.status == "ok":
        state.clear_snooze(name)
        return HostStatus(name, True, "ready", "warm")
    if result.status == "timed_out":
        if alive:
            # The connection looked alive but didn't answer: drop it so the next cycle starts fresh.
            system.master_exit(target)
            return HostStatus(name, False, "stale connection reset", "down")
        delay = state.bump_snooze(name, "login timed out")
        return HostStatus(name, False, f"login timed out, retrying in {delay}s", "down")
    return HostStatus(name, False, f"canary fetch failed: {result.detail}", connection)


def _fetch_all(repos: list[Path], config: Config, system: SystemLike) -> list[FetchResult]:
    if not repos:
        return []
    with concurrent.futures.ThreadPoolExecutor(max_workers=config.parallel) as pool:
        futures = {
            pool.submit(system.fetch, repo, timeout=config.fetch_timeout): repo for repo in repos
        }
        results = []
        for future in concurrent.futures.as_completed(futures):
            try:
                results.append(future.result())
            except Exception as error:
                results.append(FetchResult(str(futures[future]), "error", 0.0, str(error)))
    return results


def _log_changes(previous: dict[str, Any], report: CycleReport) -> None:
    """Log what changed since the last cycle, so a quiet cycle logs nothing."""
    before_hosts = {h["target"]: h for h in previous.get("hosts", [])}
    for host in report.hosts:
        before = before_hosts.get(host.target)
        if (
            before is None
            or before.get("ready") != host.ready
            or before.get("reason") != host.reason
        ):
            if host.ready:
                log.info("%s: ready", host.target)
            else:
                log.warning("%s: %s", host.target, host.reason)

    before_results = {r["repo"]: r for r in previous.get("results", [])}
    for result in report.results:
        before = before_results.get(result.repo)
        was = before.get("status") if before else None
        if result.status == was or result.status == "skipped":
            continue
        if result.status == "ok":
            if was is not None:
                log.info("%s: fetching again", result.repo)
        else:
            log.warning("%s: %s %s", result.repo, result.status.replace("_", " "), result.detail)

    if len(before_results) != len(report.results):
        log.info(
            "watching %d repositories across %d ssh hosts", len(report.results), len(report.hosts)
        )

    counts: dict[str, int] = {}
    for result in report.results:
        counts[result.status] = counts.get(result.status, 0) + 1
    log.debug("cycle done in %.2fs: %s", report.duration, counts)
