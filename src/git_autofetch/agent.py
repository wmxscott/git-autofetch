"""The optional launch agent that runs `git-autofetch resume` when a hardware key is plugged in."""

from __future__ import annotations

import os
import plistlib
import shutil
import subprocess
import sys
from pathlib import Path

from git_autofetch.gates import parse_usb_id
from git_autofetch.system import EXTRA_PATH

LABEL = "io.github.wmxscott.git-autofetch.resume"


def agent_path(home: Path | None = None) -> Path:
    return (home or Path.home()) / "Library/LaunchAgents" / f"{LABEL}.plist"


def executable() -> str:
    """The command to put in the agent: the stable one on PATH, not a versioned install path."""
    found = shutil.which("git-autofetch")
    return found or os.path.abspath(sys.argv[0])


def resume_agent(hardware_key: str, program: str) -> bytes:
    vid, pid = parse_usb_id(hardware_key)
    matching: dict[str, object] = {"IOProviderClass": "IOUSBHostDevice", "idVendor": vid}
    if pid is not None:
        matching["idProduct"] = pid
    return plistlib.dumps(
        {
            "Label": LABEL,
            "ProgramArguments": [program, "resume"],
            "LaunchEvents": {"com.apple.iokit.matching": {f"{LABEL}.key-inserted": matching}},
            "EnvironmentVariables": {"PATH": ":".join((*EXTRA_PATH, "/usr/sbin", "/sbin"))},
            "RunAtLoad": False,
            "ProcessType": "Background",
        }
    )


def _launchctl(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["launchctl", *args], capture_output=True, text=True)


def install(hardware_key: str, home: Path | None = None) -> Path:
    path = agent_path(home)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(resume_agent(hardware_key, executable()))
    domain = f"gui/{os.getuid()}"
    _launchctl("bootout", f"{domain}/{LABEL}")
    result = _launchctl("bootstrap", domain, str(path))
    if result.returncode != 0:
        raise RuntimeError(f"launchctl bootstrap failed: {result.stderr.strip()}")
    return path


def uninstall(home: Path | None = None) -> bool:
    path = agent_path(home)
    _launchctl("bootout", f"gui/{os.getuid()}/{LABEL}")
    if path.exists():
        path.unlink()
        return True
    return False
