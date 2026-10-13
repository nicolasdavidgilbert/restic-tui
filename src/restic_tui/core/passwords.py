"""Contraseñas de repositorio: generación y validación. Nunca se guardan en disco."""
from __future__ import annotations

import secrets as _secrets

MIN_LEN = 10


def generate_password() -> str:
    return _secrets.token_urlsafe(24)


def validate_new_password(pw: str, confirm: str) -> str | None:
    """Devuelve un mensaje de error o None si es válida."""
    if pw != confirm:
        return "Las contraseñas no coinciden"
    if len(pw) < MIN_LEN:
        return f"Mínimo {MIN_LEN} caracteres"
    return None
