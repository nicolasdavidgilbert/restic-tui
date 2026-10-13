"""Asistente de backups con restic: preguntas paso a paso en la terminal.

Cada pregunta es un paso. Esc (en menús) o '<' (al escribir) vuelve al paso anterior,
y antes de empezar se muestra un resumen desde el que se puede cambiar cualquier cosa.
"""
from __future__ import annotations

import asyncio
import logging
import os
import socket
import sys
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from . import ui
from .core import remote, restic
from .core.devices import list_devices
from .core.passwords import generate_password, validate_new_password
from .core.profiles import Profile, load_last, save_last

SKIP = object()  # un paso que no ha preguntado nada (se salta también al ir hacia atrás)


class Jump(Exception):
    """Salta a un paso concreto (desde el resumen).

    Si se indica `until`, al llegar a ese paso se vuelve al que saltó en vez de seguir:
    así "Cambiar origen" pregunta solo el origen y regresa al resumen.
    """

    def __init__(self, step: str, until: str | None = None):
        super().__init__(step)
        self.step = step
        self.until = until


# ---------------------------------------------------------------- formato
def human_bytes(n: float) -> str:
    for unit in ("B", "KiB", "MiB", "GiB", "TiB"):
        if abs(n) < 1024 or unit == "TiB":
            return f"{int(n)} B" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} TiB"


def human_time(s: float | None) -> str:
    if s is None:
        return "?"
    m, sec = divmod(int(s), 60)
    h, m = divmod(m, 60)
    return f"{h}:{m:02d}:{sec:02d}" if h else f"{m}:{sec:02d}"


# ---------------------------------------------------------------- motor de pasos
def run_steps(steps: list[tuple[str, Callable[[], object]]], start: int = 0) -> None:
    """Ejecuta los pasos en orden.

    - `ui.Back` en un paso vuelve al anterior (saltando los que no preguntaron nada);
      en el primero se propaga hacia fuera.
    - `Jump(nombre, until)` salta a ese paso; Back justo ahí, o llegar a `until`,
      vuelve al paso que saltó.
    """
    names = [n for n, _ in steps]
    i, going_back = start, False
    jumped: tuple[int, int, int | None] | None = None  # (destino, origen, hasta)
    while i < len(steps):
        try:
            asked = steps[i][1]() is not SKIP
        except ui.Back:
            if jumped and jumped[0] == i:
                i, jumped, going_back = jumped[1], None, False
                continue
            if i == 0:
                raise
            i, going_back = i - 1, True
            continue
        except Jump as j:
            jumped = (names.index(j.step), i, names.index(j.until) if j.until else None)
            i, going_back = jumped[0], False
            continue
        if going_back and not asked:
            if i == 0:
                raise ui.Back
            i -= 1
            continue
        i, going_back = i + 1, False
        if jumped and i == jumped[2]:
            i, jumped = jumped[1], None


@dataclass
class Choice:
    """Respuestas que se recuerdan al volver atrás."""
    p: Profile
    src_pick: int = 0
    src_other: bool = False
    dst_pick: int = 0
    dst_other: bool = False
    devices: list = field(default_factory=list)
    # Valores guardados al empezar: las listas de opciones no deben cambiar mientras
    # se responde, o al volver atrás el índice recordado apuntaría a otra opción.
    last_source: str = ""
    last_local: str = ""

    @classmethod
    def start(cls, last: Profile | None) -> Choice:
        p = last or Profile()
        return cls(p=p, devices=list_devices(),
                   last_source=p.sources[0] if p.sources else "",
                   last_local=p.local_path if p.dest_type == "local" else "")


# ---------------------------------------------------------------- origen
def _source_options(c: Choice) -> list[tuple[str, str | None]]:
    opts: list[tuple[str, str | None]] = []
    if c.last_source:
        opts.append((f"La de la última vez: {c.last_source}", c.last_source))
    opts += [(d.title, d.mountpoint) for d in c.devices if d.removable]
    opts.append((f"Carpeta personal ({Path.home()})", str(Path.home())))
    opts.append(("Otra ruta...", None))
    return opts


