# Permisos: estrategias para más adelante

Notas para decidir cómo tratar los problemas de permisos en restic-tui. Nada de esto está
implementado salvo lo marcado como **(actual)**.

Principio que no cambia: **restic nunca se ejecuta como root**. Si lo hiciera, el repositorio
quedaría a nombre de root y cada backup posterior necesitaría sudo.

---

## 1. Dónde aparecen problemas de permisos

| Sitio | Síntoma típico | Ejemplo |
|---|---|---|
| Destino local | `mkdir …/data: permission denied` al crear el repo | `/srv/backups/T7` es de root |
| Destino SSH | Lo mismo, pero en el otro equipo | `/srv/backups` remoto de root |
| Destino USB (exFAT/NTFS/FAT32) | `chown` falla con `Operation not permitted`; los permisos los fija el montaje | Disco formateado en Windows |
| Origen | restic avisa `permission denied` al leer algunos archivos y el backup sale incompleto | Copiar `/etc`, `/home` de otros usuarios |
| Repo existente | Se puede leer pero no escribir (lock, nuevos packs) | Repo creado antes con `sudo restic` |
| Restauración | Los archivos restaurados quedan a nombre del usuario, no del dueño original | Restaurar un sistema completo |
| Borrar el repo a mano | `rm` se queja: restic crea los archivos de datos en solo lectura | Limpiar pruebas |

---

## 2. Estrategias para el destino

### 2.1 sudo mkdir + chown tras el fallo **(actual)**
Al fallar `init` con "permission denied", se enseña el comando, se pide confirmación y se ejecuta
`sudo mkdir -p RUTA && sudo chown USUARIO: RUTA` (en local, o en el otro equipo con `ssh -t`).
Después se reintenta `init` con la misma contraseña.

- ✔ Sencillo; sudo solo se usa una vez.
- ✘ El usuario ya ha elegido la contraseña antes de enterarse del problema.
- ✘ **Falla en USB exFAT/NTFS/FAT32**, porque `chown` no está soportado ahí (ver 2.6).
- ✘ Si la carpeta padre es compartida, puede que no convenga cambiar el dueño.

### 2.2 Comprobar antes de preguntar la contraseña
Antes de crear el repo, comprobar si se puede escribir:
- En local: `os.access(ruta_o_primer_padre_existente, os.W_OK)`.
- Por SSH: `ssh … 'test -w RUTA || test -w PADRE'`.

Si no se puede, ofrecer el arreglo en ese momento, antes de pedir la contraseña.

- ✔ El flujo es más natural y no hace falta reintentar.
- ✘ Supone una conexión SSH más. `os.access` puede engañar con ACLs o montajes `ro`, así que
  conviene mantener también 2.1 como red de seguridad.

### 2.3 Grupo compartido en vez de cambiar el dueño
Útil cuando varios usuarios o equipos escriben en la misma carpeta:
```sh
sudo groupadd -f backup
sudo usermod -aG backup USUARIO        # requiere volver a iniciar sesión
sudo mkdir -p RUTA && sudo chgrp backup RUTA && sudo chmod 2770 RUTA   # setgid
```
restic hace los archivos legibles por el grupo si el `config` del repo lo es. Comprobar con la
versión instalada antes de depender de ello.

- ✔ No se quita la carpeta a root ni a otros usuarios.
- ✘ Más pasos, y el cambio de grupo no se aplica hasta volver a iniciar sesión, lo que
  confunde. Mejor como opción "avanzada".

### 2.4 ACLs
```sh
sudo setfacl -m u:USUARIO:rwx RUTA
sudo setfacl -d -m u:USUARIO:rwx RUTA   # por defecto para lo que se cree dentro
```
- ✔ Muy fino: el dueño no cambia.
- ✘ Depende del sistema de archivos y de que esté instalado `acl`. Es poco visible para el usuario.

### 2.5 Usuario dedicado en el equipo remoto (SSH)
Crear en el servidor un usuario `restic` (o `backup`) que sea dueño de `/srv/backups` y entrar
siempre con él. Opcionalmente, limitarlo a SFTP:
```
# /etc/ssh/sshd_config
Match User restic
    ForceCommand internal-sftp
    AllowTcpForwarding no
    X11Forwarding no
```
y/o restringir la clave en `authorized_keys` (`restrict,…`).

- ✔ Es lo más limpio para un servidor de backups. Si te roban la clave, solo se puede tocar el
  repo y no el resto del equipo.
- ✘ Hay que configurar el servidor. Se podría generar con un asistente que muestre los comandos,
  pero sin ejecutarlos automáticamente.

### 2.6 USB con exFAT / NTFS / FAT32
Estos sistemas de archivos no guardan dueño ni permisos Unix: los decide el montaje
(`uid=`, `gid=`, `umask=`). Si el usuario no puede escribir:
- Detectar el tipo con `lsblk -o FSTYPE` (ya usamos lsblk en `core/devices.py`).
- **No ofrecer `chown`**. Ofrecer volver a montar con el uid del usuario:
  `udisksctl unmount -b DEV && udisksctl mount -b DEV` (normalmente lo monta a su nombre, sin sudo),
  o `sudo mount -o remount,uid=$(id -u),gid=$(id -g) …`.
