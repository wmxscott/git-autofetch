"""Integration tests against real git, ssh and sockets."""

import os
import shutil
import socket
import time
from pathlib import Path

import pytest
from conftest import Origin, git

from git_autofetch.config import Config
from git_autofetch.repos import SshTarget
from git_autofetch.ssh import SshOptions
from git_autofetch.system import System, build_env


@pytest.fixture
def system(tmp_path: Path) -> System:
    sockets = tmp_path / "s"
    sockets.mkdir(mode=0o700)
    return System(SshOptions(str(sockets / "%r@%h:%p")), dict(os.environ))


def test_fetch_updates_and_prunes_remote_branches(origin: Origin, system: System, tmp_path: Path):
    watched = origin.clone(tmp_path / "watched")
    head = origin.push_branch("feature")

    assert system.fetch(watched, timeout=30).status == "ok"
    assert git("rev-parse", "origin/feature", cwd=watched) == head

    origin.delete_branch("feature")
    assert system.fetch(watched, timeout=30).status == "ok"
    assert "origin/feature" not in git("branch", "-r", cwd=watched)


def test_fetch_one_remote(origin: Origin, system: System, tmp_path: Path):
    watched = origin.clone(tmp_path / "watched")
    git("remote", "add", "broken", str(tmp_path / "missing.git"), cwd=watched)

    assert system.fetch(watched, timeout=30, remote="origin").status == "ok"
    assert system.fetch(watched, timeout=30).status == "failed"


def test_failed_fetch_carries_gits_error(system: System, tmp_path: Path):
    repo = tmp_path / "repo"
    git("init", "-q", str(repo))
    git("remote", "add", "origin", str(tmp_path / "missing.git"), cwd=repo)

    result = system.fetch(repo, timeout=30)
    assert result.status == "failed"
    assert "does not appear to be a git repository" in result.detail


def test_timeout(origin: Origin, system: System, tmp_path: Path):
    watched = origin.clone(tmp_path / "watched")
    assert system.fetch(watched, timeout=0).status == "timed_out"


def test_remotes(origin: Origin, system: System, tmp_path: Path):
    watched = origin.clone(tmp_path / "watched")
    git("remote", "add", "gh", "git@github.com:o/r.git", cwd=watched)

    remotes = {r.name: r for r in system.remotes(watched)}
    assert remotes["origin"].target is None
    assert remotes["gh"].target == SshTarget("github.com", "git")
    assert system.remotes(tmp_path / "not-a-repo") == []


@pytest.mark.skipif(not shutil.which("ssh"), reason="needs ssh")
def test_batch_fetch_cannot_log_in_without_a_shared_connection(system: System, tmp_path: Path):
    """The core safety property: with no live connection, a batch fetch fails
    immediately instead of trying keys, so it can never prompt."""
    repo = tmp_path / "repo"
    git("init", "-q", str(repo))
    git("remote", "add", "origin", "ssh://git@192.0.2.1/o/r.git", cwd=repo)  # TEST-NET, unroutable

    start = time.monotonic()
    result = system.fetch(repo, timeout=30)
    assert result.status == "failed"
    assert time.monotonic() - start < 5
    assert not system.master_alive(SshTarget("192.0.2.1", "git"))


@pytest.mark.skipif(not shutil.which("ssh"), reason="needs ssh")
def test_resolve_uses_ssh_config(system: System):
    resolved = system.resolve(SshTarget("example.invalid", "git", 2222))
    assert resolved is not None
    assert resolved.port == 2222


def test_reachable(system: System):
    with socket.socket() as server:
        server.bind(("127.0.0.1", 0))
        server.listen()
        port = server.getsockname()[1]
        assert system.reachable("127.0.0.1", port)
    assert not system.reachable("127.0.0.1", port, timeout=1)


def test_build_env(tmp_path: Path):
    askpass = tmp_path / "askpass"
    askpass.write_text("#!/bin/sh\n")
    askpass.chmod(0o755)
    config = Config(ssh_auth_sock="~/agent.sock", askpass=str(askpass))

    env = build_env(config, {"PATH": "/custom/bin:/usr/bin", "KEEP": "1"})

    assert env["KEEP"] == "1"
    assert env["GIT_TERMINAL_PROMPT"] == "0"
    assert env["SSH_AUTH_SOCK"] == str(Path("~/agent.sock").expanduser())
    assert env["SSH_ASKPASS"] == str(askpass)
    assert env["SSH_ASKPASS_REQUIRE"] == "force"
    assert env["PATH"].split(":")[:2] == ["/custom/bin", "/usr/bin"]
    assert "/opt/homebrew/bin" in env["PATH"].split(":")
    assert env["PATH"].split(":").count("/usr/bin") == 1


def test_build_env_leaves_ssh_alone_by_default():
    env = build_env(Config(askpass="/does/not/exist"), {"SSH_AUTH_SOCK": "/mine"})
    assert env["SSH_AUTH_SOCK"] == "/mine"
    assert "SSH_ASKPASS" not in env
    assert "SSH_ASKPASS_REQUIRE" not in env


class Output:
    def __init__(self, stdout: str = "", returncode: int = 0):
        self.stdout = stdout
        self.returncode = returncode


def test_hardware_key_and_idle_read_ioreg(system: System, monkeypatch):
    from test_gates import IOREG_USB

    import git_autofetch.system as module

    outputs = {"IOUSB": IOREG_USB, "IOHIDSystem": '"HIDIdleTime" = 5000000000'}
    monkeypatch.setattr(module, "_run", lambda args, env, timeout: Output(outputs[args[2]]))
    assert system.hardware_key_present("0x1050")
    assert not system.hardware_key_present("0x1234")
    assert system.idle_seconds() == 5.0


def test_checks_fail_open_when_ioreg_is_missing(system: System, monkeypatch):
    import git_autofetch.system as module

    def missing(*args, **kwargs):
        raise FileNotFoundError("ioreg")

    monkeypatch.setattr(module, "_run", missing)
    assert system.hardware_key_present("0x1050")
    assert system.idle_seconds() is None
    assert not system.master_alive(SshTarget("h"))
    system.master_exit(SshTarget("h"))


def test_master_commands_use_the_control_path(system: System, monkeypatch):
    import git_autofetch.system as module

    seen: list[list[str]] = []
    monkeypatch.setattr(
        module, "_run", lambda args, env, timeout: seen.append(args) or Output(returncode=0)
    )
    target = SshTarget("github.com", "git", 2222)
    assert system.master_alive(target)
    system.master_exit(target)
    control = f"ControlPath={system.ssh.control_path}"
    assert seen == [
        ["ssh", "-o", control, "-O", "check", "-p", "2222", "git@github.com"],
        ["ssh", "-o", control, "-O", "exit", "-p", "2222", "git@github.com"],
    ]
