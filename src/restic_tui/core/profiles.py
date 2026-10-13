"""Recuerda el último origen/destino usado (JSON). Nunca contiene contraseñas."""
from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, field, fields
from pathlib import Path

DEST_TYPES = ("local", "sftp", "rest")


def config_dir() -> Path:
    base = os.environ.get("XDG_CONFIG_HOME") or str(Path.home() / ".config")
    return Path(base) / "restic-tui"


def default_path() -> Path:
    return config_dir() / "last.json"


@dataclass
class Profile:
    name: str = "último"
    sources: list[str] = field(default_factory=list)
    excludes: list[str] = field(default_factory=list)
    tags: list[str] = field(default_factory=list)
    dest_type: str = "local"
    local_path: str = ""
    sftp_user: str = ""
    sftp_host: str = ""
    sftp_port: int = 22
    sftp_path: str = ""
    rest_url: str = ""
    rest_user: str = ""
    keep: dict[str, int] = field(
        default_factory=lambda: {"daily": 7, "weekly": 4, "monthly": 6})
    last_backup: str = ""  # resumen del último backup correcto, solo informativo

    @classmethod
    def from_dict(cls, d: dict) -> Profile:
        known = {f.name for f in fields(cls)}
        return cls(**{k: v for k, v in d.items() if k in known})


def load_last(path: Path | None = None) -> Profile | None:
    path = path or default_path()
    try:
        return Profile.from_dict(json.loads(path.read_text()))
    except (OSError, ValueError, TypeError):
        return None


def save_last(profile: Profile, path: Path | None = None) -> None:
    path = path or default_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    os.chmod(path.parent, 0o700)
    tmp = path.with_suffix(".tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        json.dump(asdict(profile), f, indent=2, ensure_ascii=False)
    os.replace(tmp, path)
    os.chmod(path, 0o600)
