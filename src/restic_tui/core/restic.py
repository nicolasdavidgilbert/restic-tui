"""Wrapper async sobre el binario restic.

Los secretos se pasan solo por el entorno del subproceso (RESTIC_PASSWORD),
nunca por argv ni se registran en logs.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import secrets
import shutil
import signal
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Any

MIN_VERSION = (0, 14)
log = logging.getLogger(__name__)


def mask_url(url: str) -> str:
    """Oculta la contraseña de URLs tipo rest:http://user:pass@host/."""
    return re.sub(r"(://[^:/@]+):[^@/]*@", r"\1:***@", url)


class ResticError(Exception):
    def __init__(self, message: str, returncode: int | None = None):
        super().__init__(message)
        self.returncode = returncode


@dataclass
class Repo:
    url: str = field(repr=False)
    password: str = field(repr=False, default="")
    extra_env: dict[str, str] = field(default_factory=dict, repr=False)
    extra_args: list[str] = field(default_factory=list, repr=False)

    def __repr__(self) -> str:
        return f"Repo({mask_url(self.url)!r})"

    @property
    def display(self) -> str:
        return mask_url(self.url)


def find_restic() -> str:
    path = shutil.which("restic")
    if not path:
        raise ResticError("restic no está instalado")
    return path


def _env(repo: Repo) -> dict[str, str]:
    env = dict(os.environ)
    env["RESTIC_REPOSITORY"] = repo.url
    env["RESTIC_PASSWORD"] = repo.password
    env.pop("RESTIC_PASSWORD_FILE", None)
    env.pop("RESTIC_PASSWORD_COMMAND", None)
    env.update(repo.extra_env)
    return env


async def _spawn(args: list[str], repo: Repo | None):
    env = _env(repo) if repo else dict(os.environ)
    if repo:
        args = [*repo.extra_args, *args]
    log.info("restic %s (repo=%s)", " ".join(args), repo.display if repo else "-")
    return await asyncio.create_subprocess_exec(
        find_restic(), *args,
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        stdin=asyncio.subprocess.DEVNULL, env=env,
    )


async def run(args: list[str], repo: Repo | None = None, ok_codes=(0,)) -> str:
    proc = await _spawn(args, repo)
    out, err = await proc.communicate()
    if proc.returncode not in ok_codes:
        msg = mask_url(err.decode(errors="replace").strip())
        log.warning("restic rc=%s: %s", proc.returncode, msg)
        raise ResticError(msg, proc.returncode)
    return out.decode(errors="replace")


async def stream_json(args: list[str], repo: Repo,
                      ok_codes=(0, 3)) -> AsyncIterator[dict[str, Any]]:
    """Emite cada línea JSON de stdout. Código 3 = backup incompleto.

    Si se cancela, manda SIGINT para que restic libere el lock limpiamente.
    """
    proc = await _spawn(args, repo)
    assert proc.stdout and proc.stderr
    err_task = asyncio.create_task(proc.stderr.read())
    try:
        async for line in proc.stdout:
            line = line.strip()
            if not line:
                continue
            try:
                yield json.loads(line)
            except json.JSONDecodeError:
                continue
        rc = await proc.wait()
    except (asyncio.CancelledError, GeneratorExit):
        if proc.returncode is None:
            proc.send_signal(signal.SIGINT)
            try:
                await asyncio.wait_for(proc.wait(), 15)
            except TimeoutError:
                proc.kill()
                await proc.wait()
        err_task.cancel()
        raise
    err = mask_url((await err_task).decode(errors="replace").strip())
    if rc not in ok_codes:
        log.warning("restic rc=%s: %s", rc, err)
        raise ResticError(err, rc)


async def version() -> tuple[int, ...]:
    out = await run(["version"])
    # "restic 0.16.4 compiled with ..."
    return tuple(int(p) for p in out.split()[1].split(".")[:3])


def _is_wrong_password(msg: str) -> bool:
    return "wrong password" in msg or "no key found" in msg


def _is_missing(msg: str) -> bool:
    return ("is there a repository" in msg or "does not exist" in msg
            or "no such file or directory" in msg)


def is_locked_error(e: Exception) -> bool:
    return "locked" in str(e).lower()


async def probe(url: str, extra_env: dict[str, str] | None = None,
                extra_args: list[str] | None = None) -> bool:
    """True si hay un repo en `url`, sin conocer su contraseña."""
    dummy = Repo(url, "probe-" + secrets.token_hex(16), extra_env or {}, extra_args or [])
    try:
        await run(["cat", "config"], dummy)
        return True  # improbable: la contraseña aleatoria funcionó
    except ResticError as e:
        msg = str(e).lower()
        if _is_wrong_password(msg):
            return True
        if e.returncode == 10 or _is_missing(msg):
            return False
        raise


async def verify_password(repo: Repo) -> None:
    try:
        await run(["cat", "config"], repo)
    except ResticError as e:
        if _is_wrong_password(str(e).lower()):
            raise ResticError("Contraseña incorrecta", e.returncode) from e
        raise


async def repo_exists(repo: Repo) -> bool:
    try:
        await verify_password(repo)
        return True
    except ResticError as e:
        if e.returncode == 10 or _is_missing(str(e).lower()):
            return False
        raise


async def init(repo: Repo) -> None:
    await run(["init"], repo)


def backup(repo: Repo, sources: list[str], excludes: list[str] | None = None,
           tags: list[str] | None = None, dry_run: bool = False):
    args = ["backup", "--json", "--exclude-caches"]
    for e in excludes or []:
        args += ["--exclude", e]
    for t in tags or []:
        args += ["--tag", t]
    if dry_run:
        args.append("--dry-run")
    args += ["--", *sources]
    return stream_json(args, repo)


async def snapshots(repo: Repo) -> list[dict[str, Any]]:
    return json.loads(await run(["snapshots", "--json"], repo)) or []


async def ls(repo: Repo, snapshot: str, path: str = "/") -> list[dict[str, Any]]:
    """Hijos directos de `path` dentro del snapshot."""
    out = await run(["ls", "--json", snapshot, path], repo)
    nodes = []
    for line in out.splitlines():
        try:
            d = json.loads(line)
        except json.JSONDecodeError:
            continue
        if d.get("struct_type") == "node" and d.get("path") != path.rstrip("/"):
            nodes.append(d)
    return nodes


async def diff(repo: Repo, a: str, b: str) -> str:
    return await run(["diff", a, b], repo)


async def restore(repo: Repo, snapshot: str, target: str,
                  includes: list[str] | None = None) -> None:
    args = ["restore", snapshot, "--target", target]
    for i in includes or []:
        args += ["--include", i]
    await run(args, repo)


KEEP_KEYS = ("last", "hourly", "daily", "weekly", "monthly", "yearly")


async def forget(repo: Repo, keep: dict[str, int], prune: bool = False,
                 dry_run: bool = True, host: str | None = None) -> str:
    keep = {k: v for k, v in keep.items() if k in KEEP_KEYS and v > 0}
    if not keep:
        raise ResticError("La política de retención está vacía: no se borraría nada útil")
    args = ["forget"]
    for k, v in keep.items():
        args += [f"--keep-{k}", str(v)]
    if host:
        args += ["--host", host]
    if prune:
        args.append("--prune")
    if dry_run:
        args.append("--dry-run")
    return await run(args, repo)


async def check(repo: Repo, read_data_subset: str | None = None) -> str:
    args = ["check"]
    if read_data_subset:
        args += ["--read-data-subset", read_data_subset]
    return await run(args, repo)


async def unlock(repo: Repo) -> None:
    await run(["unlock"], repo)
