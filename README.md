# restic-tui

Asistente de terminal, pregunta a pregunta, para hacer copias de seguridad con
[restic](https://restic.net): eliges qué respaldar (una carpeta o un USB) y dónde guardarlo
(otro ordenador de tu red por SSH, un disco/USB o un rest-server), y él se encarga del resto.

```
? ¿Qué quieres hacer?  Hacer backup

  Resumen
    Origen:  /media/usuario/FOTOS
    Destino: sftp://usuario@nas:22//srv/restic/portatil

? ¿Todo bien?  (↑/↓ y Enter, Esc = atrás)
  > Empezar backup
    Cambiar origen
    Cambiar destino
    Cancelar

  Comprobando acceso SSH...
✔ Conexión SSH correcta con clave
✔ Repositorio encontrado
? Contraseña del repositorio:
[##############......]  70%  1.3 GiB/1.9 GiB  35.2 MiB/s  quedan 0:18
✔ Backup hecho (snapshot a1b2c3d4)  nuevos 12 · cambiados 3 · sin cambios 840 · añadido 120.4 MiB · 0:42
```

## Índice

- [Características](#características)
- [Requisitos](#requisitos)
- [Instalación](#instalación)
- [Uso](#uso)
  - [Moverse por las preguntas](#moverse-por-las-preguntas)
  - [Hacer un backup](#hacer-un-backup)
  - [Ver backups y restaurar](#ver-backups-y-restaurar)
  - [Borrar backups antiguos](#borrar-backups-antiguos)
- [Destinos](#destinos)
  - [Otro equipo por SSH](#otro-equipo-por-ssh-recomendado)
  - [USB o carpeta local](#usb-o-carpeta-local)
  - [rest-server](#rest-server)
- [Contraseñas y seguridad](#contraseñas-y-seguridad)
- [Ficheros que usa](#ficheros-que-usa)
- [Solución de problemas](#solución-de-problemas)
- [Desarrollo](#desarrollo)
- [Licencia](#licencia)

## Características

- **Guiado**: una pregunta cada vez, con valores por defecto sensatos.
- **Se puede volver atrás** en cualquier pregunta, y antes de empezar se muestra un
  **resumen** desde el que cambiar el origen o el destino sin repetir todo.
- **Recuerda la última configuración**: la siguiente vez empieza directamente en el resumen;
  basta con Enter y la contraseña.
- **Orígenes**: detecta los USB/discos conectados, tu carpeta personal o cualquier ruta.
- **Destinos**: otro ordenador por SSH (nombre o IP), USB/carpeta local o
  [rest-server](https://github.com/restic/rest-server).
- **Crea el repositorio** si no existe (contraseña generada o escrita por ti) o **continúa el
  existente** con backups incrementales: solo se sube lo que ha cambiado.
- **Configura el acceso SSH por clave** si todavía no lo tienes (`ssh-keygen` + `ssh-copy-id`).
- Barra de progreso con velocidad y tiempo restante; `Ctrl+C` cancela sin dejar el
  repositorio bloqueado.
- **Restaurar** un backup completo o una ruta concreta.
- **Borrar backups antiguos** con políticas predefinidas, vista previa y confirmación.
- Colores discretos que usan la paleta de tu terminal (y `NO_COLOR` para quitarlos).
- **Sin dependencias**: solo Python y restic.

## Requisitos

- Linux (probado en Ubuntu) u otro sistema POSIX.
- Python ≥ 3.11.
- [restic](https://restic.readthedocs.io/en/stable/020_installation.html) ≥ 0.14:
  ```bash
  sudo apt install restic
  ```
- Para destinos SSH: `ssh`, `ssh-keygen` y `ssh-copy-id` (paquete `openssh-client`).

## Instalación

```bash
git clone https://github.com/nicolasdavidgilbert/restic-tui.git
cd restic-tui
python3 -m venv .venv
.venv/bin/pip install -e .
.venv/bin/backup
```

Para lanzarlo desde cualquier sitio escribiendo solo `backup`, añade un alias a tu
`~/.bashrc` o `~/.zshrc`:

```bash
alias backup="$HOME/ruta/a/restic-tui/.venv/bin/backup"
```

También funciona `python -m restic_tui` y el comando `restic-tui`.

## Uso

Al arrancar aparece el menú principal:

```
? ¿Qué quieres hacer?
  > Hacer backup
    Ver backups y restaurar
    Borrar backups antiguos
    Salir
```

### Moverse por las preguntas

| Acción | Tecla |
|---|---|
| Moverse por un menú | `↑` / `↓` (o `k` / `j`), o el número de la opción |
| Elegir | `Enter` |
| Volver a la pregunta anterior en un menú | `Esc`, `←` o `Retroceso` |
| Volver atrás al escribir | escribe `<` y pulsa `Enter` |
| Aceptar el valor por defecto `[...]` | `Enter` sin escribir nada |
| Salir | `Ctrl+C` |

Al volver atrás, lo que ya habías respondido queda como valor por defecto.

### Hacer un backup

1. **¿Qué quieres respaldar?** Un USB detectado, tu carpeta personal, la de la última vez u
   *Otra ruta...*.
2. **¿Dónde lo guardo?** Otro equipo (SSH), USB/carpeta local o rest-server, y sus datos.
3. **Resumen.** Desde aquí puedes:
   - *Empezar backup*.
   - *Cambiar origen* o *Cambiar destino*: pregunta solo eso y vuelve al resumen.
   - *Cancelar*.
4. Al empezar:
   - Si el destino es SSH, comprueba el acceso (y ofrece configurarlo, ver abajo).
   - Si **no hay repositorio**, ofrece crearlo y te pide la contraseña nueva: puedes
     generarla o escribirla. No continúa hasta que confirmas que la has guardado.
   - Si **ya existe**, pide su contraseña (3 intentos) y hace un backup incremental.

La configuración (sin contraseñas) se guarda, así que la próxima vez se empieza en el resumen.

### Ver backups y restaurar

1. Elige el repositorio (*el de siempre* u otro destino) e introduce la contraseña.
2. Elige el backup de la lista (los más recientes primero).
3. Indica dónde restaurarlo (por defecto `~/restic-restore/<id>`). restic recrea dentro la
   ruta original completa, por ejemplo `~/restic-restore/a1b2c3d4/home/usuario/Fotos/...`.
4. Opcionalmente, restaura solo una ruta concreta (por ejemplo `/home/usuario/Fotos/2024`).

No se permite restaurar sobre `/`.

### Borrar backups antiguos

Elige qué conservar:

- 7 diarios, 4 semanales y 6 mensuales (recomendado)
- Solo los 10 últimos
- Solo los 3 últimos

Se muestra una **vista previa** de lo que se borraría y hay que confirmarlo. Después se
ejecuta `restic forget --prune`, que libera el espacio. **Solo se tocan los backups de este
equipo**: si varios ordenadores comparten repositorio, los de los demás no se borran.

## Destinos

### Otro equipo por SSH (recomendado)

No hay que instalar nada en el otro equipo aparte del servidor SSH (`openssh-server`).
Te pedirá:

| Pregunta | Ejemplo |
|---|---|
| Equipo (nombre o IP) | `nas`, `nas.local`, `192.168.1.20` o un nombre de Tailscale |
| Usuario en ese equipo | `usuario` |
| Puerto SSH | `22` |
| Carpeta del repositorio | `/srv/restic/portatil` (absoluta) o `backups` (relativa a su `$HOME`) |

**Acceso por clave.** restic necesita entrar sin contraseña. Si aún no tienes acceso por
clave, el asistente lo ofrece:

1. Crea `~/.ssh/id_ed25519` si no existe.
2. Copia **solo esa clave** al otro equipo con `ssh-copy-id`; te pide la contraseña del otro
   equipo una vez.

A partir de ahí, tanto la comprobación como restic usan solo esa clave (`IdentitiesOnly=yes`).
Así se evita ofrecer todas las claves de tu agente SSH, que provoca fallos en cadena y puede
hacer que el otro equipo te bloquee temporalmente.

**Dónde guardar los backups en el otro equipo.** Lo habitual en Linux es `/srv`, con una
subcarpeta por ordenador y un usuario dedicado:

```bash
# En el equipo que recibe los backups
sudo adduser restic
sudo mkdir -p /srv/restic
sudo chown restic: /srv/restic
```

Evita `/var/backups` (es del sistema) y el usuario `backup` que trae Ubuntu (no tiene shell).

### USB o carpeta local

Elige un USB detectado (propone `<USB>/restic-repo`) o escribe cualquier ruta. Ubuntu
monta los USB en `/media/<usuario>/<ETIQUETA>`. El repositorio no puede estar dentro de lo
que respaldas; si eliges un USB como origen, no aparece como destino.

### rest-server

[rest-server](https://github.com/restic/rest-server) es un pequeño servidor HTTP del propio
proyecto restic. Su ventaja es el modo `--append-only`: desde el cliente se pueden añadir
backups pero no borrarlos, lo que protege frente a ransomware.

No está en los repositorios de Ubuntu: descarga el binario de
[sus releases](https://github.com/restic/rest-server/releases) o usa Docker
(`restic/rest-server`). Después, en el otro equipo:

```bash
sudo apt install apache2-utils          # para htpasswd
sudo mkdir -p /srv/restic
sudo htpasswd -B -c /srv/restic/.htpasswd usuario
rest-server --path /srv/restic --listen :8000 --append-only
```

En el asistente pon la URL **sin credenciales** (`http://nas:8000/portatil`) y el usuario
aparte; la contraseña de rest-server se pide al usarlo. Con `--append-only`, *Borrar backups
antiguos* no funcionará desde el cliente: hay que hacerlo en el servidor.

## Contraseñas y seguridad

- **Si pierdes la contraseña del repositorio, los datos son irrecuperables.** Guárdala en un
  gestor de contraseñas (KeePassXC, Bitwarden...).
- Las contraseñas **no se guardan nunca** en disco. Se pasan a restic por variable de entorno
  del proceso hijo, nunca como argumento (no aparecen en `ps`).
- La contraseña de rest-server tampoco se guarda; la URL guardada no lleva credenciales.
- La configuración y el log se crean con permisos solo para tu usuario.
- En el log, las URLs con credenciales se enmascaran.
- Los datos de host y usuario SSH se validan para evitar la inyección de opciones en `ssh`.

## Ficheros que usa

| Fichero | Contenido |
|---|---|
| `~/.config/restic-tui/last.json` | Último origen y destino (sin contraseñas), permisos `600` |
| `~/.local/state/restic-tui/restic-tui.log` | Comandos de restic ejecutados y errores, sin secretos |
| `~/.ssh/id_ed25519` | Clave SSH que se crea si no tenías ninguna |

Respetan `XDG_CONFIG_HOME` y `XDG_STATE_HOME`. Para empezar de cero, borra `last.json`.

Variables de entorno:

| Variable | Efecto |
|---|---|
| `NO_COLOR=1` | Sin colores |
| `TERM=dumb` | Sin colores |

## Solución de problemas

**"Sin acceso por clave todavía"**: acepta la opción de configurar el acceso SSH, o hazlo a
mano con `ssh-copy-id -i ~/.ssh/id_ed25519.pub usuario@equipo`.

**"El equipo corta la conexión antes de pedir credenciales"**
(`kex_exchange_identification: Connection reset by peer`): el otro equipo te ha bloqueado
temporalmente tras varios intentos fallidos (fail2ban o el límite de sshd). Espera unos
minutos y vuelve a probar. En el otro equipo puedes revisarlo con
`sudo fail2ban-client status sshd`.

**"No encuentro ese nombre de equipo en la red"**: prueba con la IP, o con `nombre.local`
si usas mDNS/Avahi.

**"El equipo no acepta conexiones SSH"**: el equipo está apagado o no tiene `openssh-server`.

**`permission denied` al crear el repositorio** (por ejemplo en `/srv/...`, que es de
root): el asistente ofrece arreglarlo con sudo. Te enseña el comando y te pide la contraseña
de sudo, en local o en el otro equipo por SSH. Crea la carpeta y la pone a tu nombre;
restic sigue funcionando sin sudo, así que el repositorio no queda a nombre de root. A mano
sería `sudo mkdir -p /ruta && sudo chown usuario: /ruta`.

**"El repositorio está bloqueado"**: suele quedar así si un backup se cortó de golpe. Si no
hay otro backup en marcha, el asistente ofrece quitar los bloqueos antiguos (`restic unlock`).

**"N ficheros no se pudieron leer"**: normalmente son ficheros sin permiso de lectura. El
backup se guarda igualmente con el resto; la lista aparece encima del resumen.

## Desarrollo

```bash
.venv/bin/pip install -e '.[dev]'
.venv/bin/pytest -q            # usa restic real sobre repositorios temporales
.venv/bin/ruff check src tests
```

Estructura:

```
src/restic_tui/
  cli.py          flujo del asistente: pasos, resumen, backup, restaurar, limpiar
  ui.py           menús con flechas, preguntas, volver atrás, colores, barra de progreso
  core/
    restic.py     wrapper async de restic (init, backup --json, snapshots, restore, forget...)
    remote.py     URLs de repositorio, validación, pruebas SSH/rest-server, ssh-copy-id
    devices.py    detección de USB/discos montados (lsblk)
    profiles.py   última configuración usada (JSON, sin contraseñas)
    passwords.py  generación y validación de contraseñas
tests/
```

El flujo se define como una lista de pasos (`run_steps` en `cli.py`). Cada paso puede lanzar
`ui.Back` para volver al anterior o `Jump` para saltar a otro. Los pasos que no preguntan
nada devuelven `SKIP` y se saltan también al ir hacia atrás.

## Licencia

[MIT](LICENSE)
