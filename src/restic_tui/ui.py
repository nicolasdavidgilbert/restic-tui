"""Preguntas en terminal: menús con flechas, texto, contraseña y progreso.

Colores: solo los 16 ANSI básicos (más negrita/atenuado), que cada terminal pinta con la
paleta de su tema; se desactivan sin TTY, con NO_COLOR o con TERM=dumb.

En cualquier pregunta se puede volver atrás: Esc o ← en los menús, '<' al escribir.
Eso lanza `Back`, que el flujo captura para repetir la pregunta anterior.
Solo librería estándar. Sin terminal interactiva los menús pasan a ser numerados.
"""
from __future__ import annotations

import getpass
import os
import select as _select
import shutil
import sys

try:
    import termios
    import tty
except ImportError:  # pragma: no cover - no POSIX
    termios = tty = None

BACK = "<"


class Back(Exception):
    """El usuario quiere volver a la pregunta anterior."""


def _interactive() -> bool:
    return termios is not None and sys.stdin.isatty() and sys.stdout.isatty()


def _use_color() -> bool:
    return (sys.stdout.isatty() and "NO_COLOR" not in os.environ
            and os.environ.get("TERM") != "dumb")


def _c(code: str, text: str) -> str:
    return f"\x1b[{code}m{text}\x1b[0m" if _use_color() else text


def dim(text: str) -> str:
    return _c("2", text)


def accent(text: str) -> str:
    return _c("36", text)


def _q(question: str) -> str:
    return f"{accent('?')} {_c('1', question)}"


def ok(msg: str) -> None:
    print(_c("32", f"✔ {msg}"))


def warn(msg: str) -> None:
    print(_c("33", f"! {msg}"))


def error(msg: str) -> None:
    print(_c("31", f"✘ {msg}"))


def info(msg: str) -> None:
    print(dim(f"  {msg}"))


def _read_key(fd: int) -> str:
    b = os.read(fd, 1)
    # Esc solo o inicio de una secuencia (flechas)
    if b == b"\x1b" and _select.select([fd], [], [], 0.05)[0]:
        b += os.read(fd, 2)
    return {b"\x1b[A": "up", b"\x1b[B": "down", b"\x1b[D": "back", b"\x1b": "back",
            b"\x7f": "back", b"\r": "enter", b"\n": "enter",
            b"k": "up", b"j": "down"}.get(b, b.decode(errors="ignore"))


def select(question: str, options: list[str], default: int = 0, back: bool = True) -> int:
    """Devuelve el índice elegido. Esc / ← / Retroceso -> Back."""
    if not options:
        raise ValueError("select() sin opciones")
    default = min(max(default, 0), len(options) - 1)
    if not _interactive():
        print(_q(question))
        for i, o in enumerate(options, 1):
            print(f"  {i}) {o}")
        while True:
            ans = input(f"  Número [{default + 1}]{' (< atrás)' if back else ''}: ").strip()
            if back and ans == BACK:
                raise Back
            if not ans:
                return default
            if ans.isdigit() and 1 <= int(ans) <= len(options):
                return int(ans) - 1

    width = shutil.get_terminal_size().columns - 4
    idx = default
    hint = "↑/↓ y Enter" + (", Esc = atrás" if back else "")
    print(f"{_q(question)}  {dim(f'({hint})')}")

    def draw(first: bool) -> None:
        if not first:
            sys.stdout.write(f"\x1b[{len(options)}A")
        for i, o in enumerate(options):
            line = accent(f"  > {o[:width]}") if i == idx else f"    {o[:width]}"
            sys.stdout.write("\x1b[2K" + line + "\n")
        sys.stdout.flush()

    fd = sys.stdin.fileno()
    old = termios.tcgetattr(fd)
    sys.stdout.write("\x1b[?25l")
    chosen: int | None = None
    try:
        tty.setcbreak(fd)  # tecla a tecla, sin eco; Ctrl+C sigue funcionando
        draw(True)
        while chosen is None:
            key = _read_key(fd)
            if key == "back" and back:
                break
            if key == "up":
                idx = (idx - 1) % len(options)
            elif key == "down":
                idx = (idx + 1) % len(options)
            elif key == "enter":
                chosen = idx
            elif key.isdigit() and 1 <= int(key) <= len(options):
                chosen = idx = int(key) - 1
            draw(False)
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, old)
        sys.stdout.write("\x1b[?25h")
    # Colapsa el menú a una línea con la respuesta.
    sys.stdout.write(f"\x1b[{len(options) + 1}A\x1b[J")
    if chosen is None:
        print(f"{_q(question)}  {dim('(atrás)')}")
        raise Back
    print(f"{_q(question)}  {accent(options[chosen])}")
    return chosen


def ask(question: str, default: str = "", allow_empty: bool = False,
        back: bool = True) -> str:
    hint = " " + dim(f"[{default}]") if default else ""
    while True:
        ans = input(f"{_q(question)}{hint} ").strip()
        if back and ans == BACK:
            raise Back
        if ans or default or allow_empty:
            return ans or default


def ask_password(question: str) -> str:
    while True:
        pw = getpass.getpass(f"{_q(question)} ")
        if pw:
            return pw


def confirm(question: str, default: bool = False, back: bool = True) -> bool:
    hint = "S/n" if default else "s/N"
    while True:
        ans = input(f"{_q(question)} {dim(f'[{hint}]')} ").strip().lower()
        if back and ans == BACK:
            raise Back
        if not ans:
            return default
        if ans in ("s", "si", "sí", "y", "yes"):
            return True
        if ans in ("n", "no"):
            return False


def progress(fraction: float, text: str) -> None:
    """Pinta una barra en la misma línea."""
    cols = shutil.get_terminal_size().columns
    bar_w = 20
    filled = int(bar_w * max(0.0, min(1.0, fraction)))
    text = f" {fraction * 100:3.0f}%  {text}"[: max(0, cols - bar_w - 3)]
    bar = _c("32", "#" * filled) + dim("." * (bar_w - filled))
    sys.stdout.write(f"\r\x1b[2K[{bar}]{text}")
    sys.stdout.flush()


def progress_done() -> None:
    sys.stdout.write("\r\x1b[2K")
    sys.stdout.flush()
