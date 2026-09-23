"""End-to-end tests through `main`, against real repositories with local remotes."""

import json
import os
import plistlib
import time
from pathlib import Path

import pytest
from conftest import Origin, git

from git_autofetch import __version__, cli


@pytest.fixture
def env(tmp_path: Path) -> dict[str, str]:
    return {
        **os.environ,
        "GIT_AUTOFETCH_CONFIG": str(tmp_path / "config.toml"),
        "XDG_STATE_HOME": str(tmp_path / "state"),
    }


def write_config(env: dict[str, str], text: str) -> None:
    Path(env["GIT_AUTOFETCH_CONFIG"]).write_text(text)


def state_file(env: dict[str, str]) -> Path:
    return Path(env["XDG_STATE_HOME"]) / "git-autofetch/state.json"


@pytest.fixture
def watched(origin: Origin, tmp_path: Path, env: dict[str, str]) -> Path:
    repo = origin.clone(tmp_path / "watched")
    write_config(env, f'[[repos]]\npath = "{repo}"\n')
    return repo


def test_version(capsys):
    with pytest.raises(SystemExit) as exit:
        cli.main(["--version"])
    assert exit.value.code == 0
    assert capsys.readouterr().out.strip() == f"git-autofetch {__version__}"


def test_no_command_prints_help(capsys):
    assert cli.main([]) == 2
    assert "usage: git-autofetch" in capsys.readouterr().out


@pytest.mark.parametrize("command", ["fetch", "resume", "list-repos"])
def test_missing_config(command, env, capsys):
    assert cli.main([command], env) == 1
    assert "no config at" in capsys.readouterr().err


def test_status_without_config(env, capsys):
    assert cli.main(["status"], env) == 1
    assert "(missing)" in capsys.readouterr().out


def test_invalid_config_is_an_error(env, capsys):
    write_config(env, "interval = 0\n")
    assert cli.main(["fetch"], env) == 1
    assert "interval must be at least 1" in capsys.readouterr().err


def test_fetch_status_and_list(watched: Path, env, capsys):
    assert cli.main(["fetch"], env) == 0
    assert f"ok        {watched.resolve()}" in capsys.readouterr().out

    state = json.loads(state_file(env).read_text())
    assert state["last_cycle"]["results"][0]["status"] == "ok"

    assert cli.main(["status"], env) == 0
    out = capsys.readouterr().out
    assert "last run:" in out
    assert "every 300s" in out
    assert "ok" in out

    assert cli.main(["list-repos"], env) == 0
    assert capsys.readouterr().out.strip() == str(watched.resolve())


def test_fetch_exits_nonzero_when_a_repo_fails(watched: Path, env, capsys):
    git("remote", "set-url", "origin", "/does/not/exist", cwd=watched)
    assert cli.main(["fetch"], env) == 1
    assert "failed" in capsys.readouterr().out


def test_resume_clears_back_off_and_is_rate_limited(watched: Path, env, capsys):
    state_file(env).parent.mkdir(parents=True)
    state_file(env).write_text(
        json.dumps(
            {"snooze": {"git@github.com": {"until": time.time() + 3600, "level": 2, "reason": "x"}}}
        )
    )

    assert cli.main(["resume"], env) == 0
    assert json.loads(state_file(env).read_text())["snooze"] == {}

    capsys.readouterr()
    assert cli.main(["resume"], env) == 0
    assert "less than a minute ago" in capsys.readouterr().out


def test_run_fetches_then_sleeps(watched: Path, env, monkeypatch):
    sleeps: list[float] = []

    def stop(seconds: float) -> None:
        sleeps.append(seconds)
        raise KeyboardInterrupt

    monkeypatch.setattr(cli.time, "sleep", stop)
    assert cli.main(["run"], env) == 130
    assert json.loads(state_file(env).read_text())["last_cycle"]["results"][0]["status"] == "ok"
    assert sleeps == [cli.RECHECK_SECONDS]


def test_run_waits_for_a_config(env, monkeypatch, caplog):
    monkeypatch.setattr(cli.time, "sleep", lambda s: (_ for _ in ()).throw(KeyboardInterrupt))
    assert cli.main(["run"], env) == 130
    assert "waiting for one" in caplog.text


def test_install_resume_agent_print(env, capsys):
    write_config(env, 'hardware_key = "0x1050"\n')
    assert cli.main(["install-resume-agent", "--print"], env) == 0
    plist = plistlib.loads(capsys.readouterr().out.encode())
    assert plist["ProgramArguments"][-1] == "resume"


def test_install_resume_agent_needs_a_key(env, capsys):
    write_config(env, "")
    assert cli.main(["install-resume-agent", "--print"], env) == 1
    assert "hardware_key" in capsys.readouterr().err


def test_status_shows_key_and_back_off(watched: Path, env, capsys, monkeypatch):
    write_config(env, f'hardware_key = "0x1050"\n[[repos]]\npath = "{watched}"\n')
    monkeypatch.setattr(cli.System, "hardware_key_present", lambda self, spec: False)
    state_file(env).parent.mkdir(parents=True)
    state_file(env).write_text(
        json.dumps(
            {"snooze": {"git@github.com": {"until": time.time() + 3600, "level": 1, "reason": "x"}}}
        )
    )

    assert cli.main(["status"], env) == 0
    out = capsys.readouterr().out
    assert "hardware key: 0x1050 (not plugged in)" in out
    assert "last run:     never" in out
    assert "git@github.com: waiting until" in out


def test_module_entry_point():
    import subprocess
    import sys

    result = subprocess.run(
        [sys.executable, "-m", "git_autofetch", "--version"], capture_output=True, text=True
    )
    assert result.stdout.strip() == f"git-autofetch {__version__}"
