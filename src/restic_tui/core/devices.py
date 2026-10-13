"""Detección de discos montados (USB y demás) vía lsblk."""
from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass


@dataclass(frozen=True)
class Device:
    name: str
    label: str
    mountpoint: str
    size: str
    fstype: str
    removable: bool

    @property
    def title(self) -> str:
        kind = "USB" if self.removable else "Disco"
        return f"{kind} {self.label or self.name} ({self.size}) → {self.mountpoint}"


LSBLK_COLS = "NAME,LABEL,MOUNTPOINT,RM,HOTPLUG,SIZE,FSTYPE,TRAN"
SKIP_MOUNTS = ("/boot", "/snap", "[SWAP]")


def _flag(v) -> bool:
    return v in (True, 1, "1", "true")


def parse_lsblk(data: dict) -> list[Device]:
    out: list[Device] = []

    def walk(nodes, parent_removable=False):
        for n in nodes or []:
            removable = (parent_removable or _flag(n.get("rm")) or _flag(n.get("hotplug"))
                         or n.get("tran") == "usb")
            mp = n.get("mountpoint")
            if mp and not mp.startswith(SKIP_MOUNTS):
                out.append(Device(n.get("name", ""), n.get("label") or "", mp,
                                  n.get("size") or "", n.get("fstype") or "", removable))
            walk(n.get("children"), removable)

    walk(data.get("blockdevices"))
    out.sort(key=lambda d: (not d.removable, d.mountpoint))
    return out


def list_devices() -> list[Device]:
    try:
        res = subprocess.run(["lsblk", "-J", "-o", LSBLK_COLS], capture_output=True,
                             text=True, timeout=10, check=True)
        return parse_lsblk(json.loads(res.stdout))
    except (OSError, subprocess.SubprocessError, json.JSONDecodeError):
        return []
