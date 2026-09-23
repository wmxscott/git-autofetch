import plistlib
from pathlib import Path

import pytest

from git_autofetch import agent


def matching(plist: dict) -> dict:
    return plist["LaunchEvents"]["com.apple.iokit.matching"][f"{agent.LABEL}.key-inserted"]


def test_resume_agent_matches_the_vendor():
    plist = plistlib.loads(agent.resume_agent("0x1050", "/opt/homebrew/bin/git-autofetch"))

    assert plist["Label"] == agent.LABEL
    assert plist["ProgramArguments"] == ["/opt/homebrew/bin/git-autofetch", "resume"]
    assert matching(plist) == {"IOProviderClass": "IOUSBHostDevice", "idVendor": 4176}
    assert plist["RunAtLoad"] is False
    assert "/opt/homebrew/bin" in plist["EnvironmentVariables"]["PATH"]


def test_resume_agent_matches_the_product_too():
    plist = plistlib.loads(agent.resume_agent("0x1050:0x0407", "git-autofetch"))
    assert matching(plist)["idProduct"] == 1031


def test_resume_agent_rejects_a_bad_key():
    with pytest.raises(ValueError):
        agent.resume_agent("yubikey", "git-autofetch")


def test_agent_path():
    home = Path("/Users/someone")
    assert agent.agent_path(home) == home / f"Library/LaunchAgents/{agent.LABEL}.plist"


def test_install_and_uninstall(tmp_path: Path, monkeypatch):
    calls: list[tuple[str, ...]] = []
    monkeypatch.setattr(agent, "_launchctl", lambda *args: calls.append(args) or Ok())
    monkeypatch.setattr(agent, "executable", lambda: "/opt/homebrew/bin/git-autofetch")

    path = agent.install("0x1050", home=tmp_path)

    assert path == agent.agent_path(tmp_path)
    assert matching(plistlib.loads(path.read_bytes()))["idVendor"] == 4176
    assert [c[0] for c in calls] == ["bootout", "bootstrap"]

    assert agent.uninstall(home=tmp_path) is True
    assert not path.exists()
    assert agent.uninstall(home=tmp_path) is False


def test_install_reports_launchctl_failure(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(agent, "_launchctl", lambda *args: Ok(returncode=5, stderr="denied"))
    with pytest.raises(RuntimeError, match="denied"):
        agent.install("0x1050", home=tmp_path)


class Ok:
    def __init__(self, returncode: int = 0, stderr: str = ""):
        self.returncode = returncode
        self.stderr = stderr
