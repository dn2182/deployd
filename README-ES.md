# deployd

[![CI](https://github.com/dn2182/deployd/actions/workflows/ci.yml/badge.svg)](https://github.com/dn2182/deployd/actions/workflows/ci.yml)
[![License](https://img.shields.io/github/license/dn2182/deployd)](LICENSE)

*Read it in [English](README.md).*

**deployd publica las nuevas versiones de tus sitios web y apps en tus propios
servidores, de forma automática y segura, sin darle a nadie acceso remoto a
ellos.**

Un desarrollador sube código a GitHub. GitHub lo compila y le avisa a deployd
que hay una versión nueva. deployd, un programa pequeño que corre en tu
servidor, descarga esa versión, comprueba que es exactamente lo que se
compiló, cambia la app activa en un instante y confirma que está sana. Si la
versión nueva no levanta sana, deployd vuelve a poner la anterior por sí solo.

Lo que obtienes:

- **Sin accesos remotos para desplegar.** GitHub nunca recibe acceso SSH ni
  FTP. El servidor recoge la versión nueva por sí mismo.
- **Nada que compilar en el servidor.** Sin compiladores, sin Docker, sin
  herramientas extra en producción.
- **Seguro por defecto.** Cada versión se verifica antes de correr, el cambio
  es instantáneo y un release fallido hace rollback solo.
- **Una sola página web para manejarlo.** Agrega apps, mira los deploys,
  vuelve a una versión anterior, pausa los deploys, edita el archivo de
  ajustes de una app.
- **Fácil de alojar.** Un comando lo instala en un servidor Ubuntu. No
  necesita servidor de base de datos ni otros servicios.

> **Estado:** estable y en uso en producción. deployd publica aplicaciones
> reales de clientes en varios servidores de producción, y cada cambio se
> prueba con una instalación, actualización y desinstalación completas en
> Ubuntu 24.04. La instalación en Windows está documentada pero aún no se ha
> probado en un servidor real.

## Contenido

1. [Cómo funciona](#cómo-funciona)
2. [Instalación en Ubuntu](#instalación-en-ubuntu)
3. [Registrar una aplicación](#registrar-una-aplicación)
4. [Conectar GitHub Actions](#conectar-github-actions)
5. [Salir a producción: conectar el sitio web](#salir-a-producción-conectar-el-sitio-web)
6. [Ajustes de la app: archivos .env](#ajustes-de-la-app-archivos-env)
7. [Operación](#operación)
8. [Migraciones de base de datos](#migraciones-de-base-de-datos)
9. [Referencia](#referencia)
10. [Desarrollo local](#desarrollo-local)

## Cómo funciona

1. GitHub Actions compila la app y adjunta el resultado a un release de GitHub.
2. Le envía a deployd una sola petición firmada: qué app, qué commit, dónde
   está el archivo y su checksum.
3. deployd descarga el archivo, verifica el checksum y lo desempaqueta.
4. Ejecuta tu migración de base de datos y el hook previo al cambio, si los
   definiste.
5. Cambia la carpeta activa a la versión nueva de forma atómica y reinicia la
   app.
6. Revisa el health de la app. Si falla, restaura la versión anterior.

```
GitHub Actions (build + publicación del release asset)
      |  HTTPS + HMAC (timestamp, nonce, secret por app)
      v
API de deployd ---- valida, encola, 202 + deploy_id
      |
      v
worker (una cola por app, apps en paralelo)
      download + SHA256 -> unpack -> migrate -> hook before_cutover
      -> cutover atómico -> restart -> health (+ hook after_health)
      fallo tras el cutover => rollback automático
```

Decisiones de diseño que conviene conocer:

- **Artefactos, no código fuente.** Lo que CI compiló es lo que corre. Cada
  deploy se fija a un commit SHA, nunca a una rama.
- **Un contrato fijo.** El workflow no elige comandos ni rutas; solo puede
  disparar una app que registraste.
- **Nada más se administra.** deployd nunca escribe el sitio de Nginx ni la
  unidad de systemd de tu app. Eso existe antes del primer deploy.
- **Sin base de datos externa.** La configuración es YAML, el estado es SQLite.

## Instalación en Ubuntu

Git es lo único que instalas a mano. El instalador configura el servicio,
Nginx, la UI de administración y las herramientas de build que falten (Node
24, uv, pnpm, todas verificadas por checksum). Ejecútalo con tu usuario
normal, no con `sudo`; eleva solo lo que necesita root.

```bash
sudo apt update && sudo apt install -y git
sudo install -d -m 0755 -o "$(id -un)" -g "$(id -gn)" /opt/deployd
git clone https://github.com/dn2182/deployd.git /opt/deployd
/opt/deployd/deploy/install-ubuntu.sh
```

Pregunta:

| Pregunta | Default | Override desatendido |
|---|---|---|
| Dominio público de deployd | `deployd.example.com` | `DEPLOYD_INSTALL_DOMAIN` |
| Dirección de bind de administración | `0.0.0.0` (LAN) | `DEPLOYD_INSTALL_ADMIN_BIND` |
| Puerto de administración | `844` | `DEPLOYD_INSTALL_ADMIN_PORT` |
| Usuario de administración | `deployd-admin` | `DEPLOYD_INSTALL_ADMIN_USER` |
| Contraseña de administración | se pide | `DEPLOYD_INSTALL_ADMIN_PASSWORD` |
| Conexión de sitios, editor de .env | se pregunta, opcional | `DEPLOYD_INSTALL_YES=1` habilita ambos |

Lo que obtienes:

- **API pública** en `https://<dominio>` por el puerto 443, con un certificado
  self-signed en `/etc/deployd/tls/`. El puerto 80 redirige. Esta es la
  dirección que llama GitHub. Pon Cloudflare en modo **Full**; para
  **Full (strict)** reemplaza el certificado y la clave por un par Origin CA
  de Cloudflare en las mismas rutas. El instalador nunca sobrescribe un
  certificado que no generó.
- **UI de administración** en `https://<servidor>:844`, tras usuario y
  contraseña, con el mismo certificado (el navegador pide aceptarlo una vez).
  `http://` en ese puerto redirige a `https://`. Mantén el puerto fuera de
  internet con tu firewall, o enlázalo a `127.0.0.1` y usa un túnel SSH.
- **Token de administración**, que la UI pide después del login. Léelo con:

  ```bash
  sudo grep DEPLOYD_ADMIN_TOKEN /opt/deployd/.env
  ```

- Un usuario de sistema `deployd` que corre el servicio y es dueño de su
  estado (`/var/lib/deployd`) y de los releases (`/srv/deployd`).

### Actualización

```bash
/opt/deployd/deploy/update-ubuntu.sh
```

Descarga la última versión, corre los tests y compila la UI antes de tocar el
servicio en ejecución, espera cualquier deploy en curso, respalda la base de
estado (las cinco copias más recientes en `/var/lib/deployd/backups/`) y
reinicia. Todos los ajustes se conservan. Las funciones opcionales que no
habilitaste se ofrecen de nuevo. Los cambios locales a archivos versionados en
`/opt/deployd` se listan y se descartan; tu configuración vive fuera de ellos.

Para cambiar el dominio, la dirección de bind o el puerto, ejecuta
`/opt/deployd/deploy/install-ubuntu.sh --reconfigure`.

### Desinstalación

```bash
/opt/deployd/deploy/uninstall-ubuntu.sh
```

Ofrece un backup, pregunta si los sitios conectados vuelven a ser carpetas
reales o conservan sus enlaces, y elimina el servicio, su sitio de Nginx, las
credenciales y el estado. Tus apps desplegadas y sus releases se quedan.
Agrega `--purge` para eliminar también los releases, los datos de
recuperación, el certificado y las herramientas de build.

### Cambiar la contraseña de administración

Usa el icono de llave en la barra superior de la UI. El token de
administración y los secrets de las apps no se ven afectados, y el navegador
vuelve a pedir login.

## Registrar una aplicación

Abre la UI de administración, ingresa el token de administración y pulsa
**Add application**. El botón **Help** de la barra superior responde las
preguntas comunes en inglés y español.

| Campo | Notas |
|---|---|
| Name | minúsculas, dígitos, guiones; se convierte en `/srv/deployd/<name>/` |
| GitHub repository | `OWNER/REPO`; solo se aceptan archivos de release de ese repo |
| Public deployd URL | `https://<dominio>`, lo usa el workflow generado |
| Kind | **Static site** (verifica que exista `index.html`) o **Service** (ejecuta tu comando de restart) |
| Local site path | solo sitios estáticos: la carpeta que Nginx ya sirve |
| Restart command | programa y argumentos, sin shell; normalmente `sudo systemctl restart <unit>` |
| Health URL | opcional; un health check fallido tras el cambio hace rollback |
| Expected body / header | opcional; `{commit_sha}` prueba que responde la versión nueva |
| Hooks | `before_cutover` (en la carpeta de la versión nueva) y `after_health` (si falla hay rollback) |
| Environment passthrough | variables del servicio que tus comandos pueden ver, por ejemplo un DSN |
| Environment file path | opcional; ver [Ajustes de la app](#ajustes-de-la-app-archivos-env) |
| Retention | versiones anteriores conservadas para rollback (default 1) |
| Signing secret | se genera y se muestra una sola vez; va a GitHub como `DEPLOYD_SECRET_<APP>` |
| GitHub access token | solo para repositorios privados; `Contents: read` en ese repositorio |

deployd no crea lo siguiente, así que tenlo listo en el servidor:

- **Para un servicio:** su unidad de systemd y permiso para que deployd la
  reinicie:

  ```
  deployd ALL=(root) NOPASSWD: /usr/bin/systemctl restart <unit>
  ```

- **Para un sitio:** el sitio de Nginx, sirviendo
  `/srv/deployd/<app>/releases/current` directamente o una carpeta existente
  que conectarás en [Salir a producción](#salir-a-producción-conectar-el-sitio-web).
- **Los ajustes y secrets propios de la app.**

Conviene saber:

- Un repositorio con frontend y backend son dos aplicaciones, cada una con su
  secret y su workflow.
- Los comandos de migrate, restart y hooks corren con un entorno limpio más
  `DEPLOYD_APP`, `DEPLOYD_DEPLOY_ID`, `DEPLOYD_COMMIT_SHA`,
  `DEPLOYD_RELEASE_DIR`, `DEPLOYD_CURRENT` y las variables de passthrough. Los
  secrets de deployd nunca les llegan.
- Un script que necesita root corre como `/usr/bin/sudo <script>` con una
  línea `NOPASSWD` de sudoers equivalente.
- Un token de GitHub guardado en el formulario de la app funciona de
  inmediato. El fallback global `DEPLOYD_GITHUB_TOKEN` en `/opt/deployd/.env`
  requiere reiniciar el servicio tras un cambio.

## Conectar GitHub Actions

En la tarjeta de la app abre **GitHub Actions setup**:

1. Elige cómo se compila la app (pnpm, npm, HTML plano, o tu propio comando
   como `dotnet publish -c Release -o dist`), la carpeta del proyecto y la
   carpeta de salida.
2. Elige el **trigger**: pushes a una rama, o tags de Git que coincidan con
   un patrón como `v*`. Con el checkbox marcado, un push o un tag despliega
   solo. Desmarcado, inicias el workflow desde la pestaña Actions.
3. **Deployment targets** opcionales (`web-01, web-02`) cuando la misma app
   corre en varios servidores, cada uno con su propio deployd.
4. **Preview workflow** lo muestra. **Save settings and download ZIP** te da
   `.github/workflows/deployd.yml`, `scripts/notify_deploy.py` y
   `DEPLOYD-SETUP.md`. Descomprime en la raíz del repositorio y haz commit.

Luego agrega esto en el repositorio, en Settings, Secrets and variables,
Actions:

| | Un servidor | Deployment targets |
|---|---|---|
| Variable | `DEPLOYD_URL` | `DEPLOYD_URL_WEB_01`, `DEPLOYD_URL_WEB_02` |
| Secret | `DEPLOYD_SECRET_<APP>` (o `DEPLOYD_SECRET`) | `DEPLOYD_SECRET_WEB_01`, `DEPLOYD_SECRET_WEB_02` |

Varias apps en un repositorio: renombra el archivo de workflow por app
(`deployd-api.yml`, `deployd-web.yml`), usa prefijos de tag distintos en modo
tag, y un secret por app. El script notificador es compartido.

Qué hace el workflow:

- **build** empaqueta la carpeta de salida. Se rechazan symlinks,
  `node_modules`, archivos `.env` y claves privadas.
- **publish** adjunta el paquete a un release (el tag enviado, o un release
  `deploy-<app>-<sha>` que crea y poda a los diez más recientes), notifica a
  deployd y espera el resultado.
- Con targets, un job **deploy** notifica a cada servidor tras una sola
  publicación.
- El run falla si el deploy falla, hace rollback, se cancela o es reemplazado
  por un push más nuevo. GitHub solo ve nombres y resultados de los pasos; la
  salida de los comandos se queda en la UI de administración.
- Un run manual tiene la opción `dry_run`, que compila sin desplegar.
  Descomenta la línea `environment:` para exigir una aprobación.

## Salir a producción: conectar el sitio web

Para un sitio estático que Nginx ya sirve desde una carpeta como
`/var/www/example.com`:

1. Despliega una vez, para que exista una versión actual.
2. En la tarjeta de la app abre **Website connection**, pulsa
   **Connect website** y escribe el nombre de la app.

La carpeta se intercambia por un enlace a la versión actual en un solo paso.
Tus archivos originales se conservan como la versión `b4deployd`, que puedes
activar de nuevo cuando quieras. La configuración de Nginx no se toca. Si más
adelante la ruta es reemplazada por una carpeta real, la pestaña lo indica y
**Fix connection** la enlaza de nuevo sin borrar nada.

Los sitios deben estar directamente bajo `/var/www` o una carpeta listada en
`/etc/deployd/site-roots` (`DEPLOYD_INSTALL_SITE_ROOTS=/srv/sites` agrega una,
luego vuelve a ejecutar la actualización). Detalles y recuperación:
[docs/website-connection.md](docs/website-connection.md).

Si lo prefieres, apunta tú mismo el `root` de Nginx a
`/srv/deployd/<app>/releases/current` y omite este paso.

## Ajustes de la app: archivos .env

Mantén el `.env` de cada app fuera de su carpeta de releases. Los deploys y
rollbacks nunca lo tocan. El lugar recomendado es
`/etc/apps-env/<app>/<app>.env`:

```bash
sudo install -d -m 0750 -o root -g <appuser> /etc/apps-env/<app>
sudo install -m 0640 -o root -g <appuser> /dev/null /etc/apps-env/<app>/<app>.env
```

Cárgalo desde la unidad de systemd de la app con
`EnvironmentFile=/etc/apps-env/<app>/<app>.env`.

Con el editor de .env habilitado al instalar, manejas el archivo desde la UI:

1. Edita la app y define **Environment file path**.
2. Abre la pestaña **Environment file** y pulsa **Open file**. Los valores
   están ocultos hasta que muestres uno. **Edit as text** muestra el archivo
   crudo.
3. **Save file**, con la opción de reiniciar la app y revisar su health.

Qué esperar:

- El archivo conserva su dueño y permisos.
- Cada guardado conserva la versión anterior en el servidor (las diez más
  recientes), y **Restore previous version** la recupera.
- El audit log registra quién abrió o cambió el archivo y qué claves
  cambiaron, nunca los valores.
- Un guardado se rechaza si el archivo cambió en el servidor después de
  abrirlo.
- El archivo ya debe existir y llamarse `.env`, `.env.<name>` o `<name>.env`.
- Cada servidor tiene su propio archivo. Con varios servidores, repite el
  cambio en cada uno.

Los archivos guardados en otro lugar también funcionan. Agrega su carpeta,
por ejemplo `/etc/myapp`, como una línea en `/etc/deployd/env-roots` y vuelve
a ejecutar la actualización (`DEPLOYD_INSTALL_ENV_ROOTS=/etc/myapp` lo hace
desatendido). Las carpetas bajo `/home` no son alcanzables, y la configuración
propia de deployd no se puede editar por esta vía. Notas de seguridad:
[SECURITY.md](SECURITY.md).

## Operación

- **Tarjeta de estado** por app: versión actual, cola, último deploy, último
  health check, estado del enlace del sitio.
- **Freeze** pausa los deploys desde GitHub para una app. Volver a una versión
  anterior sigue funcionando.
- **Recent deploys** muestra los últimos diez con filtros y búsqueda por
  commit. Puedes cancelar un deploy en cola o volver a desplegar uno. Se
  refresca cada minuto; el botón Refresh o la tecla `r` actualizan ya.
- **Manage versions** activa una versión anterior, borra versiones conservadas
  y define cuántas conservar. Activar no deshace migraciones de base de datos,
  así que el código anterior debe funcionar con la base actual.
- **Rollback** es automático cuando el restart o el health check fallan tras
  un cambio.
- **Audit log** lista cada acción de administración y quién la hizo.
- **`deployd check`** valida la configuración en el servidor y avisa de sitios
  sin enlazar o apps congeladas. Ejecútalo tras editar archivos a mano.

## Migraciones de base de datos

`deployd-migrate` aplica archivos `NNN_name.sql` en orden, registra el
checksum de cada archivo y se niega a continuar cuando un archivo ya aplicado
fue modificado. Defínelo como el comando migrate de la app y una migración
fallida detiene el deploy antes del cambio.

```bash
deployd-migrate --dir migrations --dialect mssql --dsn-env DEPLOYD_MIGRATE_DSN
deployd-migrate --dir migrations --dialect postgres --dsn-file /etc/app/dsn --dry-run
deployd-migrate --dir migrations status
```

Entrega la cadena de conexión con `--dsn-env` (y lista esa variable en el
environment passthrough de la app) o `--dsn-file`. Cada archivo corre en una
transacción, y dos ejecuciones contra la misma base nunca se solapan. Instala
el driver con `pip install 'deployd[mssql]'` o `'deployd[postgres]'`.

## Referencia

### Contrato de la petición

```
POST /deploys
X-Deploy-Timestamp: <unix epoch seconds>        (±5 minutos)
X-Deploy-Nonce: <uuid4>                         (un solo uso por app)
X-Deploy-Signature: sha256=<hex hmac de "{timestamp}.{nonce}.{raw body}">

{"app": "...", "commit_sha": "<40 hex>", "artifact_url": "https://...",
 "artifact_sha256": "<64 hex>", "triggered_by": "github-actions:<run_id>"}
```

La respuesta es `202` con un `deploy_id`. Luego `GET /deploys/{deploy_id}`
reporta `queued`, `running`, `succeeded`, `failed`, `rolled_back`,
`superseded` o `cancelled`, con los pasos. Enviar de nuevo la misma petición
firmada devuelve el mismo id. Una firma inválida o una app desconocida
responden `401`, una app congelada `423`, un archivo de fuera del repositorio
de la app `403`.

### Rutas

| Ruta | Contenido |
|---|---|
| `/opt/deployd` | el checkout, sus herramientas y `.env` (token de administración, ajustes del servicio) |
| `/var/lib/deployd` | `apps.yaml`, `secrets.env`, la base de estado y sus backups |
| `/srv/deployd/<app>/releases/` | `current/` más las versiones anteriores conservadas |
| `/etc/deployd/tls/` | certificado y clave de la API pública y de la UI de administración |
| `/etc/deployd/site-roots` | carpetas cuyos sitios se pueden conectar |
| `/etc/deployd/env-roots` | carpetas cuyos archivos `.env` se pueden editar |
| `/etc/apps-env/<app>/` | lugar por defecto del `.env` de cada app |
| `/var/lib/deployd-env` | versiones anteriores de archivos `.env` editados, solo root |
| `/var/lib/deployd-connect` | datos de recuperación de la conexión de sitios, solo root |
| `/var/lib/deployd-auth` | login de administración |

### Ajustes del servicio

`/opt/deployd/.env` contiene `DEPLOYD_ADMIN_TOKEN`, el fallback opcional
`DEPLOYD_GITHUB_TOKEN` para repositorios privados, y
`DEPLOYD_DRAIN_TIMEOUT_SECONDS` (cuánto espera un stop por un deploy que está
a mitad del cambio, default 600). Definir ahí `DEPLOYD_SECRET_<APP>` o
`DEPLOYD_GITHUB_TOKEN_<APP>` tiene prioridad sobre la UI para esa app.
Reinicia el servicio después de cambiar este archivo.

Todo lo que la UI edita vive en `/var/lib/deployd/apps.yaml`.
[config/apps.example.yaml](config/apps.example.yaml) documenta cada clave,
incluidos límites de tamaño y timeouts. Almacenamiento de releases y
retención: [docs/release-layout.md](docs/release-layout.md).

### Seguridad y Windows

El checklist de hardening y las propiedades de seguridad están en
[SECURITY.md](SECURITY.md). Una instalación en Windows con NSSM e IIS se
describe en [deploy/windows.md](deploy/windows.md); su instalador no se ha
probado en un servidor Windows real.

## Desarrollo local

Python 3.11.4+, uv, Node 24, pnpm 12.

```bash
make install                                   # dependencias Python + web
cp .env.example .env                           # define DEPLOYD_ADMIN_TOKEN y las rutas locales
cp config/apps.example.yaml config/apps.yaml
make dev                                       # API en 127.0.0.1:8300
make dev-web                                   # UI Vite, hace proxy de /api a la API
make check                                     # ruff, pytest, oxlint, vitest
make check-audit                               # pip-audit y pnpm audit
```

```
src/deployd/        API, worker de deploys, store SQLite, comandos migrate y check
web/                UI de administración (React, Vite, Tailwind)
deploy/             instalador, actualizador y desinstalador Ubuntu, unidad systemd, helpers root
examples/           workflow de referencia y el script notificador
docs/               conexión de sitios y layout de releases en detalle
tests/              pytest para API, worker e instalador; web/ usa vitest
```

Ver [CONTRIBUTING.md](CONTRIBUTING.md). Apache-2.0, ver [LICENSE](LICENSE) y
[NOTICE](NOTICE).