- Avisar de que restic funciona bien en exFAT, pero que FAT32 limita los archivos a 4 GB
  (restic usa packs pequeños, así que suele dar igual).

### 2.7 rest-server
Ahí los permisos los gestiona el servidor y el cliente no puede arreglarlos. Basta con explicar
el error (401/403 → usuario y contraseña de `.htpasswd`; 500 → permisos en el `--path` del servidor).
Opciones interesantes:
- `--append-only`: protege contra ransomware, porque el cliente no puede borrar. Entonces
  `forget`/`prune` hay que hacerlos en el servidor; la limpieza de la app debería avisarlo.
- `--private-repos`: cada usuario solo ve su repo.

### 2.8 pkexec / polkit en lugar de sudo
Muestra el diálogo gráfico de contraseña. Solo tiene sentido si algún día hay interfaz gráfica.
Para la línea de comandos, sudo es lo esperado.

### 2.9 Regla sudoers limitada
Para backups programados (cron/systemd) sin nadie delante:
```
USUARIO ALL=(root) NOPASSWD: /usr/bin/mkdir -p /srv/backups/*, /usr/bin/chown USUARIO\: /srv/backups/*
```
- ✘ Es fácil equivocarse y abrir demasiado. Mejor resolverlo una vez con 2.1 o 2.5 y no
  necesitar sudo después.
- Para detectar si sudo pedirá contraseña: `sudo -n true`.

---

## 3. Estrategias para el origen (archivos que no se pueden leer)

### 3.1 Avisar y seguir **(lo que hace restic por defecto)**
restic continúa y termina con código 3 ("snapshot creado, pero faltan archivos"). Mejora pendiente:
- Recoger los mensajes `error` del `--json` y mostrar al final:
  "N archivos no se pudieron leer" y los primeros 5 de la lista.
- No tratar el código 3 como fallo total.

### 3.2 Capacidad `cap_dac_read_search` en el binario de restic
Es lo que recomienda la documentación de restic para hacer backup de todo el sistema sin root:
```sh
sudo setcap cap_dac_read_search=+ep "$(command -v restic)"
```
- ✔ restic puede leerlo todo pero sigue siendo tu usuario, así que el repo queda a tu nombre.
- ✘ Cualquiera que pueda ejecutar ese binario puede leer cualquier archivo. Lo recomendable es
  una copia de restic solo ejecutable por el usuario o un grupo (`chmod 750`).
  Además, se pierde la capacidad al actualizar restic con apt.
- Ofrecerlo solo si el origen es una ruta del sistema y hubo errores de lectura.

### 3.3 sudo restic con `--cache-dir` del usuario
Evitarlo: los archivos nuevos del repo serían de root (salvo con rest-server o SFTP, donde el
dueño lo pone el servidor). Solo si no queda otra y avisando claramente.

### 3.4 Excluir lo que no se puede leer
Ofrecer añadir a exclusiones las rutas que fallaron, por ejemplo cachés o `lost+found`. Habría
que guardarlo en el perfil (`Profile.excludes`, todavía no existe).

---

## 4. Otros detalles

- **Restaurar**: sin root, los archivos restaurados son del usuario que restaura. Para restaurar
  un sistema con dueños originales hace falta `sudo restic restore`. Avisar si el destino de la
  restauración es una ruta del sistema.
- **Borrar un repo**: los packs se crean en solo lectura, así que borrarlo con `rm -r` pide
  confirmación por cada uno. Si se añade "eliminar repositorio", usar `chmod -R u+w` antes.
- **Repo creado antes con sudo**: detectar si el dueño de `RUTA/config` no es el usuario y
  ofrecer `sudo chown -R USUARIO: RUTA` (es la misma lógica que 2.1, pero recursiva).
- **Configuración local**: `~/.config/restic-tui/last.json` ya se guarda con 0600. Si algún día se
  guardan contraseñas, que sea en un archivo 0600 aparte o en el llavero del sistema
  (`secret-tool`), nunca en el perfil.

---

## 5. Orden propuesto

1. **2.6** Detectar exFAT/NTFS/FAT32 y no ofrecer `chown` ahí. Ahora mismo es un fallo real en USB.
2. **3.1** Resumen de archivos no leídos y tratar el código 3 como aviso.
3. **2.2** Comprobar la escritura antes de pedir la contraseña.
4. **Repo creado con sudo**: `chown -R` (sección 4).
5. **2.5** Asistente opcional para "preparar un servidor de backups" (usuario dedicado + solo SFTP).
6. **3.2** Ofrecer `setcap` cuando se hagan backups de rutas del sistema.
7. El resto (grupos, ACLs, sudoers, pkexec), solo si alguien lo pide.
