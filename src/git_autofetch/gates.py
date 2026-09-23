"""Checks that decide whether a fresh login may be attempted: key present, user present."""

from __future__ import annotations

import re


def parse_usb_id(spec: str) -> tuple[int, int | None]:
    """Parse `"0xVID"` or `"0xVID:0xPID"` (hex) into `(vid, pid)`. Raises ValueError."""
    parts = spec.strip().split(":")
    if not 1 <= len(parts) <= 2 or not parts[0].strip():
        raise ValueError(f"expected 0xVID or 0xVID:0xPID, got {spec!r}")
    vid = int(parts[0], 16)
    pid = int(parts[1], 16) if len(parts) == 2 and parts[1].strip() else None
    return vid, pid


def usb_device_present(ioreg_output: str, vid: int, pid: int | None = None) -> bool:
    """Whether `ioreg -p IOUSB -l` output lists a device with this vendor (and product) id.

    ioreg prints ids in decimal. Vendor and product are matched per device, so a
    token's product id is never paired with another device's vendor id.
    """
    for device in re.split(r"\n(?=[ |]*\+-o )", ioreg_output):
        if not re.search(rf'"idVendor" = {vid}\b', device):
            continue
        if pid is None or re.search(rf'"idProduct" = {pid}\b', device):
            return True
    return False


def parse_idle_seconds(ioreg_output: str) -> float | None:
    """Seconds since the last keyboard, mouse or trackpad input, from `ioreg -c IOHIDSystem`."""
    match = re.search(r'"HIDIdleTime"\s*=\s*(\d+)', ioreg_output)
    return int(match.group(1)) / 1_000_000_000 if match else None
