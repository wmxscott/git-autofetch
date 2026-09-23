"""The ssh commands git runs, and how they share one connection per host.

The canary fetch for each host logs in normally and leaves a shared connection
(an ssh ControlMaster) behind. Every other fetch rides that connection with an
ssh command that cannot log in on its own: `ProxyCommand=false` means that
without a live shared connection it fails at once, before any key is used, so
only the canary can ever ask for a passphrase, a touch or an approval.
"""

from __future__ import annotations

import shlex
from dataclasses import dataclass

CONTROL_NAME = "%r@%h:%p"


@dataclass(frozen=True)
class SshOptions:
    control_path: str
    control_persist: str = "24h"
    connect_timeout: int = 10

    def canary_command(self) -> str:
        return shlex.join(
            [
                "ssh",
                "-o", "ControlMaster=auto",
                "-o", f"ControlPath={self.control_path}",
                "-o", f"ControlPersist={self.control_persist}",
                "-o", f"ConnectTimeout={self.connect_timeout}",
            ]
        )  # fmt: skip

    def batch_command(self) -> str:
        return shlex.join(
            [
                "ssh",
                "-o", "ControlMaster=no",
                "-o", f"ControlPath={self.control_path}",
                "-o", "ProxyCommand=false",
                "-o", "BatchMode=yes",
            ]
        )  # fmt: skip

    def control_args(self) -> list[str]:
        return ["-o", f"ControlPath={self.control_path}"]


@dataclass(frozen=True)
class Resolved:
    hostname: str
    port: int
    proxied: bool


def parse_ssh_g(output: str) -> Resolved | None:
    """The real host and port from `ssh -G` output, after aliases in ssh config apply."""
    settings: dict[str, str] = {}
    for line in output.splitlines():
        key, _, value = line.partition(" ")
        settings.setdefault(key.lower(), value.strip())
    hostname = settings.get("hostname")
    if not hostname:
        return None
    try:
        port = int(settings.get("port", "22"))
    except ValueError:
        port = 22
    proxied = any(settings.get(key, "none") != "none" for key in ("proxycommand", "proxyjump"))
    return Resolved(hostname, port, proxied)