def step_source(c: Choice) -> None:
    opts = _source_options(c)
    c.src_pick = ui.select("¿Qué quieres respaldar?", [o[0] for o in opts], c.src_pick)
    path = opts[c.src_pick][1]
    c.src_other = path is None
    if path:
        c.p.sources = [path]


def step_source_path(c: Choice) -> object:
    if not c.src_other:
        return SKIP
    while True:
        path = ui.ask("Ruta de la carpeta:", c.p.sources[0] if c.p.sources else "")
        path = str(Path(path).expanduser().resolve())
        if Path(path).is_dir():
            c.p.sources = [path]
            return None
        ui.error(f"No existe esa carpeta: {path}")


# ---------------------------------------------------------------- destino
KINDS = (("sftp", "Otro equipo de la red (SSH)"), ("local", "USB / carpeta local"),
         ("rest", "rest-server"))


def step_kind(c: Choice) -> None:
    default = [k for k, _ in KINDS].index(c.p.dest_type) if c.p.dest_type in dict(KINDS) else 0
    c.p.dest_type = KINDS[ui.select("¿Dónde lo guardo?", [n for _, n in KINDS], default)][0]


def step_dest(c: Choice) -> None:
    p = c.p
    if p.dest_type == "sftp":
        def host() -> None:
            while True:
                h = ui.ask("Equipo (nombre o IP):", p.sftp_host)
                try:
                    remote.validate_ssh("x", h, 22)
                    p.sftp_host = h
                    return
                except remote.ConfigError as e:
                    ui.error(str(e))

        def user() -> None:
            p.sftp_user = ui.ask("Usuario en ese equipo:",
                                 p.sftp_user or os.environ.get("USER", ""))

        def port() -> None:
            while True:
                v = ui.ask("Puerto SSH:", str(p.sftp_port or 22))
                if v.isdigit() and 1 <= int(v) <= 65535:
                    p.sftp_port = int(v)
                    return
                ui.error("Puerto no válido")

        def path() -> None:
            p.sftp_path = ui.ask("Carpeta del repositorio en ese equipo:",
                                 p.sftp_path or f"/srv/restic/{socket.gethostname()}")

        run_steps([("host", host), ("user", user), ("port", port), ("path", path)])

    elif p.dest_type == "local":
        src = Path(p.sources[0]) if p.sources else None
        opts: list[tuple[str, str | None]] = []
        if c.last_local:
            opts.append((f"La de la última vez: {c.last_local}", c.last_local))
        opts += [(d.title, str(Path(d.mountpoint) / "restic-repo")) for d in c.devices
                 if d.removable and (src is None or Path(d.mountpoint) != src)]
        opts.append(("Otra ruta...", None))

        def pick() -> object:
            if len(opts) == 1:
                c.dst_other = True
                return SKIP
            c.dst_pick = ui.select("¿En qué carpeta o USB?", [o[0] for o in opts], c.dst_pick)
            c.dst_other = opts[c.dst_pick][1] is None
            if not c.dst_other:
                p.local_path = opts[c.dst_pick][1]
            return None

        def typed() -> object:
            if not c.dst_other:
                return SKIP
            p.local_path = str(Path(ui.ask(
                "Ruta del repositorio (ej. /media/usuario/DISCO/restic-repo):",
                p.local_path)).expanduser().resolve())
            return None

        run_steps([("pick", pick), ("typed", typed)])

    else:
        def url() -> None:
            while True:
                v = ui.ask("URL del rest-server (ej. http://nas:8000/portatil):",
                           p.rest_url)
                try:
                    remote.validate_rest_url(v)
                    p.rest_url = v
                    return
                except remote.ConfigError as e:
                    ui.error(str(e))

        def ruser() -> None:
            p.rest_user = ui.ask("Usuario de rest-server (Enter si no tiene):", p.rest_user,
                                 allow_empty=True)

        run_steps([("url", url), ("user", ruser)])


def dest_problem(p: Profile) -> str | None:
    """Errores que impiden usar el destino elegido."""
    try:
        remote.repo_url(p)
    except remote.ConfigError as e:
        return str(e)
    if p.dest_type == "local" and p.sources:
        repo, src = Path(p.local_path), Path(p.sources[0])
        if repo == src or src in repo.parents:
            return "El repositorio no puede estar dentro de lo que respaldas"
    return None


