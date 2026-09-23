"""Where git-autofetch keeps its config, state and connection sockets."""

from __future__ import annotations

import os
import stat
from collections.abc import Mapping
from pathlib import Path

APP = "git-autofetch"


class UnsafeDirectoryError(Exception):
    pass


def _base(env: Mapping[str, str], var: str, fallback: Path) -> Path:
    value = env.get(var, "")
    return Path(value) if value.startswith("/") else fallback


def config_file(env: Mapping[str, str] = os.environ, home: Path | None = None) -> Path:
    """`$GIT_AUTOFETCH_CONFIG`, else `$XDG_CONFIG_HOME/git-autofetch/config.toml`."""
    home = home or Path.home()
    if env.get("GIT_AUTOFETCH_CONFIG"):
        return Path(env["GIT_AUTOFETCH_CONFIG"]).expanduser()
    return _base(env, "XDG_CONFIG_HOME", home / ".config") / APP / "config.toml"


def state_dir(env: Mapping[str, str] = os.environ, home: Path | None = None) -> Path:
    """`$XDG_STATE_HOME/git-autofetch`, else `~/.local/state/git-autofetch`."""
    home = home or Path.home()
    return _base(env, "XDG_STATE_HOME", home / ".local/state") / APP


def control_dir(uid: int | None = None, base: Path = Path("/tmp")) -> Path:
    """Directory for connection sockets.

    Under /tmp rather than the state directory because a Unix socket path is
    limited to 104 bytes on macOS, and ssh adds a random suffix while creating it.
    """
    return base / f"{APP}-{os.getuid() if uid is None else uid}"


def ensure_private_dir(path: Path) -> Path:
    """Create `path` as a 0700 directory, refusing one another user could tamper with."""
    path.mkdir(mode=0o700, parents=True, exist_ok=True)
    info = path.lstat()
    if not stat.S_ISDIR(info.st_mode):
        raise UnsafeDirectoryError(f"{path} is not a directory")
    if info.st_uid != os.getuid():
        raise UnsafeDirectoryError(f"{path} is owned by another user")
    if info.st_mode & 0o077:
        raise UnsafeDirectoryError(
            f"{path} is accessible to other users (mode {info.st_mode & 0o777:o})"
        )
    return path
