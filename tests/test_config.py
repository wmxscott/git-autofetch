from pathlib import Path

import pytest

from git_autofetch.config import Config, ConfigError, RepoEntry, load, parse


def test_defaults():
    config = parse({})
    assert config == Config()
    assert config.interval == 300
    assert config.control_persist == "24h"
    assert config.repos == ()


def test_full_config(tmp_path: Path):
    path = tmp_path / "config.toml"
    path.write_text(
        """
interval = 15
parallel = 8
fetch_timeout = 90
connect_timeout = 20
control_path = "~/.ssh/cm-%r@%h:%p"
control_persist = "168h"
hardware_key = "0x1050"
idle_threshold = 0
ssh_auth_sock = "/tmp/agent.sock"
askpass = "~/bin/askpass"

[[repos]]
path = "~/src/one"

[[repos]]
path = "/src/many"
recursive = true
max_depth = 3
"""
    )
    config = load(path)
    assert config is not None
    assert config.interval == 15
    assert config.parallel == 8
    assert config.idle_threshold == 0
    assert config.control_path == "~/.ssh/cm-%r@%h:%p"
    assert config.hardware_key == "0x1050"
    assert config.repos == (
        RepoEntry(Path("~/src/one").expanduser()),
        RepoEntry(Path("/src/many"), recursive=True, max_depth=3),
    )


def test_missing_file_is_none(tmp_path: Path):
    assert load(tmp_path / "nope.toml") is None


def test_bad_toml_names_the_file(tmp_path: Path):
    path = tmp_path / "config.toml"
    path.write_text("interval = ")
    with pytest.raises(ConfigError, match=r"config\.toml"):
        load(path)


@pytest.mark.parametrize(
    ("data", "message"),
    [
        ({"intervall": 5}, "unknown setting.*intervall"),
        ({"interval": 0}, "interval must be at least 1"),
        ({"interval": "5"}, "interval must be a whole number"),
        ({"interval": True}, "interval must be a whole number"),
        ({"interval": 1.5}, "interval must be a whole number"),
        ({"idle_threshold": -1}, "idle_threshold must be at least 0"),
        ({"hardware_key": "yubikey"}, "hardware_key"),
        ({"hardware_key": 4176}, "hardware_key must be a string"),
        ({"control_persist": " "}, "control_persist must not be empty"),
        ({"repos": ["~/src"]}, r"repos\[0\] must be a table"),
        ({"repos": [{"path": ""}]}, r"repos\[0\].path"),
        ({"repos": [{"path": "/a", "recursive": "yes"}]}, r"repos\[0\].recursive"),
        ({"repos": [{"path": "/a", "max_depth": 0}]}, r"repos\[0\].max_depth"),
        ({"repos": [{"path": "/a", "depth": 2}]}, r"repos\[0\]: unknown setting.*depth"),
    ],
)
def test_invalid(data, message):
    with pytest.raises(ConfigError, match=message):
        parse(data)


def test_strings_are_trimmed():
    assert parse({"hardware_key": " 0x1050 "}).hardware_key == "0x1050"