# ---------------------------------------------------------------- SSH / repositorio
def ensure_ssh_access(p: Profile) -> bool:
    ui.info("Comprobando acceso SSH...")
    good, msg = asyncio.run(remote.test_ssh(p.sftp_user, p.sftp_host, p.sftp_port))
    if good:
        ui.ok(msg)
        return True
    ui.error(msg)
    if "configurar acceso SSH" not in msg:
        return False  # red, nombre o bloqueo: ssh-copy-id no lo arregla
    if ui.confirm("¿Configuro ahora el acceso con clave SSH? (pide la contraseña "
                  "del otro equipo una vez)", default=True, back=False):
        remote.setup_ssh_interactive(p.sftp_user, p.sftp_host, p.sftp_port)
        good, msg = asyncio.run(remote.test_ssh(p.sftp_user, p.sftp_host, p.sftp_port))
        (ui.ok if good else ui.error)(msg)
    return good


def open_repo(p: Profile, create: bool) -> restic.Repo | None:
    if p.dest_type == "sftp" and not ensure_ssh_access(p):
        return None
    rest_pw = ""
    if p.dest_type == "rest" and p.rest_user:
        rest_pw = ui.ask_password(f"Contraseña de rest-server para '{p.rest_user}':")
    url = remote.repo_url(p, rest_pw)
    args = remote.restic_args(p)
    shown = restic.mask_url(url)
    ui.info(f"Conectando con {shown}...")
    try:
        exists = asyncio.run(restic.probe(url, extra_args=args))
    except restic.ResticError as e:
        ui.error(f"No puedo acceder al destino: {e}")
        return None

    if exists:
        ui.ok("Repositorio encontrado")
        for _ in range(3):
            repo = restic.Repo(url, ui.ask_password("Contraseña del repositorio:"),
                               extra_args=args)
            try:
                asyncio.run(restic.verify_password(repo))
                return repo
            except restic.ResticError as e:
                ui.error(str(e))
        return None

    if not create:
        ui.warn("Ahí no hay ningún repositorio todavía. Haz primero un backup.")
        return None
    if not ui.confirm(f"No hay repositorio en {shown}. ¿Lo creo?", default=True, back=False):
        return None
    pw = new_password()
    repo = restic.Repo(url, pw, extra_args=args)
    try:
        asyncio.run(restic.init(repo))
    except restic.ResticError as e:
        ui.error(f"No se pudo crear: {e}")
        return None
    ui.ok("Repositorio creado")
    return repo


def new_password() -> str:
    how = ui.select("Contraseña del repositorio nuevo:",
                    ["Generar una segura", "Escribirla yo"], back=False)
    if how == 0:
        pw = generate_password()
        print(f"\n    {ui.accent(pw)}\n")
    else:
        while True:
            pw = ui.ask_password("Contraseña nueva:")
            err = validate_new_password(pw, ui.ask_password("Repítela:"))
            if not err:
                break
            ui.error(err)
    ui.warn("Sin esta contraseña los datos son IRRECUPERABLES. Guárdala en tu gestor "
            "de contraseñas: este programa no la guarda.")
    while not ui.confirm("¿La has guardado?", back=False):
        pass
    return pw


# ---------------------------------------------------------------- backup
async def _run_backup(repo: restic.Repo, p: Profile) -> tuple[dict | None, list[str]]:
    summary, errors = None, []
    async for ev in restic.backup(repo, p.sources, p.excludes, p.tags):
        kind = ev.get("message_type")
        if kind == "status":
            done, elapsed = ev.get("bytes_done", 0), ev.get("seconds_elapsed") or 0
            speed = f"{human_bytes(done / elapsed)}/s" if elapsed else ""
            ui.progress(ev.get("percent_done") or 0,
                        f"{human_bytes(done)}/{human_bytes(ev.get('total_bytes', 0))}  "
                        f"{speed}  quedan {human_time(ev.get('seconds_remaining'))}")
        elif kind == "error":
            err = ev.get("error", {})
            errors.append(f"{ev.get('item', '')}: "
                          f"{err.get('message', err) if isinstance(err, dict) else err}")
        elif kind == "summary":
            summary = ev
    ui.progress_done()
    return summary, errors


