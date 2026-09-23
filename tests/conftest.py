from __future__ import annotations

import os
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

import pytest

from git_autofetch.repos import Remote, SshTarget
from git_autofetch.ssh import Resolved
from git_autofetch.state import State
from git_autofetch.system import FetchResult

GIT_ISOLATION = {
    "GIT_CONFIG_GLOBAL": "/dev/null",
    "GIT_CONFIG_NOSYSTEM": "1",
    "GIT_AUTHOR_NAME": "test",
    "GIT_AUTHOR_EMAIL": "test@example.com",
    "GIT_COMMITTER_NAME": "test",
    "GIT_COMMITTER_EMAIL": "test@example.com",
}


@pytest.fixture(autouse=True)
def isolated_git(monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep the developer's own git config (signing, URL rewrites, hooks) out of the tests."""
    for key, value in GIT_ISOLATION.items():
        monkeypatch.setenv(key, value)


def git(*args: str, cwd: Path | None = None) -> str:
    result = subprocess.run(
        ["git", *args], cwd=cwd, capture_output=True, text=True, check=True, env=os.environ
    )
    return result.stdout.strip()


@dataclass
class Origin:
    bare: Path
    work: Path

    def push_branch(self, name: str) -> str:
        git("switch", "-q", "-c", name, cwd=self.work)
        git("commit", "-q", "--allow-empty", "-m", name, cwd=self.work)
        git("push", "-q", "origin", name, cwd=self.work)
        return git("rev-parse", "HEAD", cwd=self.work)

    def delete_branch(self, name: str) -> None:
        git("push", "-q", "origin", "--delete", name, cwd=self.work)

    def clone(self, path: Path) -> Path:
        git("clone", "-q", str(self.bare), str(path))
        return path


@pytest.fixture
def origin(tmp_path: Path) -> Origin:
    bare = tmp_path / "origin.git"
    git("init", "-q", "--bare", "-b", "main", str(bare))
    work = tmp_path / "work"
    git("clone", "-q", str(bare), str(work))
    git("commit", "-q", "--allow-empty", "-m", "initial", cwd=work)
    git("push", "-q", "origin", "HEAD:main", cwd=work)
    return Origin(bare, work)


class Clock:
    def __init__(self, now: float = 1_000_000.0):
        self.now = now

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


@pytest.fixture
def clock() -> Clock:
    return Clock()


@pytest.fixture
def state(tmp_path: Path, clock: Clock) -> State:
    return State(tmp_path / "state", clock)


def make_repo(path: Path) -> Path:
    (path / ".git").mkdir(parents=True)
    return path


@dataclass
class FakeSystem:
    """Stands in for git, ssh and ioreg. A successful canary warms its host's connection."""

    remotes_by_repo: dict[Path, list[Remote]] = field(default_factory=dict)
    alive: set[SshTarget] = field(default_factory=set)
    unreachable: set[str] = field(default_factory=set)
    proxied: set[str] = field(default_factory=set)
    key_present: bool = True
    idle: float | None = 0.0
    canary_status: dict[SshTarget, str] = field(default_factory=dict)
    fetch_status: dict[Path, str] = field(default_factory=dict)
    calls: list[tuple] = field(default_factory=list)

    def add(self, repo: Path, *urls: str) -> None:
        from git_autofetch.repos import parse_ssh_url

        self.remotes_by_repo[repo] = [
            Remote(f"r{i}", url, parse_ssh_url(url)) for i, url in enumerate(urls)
        ]

    def remotes(self, repo: Path) -> list[Remote]:
        return self.remotes_by_repo.get(repo, [])

    def resolve(self, target: SshTarget) -> Resolved | None:
        return Resolved(target.host, target.port or 22, target.host in self.proxied)

    def reachable(self, hostname: str, port: int, timeout: float = 3.0) -> bool:
        self.calls.append(("reachable", hostname))
        return hostname not in self.unreachable

    def master_alive(self, target: SshTarget) -> bool:
        self.calls.append(("check", str(target)))
        return target in self.alive

    def master_exit(self, target: SshTarget) -> None:
        self.calls.append(("exit", str(target)))
        self.alive.discard(target)

    def hardware_key_present(self, spec: str) -> bool:
        self.calls.append(("key", spec))
        return self.key_present

    def idle_seconds(self) -> float | None:
        self.calls.append(("idle",))
        return self.idle

    def fetch(
        self, repo: Path, *, timeout: int, canary: bool = False, remote: str | None = None
    ) -> FetchResult:
        if canary:
            target = next(r.target for r in self.remotes(repo) if r.name == remote)
            self.calls.append(("canary", str(target)))
            status = self.canary_status.get(target, "ok")
            if status == "ok":
                self.alive.add(target)
        else:
            self.calls.append(("fetch", repo.name))
            status = self.fetch_status.get(repo, "ok")
        detail = "" if status == "ok" else f"{status} detail"
        return FetchResult(str(repo), status, 0.1, detail)

    def called(self, kind: str) -> list[tuple]:
        return [call for call in self.calls if call[0] == kind]
