"""Everything that touches the outside world: git, ssh, the network, ioreg."""

from __future__ import annotations

import contextlib
import os
import socket
import subprocess
import time
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from git_autofetch import gates
from git_autofetch.config import Config
from git_autofetch.repos import Remote, SshTarget, parse_remotes
from git_autofetch.ssh import Resolved, SshOptions, parse_ssh_g

# launchd starts services with a bare PATH.
EXTRA_PATH = ("/opt/homebrew/bin", "/usr/local/bin", "/usr/bin", "/bin")


@dataclass(frozen=True)
class FetchResult:
    repo: str
    status: str  # ok, failed, timed_out, error or skipped
    duration: float = 0.0
    detail: str = ""


def build_env(config: Config, base: Mapping[str, str]) -> dict[str, str]:
    """The environment git and ssh run with."""
    env = dict(base)
    env["GIT_TERMINAL_PROMPT"] = "0"
    if config.ssh_auth_sock:
        env["SSH_AUTH_SOCK"] = str(Path(config.ssh_auth_sock).expanduser())
    if config.askpass:
        askpass = Path(config.askpass).expanduser()
        if os.access(askpass, os.X_OK):
            env["SSH_ASKPASS"] = str(askpass)
            env["SSH_ASKPASS_REQUIRE"] = "force"
    path = [p for p in env.get("PATH", "").split(":") if p]
    env["PATH"] = ":".join(path + [p for p in EXTRA_PATH if p not in path])
    return env


def _run(args: list[str], env: Mapping[str, str], timeout: float) -> subprocess.CompletedProcess:
    return subprocess.run(args, capture_output=True, text=True, timeout=timeout, env=env)


class System:
    def __init__(self, ssh: SshOptions, env: Mapping[str, str]):
        self.ssh = ssh
        self.env = dict(env)

    def remotes(self, repo: Path) -> list[Remote]:
        try:
            result = _run(["git", "-C", str(repo), "remote", "-v"], self.env, 10)
        except (subprocess.TimeoutExpired, OSError):
            return []
        return parse_remotes(result.stdout) if result.returncode == 0 else []

    def resolve(self, target: SshTarget) -> Resolved | None:
        try:
            result = _run(["ssh", "-G", *target.ssh_args()], self.env, 5)
        except (subprocess.TimeoutExpired, OSError):
            return None
        return parse_ssh_g(result.stdout) if result.returncode == 0 else None

    def reachable(self, hostname: str, port: int, timeout: float = 3.0) -> bool:
        try:
            with socket.create_connection((hostname, port), timeout=timeout):
                return True
        except OSError:
            return False

    def master_alive(self, target: SshTarget) -> bool:
        args = ["ssh", *self.ssh.control_args(), "-O", "check", *target.ssh_args()]
        try:
            return _run(args, self.env, 5).returncode == 0
        except (subprocess.TimeoutExpired, OSError):
            return False

    def master_exit(self, target: SshTarget) -> None:
        args = ["ssh", *self.ssh.control_args(), "-O", "exit", *target.ssh_args()]
        with contextlib.suppress(subprocess.TimeoutExpired, OSError):
            _run(args, self.env, 5)

    def hardware_key_present(self, spec: str) -> bool:
        try:
            vid, pid = gates.parse_usb_id(spec)
            output = _run(["ioreg", "-p", "IOUSB", "-l"], self.env, 5).stdout
        except (ValueError, subprocess.TimeoutExpired, OSError):
            return True  # can't tell: don't hold fetches back over it
        return gates.usb_device_present(output, vid, pid)

    def idle_seconds(self) -> float | None:
        try:
            output = _run(["ioreg", "-c", "IOHIDSystem"], self.env, 5).stdout
        except (subprocess.TimeoutExpired, OSError):
            return None
        return gates.parse_idle_seconds(output)

    def fetch(
        self, repo: Path, *, timeout: int, canary: bool = False, remote: str | None = None
    ) -> FetchResult:
        env = dict(self.env)
        env["GIT_SSH_COMMAND"] = self.ssh.canary_command() if canary else self.ssh.batch_command()
        args = ["git", "-C", str(repo), "fetch", "--prune", "--quiet"]
        args += [remote] if remote else ["--all"]
        start = time.monotonic()
        try:
            result = _run(args, env, timeout)
        except subprocess.TimeoutExpired:
            return FetchResult(str(repo), "timed_out", float(timeout), f"no answer in {timeout}s")
        except OSError as error:
            return FetchResult(str(repo), "error", 0.0, str(error))
        duration = round(time.monotonic() - start, 2)
        if result.returncode == 0:
            return FetchResult(str(repo), "ok", duration)
        return FetchResult(str(repo), "failed", duration, result.stderr.strip()[-500:])
