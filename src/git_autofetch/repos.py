"""Finding repositories and working out which ssh hosts their remotes use."""

from __future__ import annotations

import logging
import re
from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit

from git_autofetch.config import RepoEntry

log = logging.getLogger(__name__)


@dataclass(frozen=True, order=True)
class SshTarget:
    host: str
    user: str | None = None
    port: int | None = None

    @property
    def destination(self) -> str:
        return f"{self.user}@{self.host}" if self.user else self.host

    def ssh_args(self) -> list[str]:
        return (["-p", str(self.port)] if self.port else []) + [self.destination]

    def __str__(self) -> str:
        return f"{self.destination}:{self.port}" if self.port else self.destination


@dataclass(frozen=True)
class Remote:
    name: str
    url: str
    target: SshTarget | None


_SSH_SCHEMES = {"ssh", "git+ssh", "ssh+git"}
# git's scp-like syntax: `[user@]host:path`, a colon before any slash.
_SCP_LIKE = re.compile(r"^(?:(?P<user>[^@/:]+)@)?(?P<host>[^@/:]+):(?!//)")


def parse_ssh_url(url: str) -> SshTarget | None:
    """The ssh host a remote URL points at, or None for https, file and local remotes."""
    url = url.strip()
    if "://" in url:
        parts = urlsplit(url)
        if parts.scheme.lower() not in _SSH_SCHEMES or not parts.hostname:
            return None
        try:
            port = parts.port
        except ValueError:
            return None
        return SshTarget(parts.hostname, parts.username, port)
    match = _SCP_LIKE.match(url)
    if not match:
        return None
    return SshTarget(match["host"].lower(), match["user"])


def parse_remotes(output: str) -> list[Remote]:
    """Parse `git remote -v` output, keeping each remote's fetch URL."""
    remotes = []
    for line in output.splitlines():
        name, _, rest = line.partition("\t")
        url, _, kind = rest.rpartition(" ")
        if kind == "(fetch)" and name and url:
            remotes.append(Remote(name, url, parse_ssh_url(url)))
    return remotes


def is_repo(path: Path) -> bool:
    # A file for worktrees and submodules, a directory otherwise.
    return (path / ".git").exists()


def _walk(root: Path, max_depth: int) -> Iterator[Path]:
    stack = [(root, 0)]
    while stack:
        current, depth = stack.pop()
        if is_repo(current):
            yield current
            continue
        if depth >= max_depth:
            continue
        try:
            children = sorted(current.iterdir())
        except OSError:
            continue
        stack.extend(
            (child, depth + 1) for child in children if child.is_dir() and not child.is_symlink()
        )


def discover(entries: Iterable[RepoEntry]) -> list[Path]:
    """Every repository the config names, sorted and without duplicates."""
    found: set[Path] = set()
    for entry in entries:
        path = entry.path.expanduser().resolve()
        if not path.is_dir():
            log.warning("repo path does not exist: %s", path)
        elif is_repo(path):
            found.add(path)
        elif entry.recursive:
            found.update(_walk(path, entry.max_depth))
        else:
            log.warning("not a git repository (set recursive = true to search it): %s", path)
    return sorted(found)