def run_backup(repo: restic.Repo, p: Profile) -> None:
    print()
    try:
        summary, errors = asyncio.run(_run_backup(repo, p))
    except KeyboardInterrupt:
        ui.progress_done()
        ui.warn("Backup cancelado. No se ha guardado nada.")
        return
    except restic.ResticError as e:
        ui.progress_done()
        ui.error(f"El backup falló: {e}")
        if restic.is_locked_error(e) and ui.confirm(
                "El repositorio está bloqueado. Si no hay otro backup en marcha, "
                "¿quito los bloqueos antiguos?", back=False):
            asyncio.run(restic.unlock(repo))
            ui.ok("Desbloqueado. Vuelve a lanzar el backup.")
        return
    for e in errors[:20]:
        ui.warn(e)
    if summary:
        ui.ok(f"Backup hecho (snapshot {summary.get('snapshot_id', '')[:8]})  "
              f"nuevos {summary.get('files_new', 0)} · cambiados "
              f"{summary.get('files_changed', 0)} · sin cambios "
              f"{summary.get('files_unmodified', 0)} · añadido "
              f"{human_bytes(summary.get('data_added', 0))} · "
              f"{human_time(summary.get('total_duration'))}")
    if errors:
        ui.warn(f"{len(errors)} ficheros no se pudieron leer")


def do_backup() -> None:
    last = load_last()
    c = Choice.start(last)
    done: list[restic.Repo] = []

    def review() -> None:
        p = c.p
        print("\n  " + ui.accent("Resumen"))
        print(f"    {ui.dim('Origen: ')} {p.sources[0] if p.sources else '-'}")
        print(f"    {ui.dim('Destino:')} {remote.describe(p)}\n")
        problem = dest_problem(p) or (None if p.sources else "Falta el origen")
        if problem:
            ui.error(problem)
        opts = ["Empezar backup", "Cambiar origen", "Cambiar destino", "Cancelar"]
        while True:
            i = ui.select("¿Todo bien?", opts, 1 if problem else 0)
            if i == 1:
                raise Jump("source", until="kind")
            if i == 2:
                raise Jump("kind")
            if i == 3:
                raise Abort("Cancelado")
            if problem:
                ui.error(problem)
                continue
            repo = open_repo(p, create=True)
            if repo:
                done.append(repo)
                return
            ui.warn("No se ha podido abrir el destino: revisa los datos o cancela.")

    steps = [("source", lambda: step_source(c)), ("source_path", lambda: step_source_path(c)),
             ("kind", lambda: step_kind(c)), ("dest", lambda: step_dest(c)),
             ("review", review)]
    # Con configuración previa se empieza directamente en el resumen.
    run_steps(steps, start=len(steps) - 1 if last and last.sources else 0)
    save_last(c.p)
    run_backup(done[0], c.p)


# ---------------------------------------------------------------- restaurar / limpiar
def choose_repo() -> restic.Repo:
    last = load_last()
    c = Choice.start(last)
    if last:
        i = ui.select("¿Qué repositorio?", [f"El de siempre: {remote.describe(last)}",
                                            "Otro destino"])
        if i == 0:
            repo = open_repo(last, create=False)
            if not repo:
                raise Abort("No se ha podido abrir el repositorio")
            return repo
    run_steps([("kind", lambda: step_kind(c)), ("dest", lambda: step_dest(c))])
    repo = open_repo(c.p, create=False)
    if not repo:
        raise Abort("No se ha podido abrir el repositorio")
    return repo


