import os
from pathlib import Path

import pytest

from git_autofetch import paths

HOME = Path("/Users/someone")


def test_config_defaults_to_dot_config():
    assert paths.config_file({}, HOME) == HOME / ".config/git-autofetch/config.toml"


def test_config_honours_xdg_config_home():
    env = {"XDG_CONFIG_HOME": "/cfg"}
    assert paths.config_file(env, HOME) == Path("/cfg/git-autofetch/config.toml")


def test_config_env_override_wins():
    env = {"GIT_AUTOFETCH_CONFIG": "/etc/af.toml", "XDG_CONFIG_HOME": "/cfg"}
    assert paths.config_file(env, HOME) == Path("/etc/af.toml")


def test_relative_xdg_is_ignored():
    assert paths.state_dir({"XDG_STATE_HOME": "state"}, HOME) == HOME / ".local/state/git-autofetch"


def test_state_honours_xdg_state_home():
    assert paths.state_dir({"XDG_STATE_HOME": "/st"}, HOME) == Path("/st/git-autofetch")


def test_control_dir_is_short_and_per_user():
    path = paths.control_dir(501)
    assert path == Path("/tmp/git-autofetch-501")
    # ssh needs room for "user@host:port" plus a random suffix within macOS's 104-byte limit.
    assert len(str(path)) < 30


def test_ensure_private_dir_creates_0700(tmp_path: Path):
    path = paths.ensure_private_dir(tmp_path / "sockets")
    assert path.stat().st_mode & 0o777 == 0o700


def test_ensure_private_dir_rejects_open_permissions(tmp_path: Path):
    path = tmp_path / "sockets"
    path.mkdir(mode=0o755)
    path.chmod(0o755)
    with pytest.raises(paths.UnsafeDirectoryError, match="accessible to other users"):
        paths.ensure_private_dir(path)


def test_ensure_private_dir_rejects_a_symlink(tmp_path: Path):
    real = tmp_path / "real"
    real.mkdir(mode=0o700)
    link = tmp_path / "link"
    os.symlink(real, link)
    with pytest.raises(paths.UnsafeDirectoryError, match="not a directory"):
        paths.ensure_private_dir(link)
