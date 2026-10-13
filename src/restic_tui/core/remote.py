"""Construcción de URLs de repo y pruebas de conectividad (SFTP / rest-server)."""
from __future__ import annotations

import asyncio
import base64
import re
import subprocess
import urllib.error
import urllib.request
from pathlib import Path
from urllib.parse import quote, urlsplit, urlunsplit

from .profiles import Profile

HOST_RE = re.compile(r"^[A-Za-z0-9._:\[\]-]+$")
USER_RE = re.compile(r"^[A-Za-z0-9._][A-Za-z0-9._-]*$")


class ConfigError(ValueError):
    pass


def validate_ssh(user: str, host: str, port: int) -> None:
    if not host or host.startswith("-") or not HOST_RE.match(host):
        raise ConfigError("Host/IP no válido")
    if not user or not USER_RE.match(user):
        raise ConfigError("Usuario no válido")
    if not 1 <= int(port) <= 65535:
        raise ConfigError("Puerto no válido")


def validate_rest_url(url: str) -> None:
    parts = urlsplit(url)
    if parts.scheme not in ("http", "https") or not parts.hostname:
        raise ConfigError("URL rest-server debe ser http(s)://host:puerto/repo")
    if parts.username or parts.password:
        raise ConfigError("No pongas usuario/contraseña en la URL; usa el campo usuario")


def repo_url(p: Profile, rest_password: str = "") -> str:
    """URL de restic. La contraseña de rest-server solo vive en memoria."""
    if p.dest_type == "local":
        if not p.local_path:
            raise ConfigError("Falta la ruta local del repositorio")
        return str(Path(p.local_path).expanduser())
    if p.dest_type == "sftp":
        validate_ssh(p.sftp_user, p.sftp_host, p.sftp_port)
        if not p.sftp_path:
            raise ConfigError("Falta la ruta remota")
        host = f"[{p.sftp_host}]" if ":" in p.sftp_host and not p.sftp_host.startswith("[") \
            else p.sftp_host
        # sftp://user@host:port//abs  (absoluta)  |  sftp://user@host:port/rel  (relativa a $HOME)
        return f"sftp://{p.sftp_user}@{host}:{p.sftp_port}/{p.sftp_path}"
    if p.dest_type == "rest":
        validate_rest_url(p.rest_url)
        parts = urlsplit(p.rest_url)
        netloc = parts.netloc
        if p.rest_user:
            cred = quote(p.rest_user, safe="")
            if rest_password:
                cred += ":" + quote(rest_password, safe="")
            netloc = f"{cred}@{netloc}"
        return "rest:" + urlunsplit((parts.scheme, netloc, parts.path, "", ""))
    raise ConfigError(f"Tipo de destino desconocido: {p.dest_type}")


def describe(p: Profile) -> str:
    try:
        return repo_url(p).replace("rest:", "rest-server ")
    except ConfigError as e:
        return f"(destino incompleto: {e})"


def local_pubkey() -> Path | None:
    for name in ("id_ed25519.pub", "id_ecdsa.pub", "id_rsa.pub"):
        p = Path.home() / ".ssh" / name
        if p.exists():
            return p
    return None


def ssh_opts() -> list[str]:
    """Usa solo nuestra clave. Ofrecer todas las del agente provoca fallos de
    autenticación en cadena, y fail2ban/MaxAuthTries acaban bloqueando la IP."""
    pub = local_pubkey()
    if pub is None:
        return []
    return ["-i", str(pub.with_suffix("")), "-o", "IdentitiesOnly=yes"]


def restic_args(p: Profile) -> list[str]:
    """Opciones globales de restic para este destino."""
    if p.dest_type == "sftp" and (opts := ssh_opts()):
        return ["-o", "sftp.args=" + " ".join(opts)]
    return []


def _explain_ssh_error(msg: str, rc: int | None) -> str:
    if "Host key verification failed" in msg:
        return "Equipo desconocido todavía: hay que aceptar su huella (configurar acceso SSH)"
    if "Permission denied" in msg:
        return "Sin acceso por clave todavía (configurar acceso SSH)"
    if ("reset by peer" in msg or "Connection closed" in msg or "timed out" in msg
            or "kex_exchange_identification" in msg):
        return ("El equipo corta la conexión antes de pedir credenciales. Suele ser "
                "fail2ban o el límite de intentos de sshd tras varios fallos: espera unos "
                "minutos y vuelve a probar (o revisa 'sudo fail2ban-client status sshd' allí)")
    if "Could not resolve" in msg:
        return "No encuentro ese nombre de equipo en la red"
    if "No route" in msg or "refused" in msg:
        return "El equipo no acepta conexiones SSH (¿apagado o sin servidor SSH?)"
    return msg or f"ssh terminó con código {rc}"


async def test_ssh(user: str, host: str, port: int) -> tuple[bool, str]:
    validate_ssh(user, host, port)
    proc = await asyncio.create_subprocess_exec(
        "ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=8", *ssh_opts(),
        "-p", str(port), f"{user}@{host}", "true",
        stdin=asyncio.subprocess.DEVNULL, stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.PIPE)
    _, err = await proc.communicate()
    if proc.returncode == 0:
        return True, "Conexión SSH correcta con clave"
    return False, _explain_ssh_error(err.decode(errors="replace").strip(), proc.returncode)


def setup_ssh_interactive(user: str, host: str, port: int) -> int:
    """Interactivo: crea la clave si falta y copia SOLO esa clave al otro equipo."""
    validate_ssh(user, host, port)
    if local_pubkey() is None:
        print("No tienes clave SSH; se creará ~/.ssh/id_ed25519")
        rc = subprocess.call(["ssh-keygen", "-t", "ed25519", "-f",
                              str(Path.home() / ".ssh" / "id_ed25519")])
        if rc:
            return rc
    pub = local_pubkey()
    print(f"Copiando {pub} a {user}@{host}:{port} (te pedirá su contraseña una vez)…")
    return subprocess.call(["ssh-copy-id", "-i", str(pub), "-o", "IdentitiesOnly=yes",
                            "-p", str(port), f"{user}@{host}"])


def _rest_check(url: str, user: str, password: str) -> tuple[bool, str]:
    req = urllib.request.Request(url.rstrip("/") + "/config", method="HEAD")
    if user:
        token = base64.b64encode(f"{user}:{password}".encode()).decode()
        req.add_header("Authorization", f"Basic {token}")
    try:
        with urllib.request.urlopen(req, timeout=8):
            return True, "rest-server accesible; el repositorio ya existe"
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return True, "rest-server accesible; el repositorio aún no existe"
        if e.code in (401, 403):
            return False, "Usuario o contraseña de rest-server incorrectos"
        return False, f"HTTP {e.code}"
    except (urllib.error.URLError, OSError) as e:
        return False, f"No se pudo conectar: {getattr(e, 'reason', e)}"


async def test_rest(url: str, user: str, password: str) -> tuple[bool, str]:
    validate_rest_url(url)
    return await asyncio.to_thread(_rest_check, url, user, password)