def do_restore() -> None:
    repo = choose_repo()
    snaps = sorted(asyncio.run(restic.snapshots(repo)),
                   key=lambda s: s.get("time", ""), reverse=True)[:30]
    if not snaps:
        ui.warn("El repositorio no tiene backups todavía.")
        return
    labels = [f"{s['time'][:16].replace('T', ' ')}  {s['short_id']}  {s.get('hostname', '')}"
              f"  {', '.join(s.get('paths', []))}" for s in snaps]
    st: dict = {"snap": 0, "target": "", "only": ""}

    def pick() -> None:
        st["snap"] = ui.select("¿Qué backup?", labels, st["snap"])

    def target() -> None:
        default = st["target"] or str(Path.home() / "restic-restore"
                                      / snaps[st["snap"]]["short_id"])
        while True:
            t = str(Path(ui.ask("¿Dónde lo restauro?", default)).expanduser().resolve())
            if t != "/":
                st["target"] = t
                return
            ui.error("No restauro sobre /")

    def only() -> None:
        st["only"] = ui.ask("¿Solo una ruta concreta? (Enter = todo)", st["only"],
                            allow_empty=True)

    def go() -> None:
        if not ui.confirm(f"Restaurar en {st['target']}? Se sobrescriben ficheros con el "
                          "mismo nombre", default=True):
            raise Abort("Cancelado")

    run_steps([("pick", pick), ("target", target), ("only", only), ("go", go)])
    ui.info("Restaurando...")
    try:
        asyncio.run(restic.restore(repo, snaps[st["snap"]]["id"], st["target"],
                                   [st["only"]] if st["only"] else []))
        ui.ok(f"Restaurado en {st['target']} (dentro está la ruta original completa)")
    except restic.ResticError as e:
        ui.error(str(e))


POLICIES = (("7 diarios, 4 semanales, 6 mensuales (recomendado)",
             {"daily": 7, "weekly": 4, "monthly": 6}),
            ("Solo los 10 últimos", {"last": 10}),
            ("Solo los 3 últimos", {"last": 3}))


def do_cleanup() -> None:
    repo = choose_repo()
    host = socket.gethostname()
    st = {"policy": 0}

    def policy() -> None:
        st["policy"] = ui.select("¿Qué conservo?", [p[0] for p in POLICIES], st["policy"])

    def preview() -> None:
        keep = POLICIES[st["policy"]][1]
        ui.info(f"Solo se tocan los backups de este equipo ({host}). Vista previa:")
        print(asyncio.run(restic.forget(repo, keep, dry_run=True, host=host)))
        if not ui.confirm("¿Borro los que sobran? No se puede deshacer"):
            raise Abort("No se ha borrado nada")

    try:
        run_steps([("policy", policy), ("preview", preview)])
        ui.info("Borrando y liberando espacio...")
        asyncio.run(restic.forget(repo, POLICIES[st["policy"]][1], prune=True, dry_run=False,
                                  host=host))
        ui.ok("Hecho")
    except restic.ResticError as e:
        ui.error(str(e))


# ---------------------------------------------------------------- main
class Abort(Exception):
    """Vuelve al menú principal mostrando el mensaje."""


def setup_logging() -> None:
    base = os.environ.get("XDG_STATE_HOME") or str(Path.home() / ".local" / "state")
    d = Path(base) / "restic-tui"
    d.mkdir(parents=True, exist_ok=True)
    os.chmod(d, 0o700)
    path = d / "restic-tui.log"
    path.touch(mode=0o600, exist_ok=True)
    logging.basicConfig(filename=path, level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")


def main() -> None:
    setup_logging()
    try:
        restic.find_restic()
    except restic.ResticError:
        sys.exit("No encuentro restic. Instálalo con: sudo apt install restic")
    print(ui.dim("Backups con restic. En cualquier pregunta: Esc o '<' vuelve atrás, "
                 "Ctrl+C sale.") + "\n")
    actions = [("Hacer backup", do_backup), ("Ver backups y restaurar", do_restore),
               ("Borrar backups antiguos", do_cleanup), ("Salir", None)]
    try:
        while True:
            try:
                fn = actions[ui.select("¿Qué quieres hacer?", [a[0] for a in actions],
                                       back=False)][1]
            except ui.Back:
                continue
            if fn is None:
                return
            try:
                fn()
            except ui.Back:
                pass  # atrás desde la primera pregunta: al menú
            except Abort as e:
                ui.warn(str(e))
            print()
    except (KeyboardInterrupt, EOFError):
        print()


if __name__ == "__main__":
    main()
