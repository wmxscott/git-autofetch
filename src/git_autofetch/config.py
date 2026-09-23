"""The TOML config file."""

from __future__ import annotations

import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from git_autofetch.gates import parse_usb_id


class ConfigError(Exception):
    pass


@dataclass(frozen=True)
class RepoEntry:
    path: Path
    recursive: bool = False
    max_depth: int = 1


@dataclass(frozen=True)
class Config:
    interval: int = 300
    parallel: int = 4
    fetch_timeout: int = 60
    connect_timeout: int = 30
    control_path: str = ""
    control_persist: str = "24h"
    hardware_key: str = ""
    idle_threshold: int = 180
    ssh_auth_sock: str = ""
    askpass: str = ""
    repos: tuple[RepoEntry, ...] = field(default_factory=tuple)


_MINIMUMS = {
    "interval": 1,
    "parallel": 1,
    "fetch_timeout": 1,
    "connect_timeout": 1,
    "idle_threshold": 0,
}
_STRINGS = ("control_path", "control_persist", "hardware_key", "ssh_auth_sock", "askpass")


def load(path: Path) -> Config | None:
    """Read the config file, or return None if it doesn't exist."""
    try:
        with path.open("rb") as file:
            data = tomllib.load(file)
    except FileNotFoundError:
        return None
    except tomllib.TOMLDecodeError as error:
        raise ConfigError(f"{path}: {error}") from error
    try:
        return parse(data)
    except ConfigError as error:
        raise ConfigError(f"{path}: {error}") from error


def parse(data: dict[str, Any]) -> Config:
    known = {*_MINIMUMS, *_STRINGS, "repos"}
    unknown = sorted(set(data) - known)
    if unknown:
        raise ConfigError(f"unknown setting(s): {', '.join(unknown)}")

    values: dict[str, Any] = {}
    for key, minimum in _MINIMUMS.items():
        if key in data:
            values[key] = _integer(data[key], key, minimum)
    for key in _STRINGS:
        if key in data:
            if not isinstance(data[key], str):
                raise ConfigError(f"{key} must be a string")
            values[key] = data[key].strip()

    if values.get("hardware_key"):
        try:
            parse_usb_id(values["hardware_key"])
        except ValueError as error:
            raise ConfigError(f"hardware_key: {error}") from error
    if "control_persist" in values and not values["control_persist"]:
        raise ConfigError("control_persist must not be empty")

    values["repos"] = tuple(
        _repo(entry, index) for index, entry in enumerate(data.get("repos", []))
    )
    return Config(**values)


def _integer(value: Any, key: str, minimum: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ConfigError(f"{key} must be a whole number")
    if value < minimum:
        raise ConfigError(f"{key} must be at least {minimum}")
    return value


def _repo(entry: Any, index: int) -> RepoEntry:
    where = f"repos[{index}]"
    if not isinstance(entry, dict):
        raise ConfigError(f"{where} must be a table with a path")
    unknown = sorted(set(entry) - {"path", "recursive", "max_depth"})
    if unknown:
        raise ConfigError(f"{where}: unknown setting(s): {', '.join(unknown)}")
    path = entry.get("path")
    if not isinstance(path, str) or not path.strip():
        raise ConfigError(f"{where}.path must be a non-empty string")
    recursive = entry.get("recursive", False)
    if not isinstance(recursive, bool):
        raise ConfigError(f"{where}.recursive must be true or false")
    max_depth = _integer(entry.get("max_depth", 1), f"{where}.max_depth", 1)
    return RepoEntry(Path(path.strip()).expanduser(), recursive, max_depth)
