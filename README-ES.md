# deployd — agente de deploy tipo pull sobre HTTPS

[![CI](https://github.com/dn2182/deployd/actions/workflows/ci.yml/badge.svg)](https://github.com/dn2182/deployd/actions/workflows/ci.yml)
[![License](https://img.shields.io/github/license/dn2182/deployd)](LICENSE)

*Read it in [English](README.md).*

Despliega artefactos de tus aplicaciones en tus propios servidores desde
GitHub Actions **sin SSH ni FTP entrante y sin toolchain de build ni
contenedores en el servidor**. CI compila y publica un release asset y envía
una sola petición firmada con HMAC; deployd la descarga, la verifica, la pone
en producción de forma atómica, reinicia la app, comprueba su salud y hace
rollback solo si algo falla.

> **Estado:** versión preliminar. `0.1.0` se etiqueta tras el primer deploy en
> producción y un simulacro de rollback.

## Contenido

1. [Cómo funciona](#cómo-funciona)
2. [Instalación en Ubuntu](#instalación-en-ubuntu)
3. [Registrar una aplicación](#registrar-una-aplicación)
4. [Conectar GitHub Actions](#conectar-github-actions)
5. [Salir a producción: conectar el sitio web](#salir-a-producción-conectar-el-sitio-web)
6. [Operación](#operación)
7. [Referencia](#referencia)
8. [Desarrollo local](#desarrollo-local)

## Cómo funciona

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

- **Artefactos, no código fuente.** Lo que CI firmó es lo que corre; producción
  no necesita SDKs. Cada deploy se fija a un commit SHA, nunca a una rama.
- **Un contrato fijo.** El workflow no elige comandos ni rutas; solo puede
  disparar una app que registraste. Un build comprometido sigue enviando lo
  que construyó, así que cada app corre bajo su propia identidad con mínimos
  privilegios.
- **Nada más se administra.** deployd nunca escribe el sitio de Nginx, la
  unidad de systemd ni la configuración de tu app. Eso existe antes del primer
  deploy.
- **Sin base de datos externa.** La configuración es YAML más env; el estado
  en runtime es SQLite.

## Instalación en Ubuntu

Git es la única dependencia de arranque; el instalador descarga Node 24, uv y
pnpm como builds oficiales verificados por checksum y configura el servicio,
Nginx, los directorios de estado y la UI de administración. Ejecútalo con tu
usuario normal, no con `sudo`; eleva solo lo que necesita root.

```bash
sudo apt update && sudo apt install -y git
sudo install -d -m 0755 -o "$(id -un)" -g "$(id -gn)" /opt/deployd
git clone https://github.com/dn2182/deployd.git /opt/deployd
/opt/deployd/deploy/install-ubuntu.sh
```

Te pregunta:

| Pregunta | Default | Override desatendido |
|---|---|---|
| Dominio público de deployd | `deployd.example.com` | `DEPLOYD_INSTALL_DOMAIN` |
| Dirección de bind de administración | `0.0.0.0` (LAN) | `DEPLOYD_INSTALL_ADMIN_BIND` |
| Puerto de administración | `844` | `DEPLOYD_INSTALL_ADMIN_PORT` |
| Usuario Basic Auth | `deployd-admin` | `DEPLOYD_INSTALL_ADMIN_USER` |
| Contraseña Basic Auth | se pide | `DEPLOYD_INSTALL_ADMIN_PASSWORD` |
| Confirmaciones | se piden | `DEPLOYD_INSTALL_YES=1` |

Lo que obtienes:

- **API pública** en `https://<dominio>` (puerto 443, certificado self-signed
  en `/etc/deployd/tls/`; el puerto 80 redirige). Pon Cloudflare en modo
  **Full**. Para **Full (strict)** reemplaza el par certificado y clave por un
  certificado Origin CA de Cloudflare en las mismas rutas; el instalador nunca
  sobrescribe un certificado que no generó.
- **UI de administración** en `http://<bind>:844` tras Basic Auth. El bind por
  defecto es alcanzable en la LAN; mantén ese puerto fuera de la WAN con el
  firewall del host, o elige `127.0.0.1` y usa un túnel SSH.
- **Token de administración** generado en `/opt/deployd/.env` (se imprime la
  ruta, no el valor). Léelo con `sudo grep DEPLOYD_ADMIN_TOKEN /opt/deployd/.env`.
- Un usuario de sistema `deployd` dueño de `/var/lib/deployd` (estado,
  registro de apps, secrets) y `/srv/deployd` (releases).

Opcional al instalar: el **helper de conexión de sitios web** (un programa
propiedad de root tras una sola regla de sudo) que puede cambiar un sitio
estático existente bajo `/var/www` o cualquier raíz listada en
`/etc/deployd/site-roots` a los releases de deployd. Ver
[Salir a producción](#salir-a-producción-conectar-el-sitio-web).

### Actualización

```bash
/opt/deployd/deploy/update-ubuntu.sh
```

Hace pull, corre lint, tests y el build web **antes** de detener el servicio,
se niega a detenerlo mientras hay un deploy en curso, respalda el estado
SQLite en `/var/lib/deployd/backups/` (conserva los cinco más recientes) y
reinicia. Todos los ajustes se conservan. Los cambios locales a archivos
versionados en el servidor se imprimen y se descartan primero; tu
configuración vive fuera del árbol versionado. Los hallazgos del audit de
dependencias se reportan, no detienen (`DEPLOYD_INSTALL_STRICT_AUDIT=1` para
que detengan). Ejecutar `install-ubuntu.sh` a mano sobre una instalación
existente ofrece la misma actualización in-place; `--reconfigure` vuelve a
hacer las preguntas.

### Desinstalación

```bash
/opt/deployd/deploy/uninstall-ubuntu.sh          # --purge también elimina releases,
                                                 # datos de recuperación, TLS y tooling
```

Ofrece un backup, pregunta si los sitios conectados deben restaurarse a
carpetas reales o conservar sus symlinks, y elimina el servicio, los sitios de
Nginx, las credenciales y el estado. Las aplicaciones desplegadas se conservan.

### Cambiar la contraseña de administración

Icono de llave en la barra superior de la UI una vez autenticado. Cambia la
contraseña Basic Auth de Nginx mediante un helper root restringido; el token
de administración y los secrets de las apps no cambian, y el navegador vuelve
a pedir login.

## Registrar una aplicación

Abre la UI de administración, ingresa el token de administración y pulsa
**Add application**. El botón **Help** (barra superior, disponible antes del
login) responde las preguntas habituales en inglés y español.

| Campo | Notas |
|---|---|
| Name | minúsculas, dígitos, guiones; se convierte en `/srv/deployd/<name>/` |
| GitHub repository | `OWNER/REPO`; fija el allowlist de artefactos automáticamente |
| Public deployd URL | `https://<dominio>`, lo usa el workflow generado |
| Kind | **Static site** (verifica `index.html`) o **Service** (tu comando de restart) |
| Local site path | solo sitios estáticos; la ruta que Nginx ya sirve, bajo una raíz permitida |
| Restart command | lista argv, sin shell; en servicios suele ser `sudo systemctl restart <unit>` |
| Health URL | opcional; si el restart o el health fallan tras el cutover, hay rollback |
| Expected body / header | opcional, placeholder `{commit_sha}`: un proceso viejo no puede pasar |
| Hooks | `before_cutover` (directorio del release nuevo) y `after_health` (release activo, rollback si falla) |
| Environment passthrough | variables del servicio reenviadas a los comandos, por ejemplo un DSN |
| Retention | versiones previas conservadas para rollback (default 1) |
| Signing secret | se genera y se muestra una sola vez; va a CI como `DEPLOYD_SECRET_<APP>` |
| GitHub access token | opcional, `Contents: read` en ese repo, necesario para repos privados |

Prerrequisitos en el servidor, por app, que deployd no crea:

- Una unidad de systemd (o pool de IIS en Windows) para servicios, y esta línea
  de sudoers para el usuario deployd:
  `deployd ALL=(root) NOPASSWD: /usr/bin/systemctl restart <unit>`.
- El sitio de Nginx sirviendo `/srv/deployd/<app>/releases/current`
  directamente, o un document root existente que conectarás en
  [Salir a producción](#salir-a-producción-conectar-el-sitio-web).
- La configuración y los secrets propios de la app, donde sea que los lea.

Los comandos corren con un entorno limpio más `DEPLOYD_APP`,
`DEPLOYD_DEPLOY_ID`, `DEPLOYD_COMMIT_SHA`, `DEPLOYD_RELEASE_DIR`,
`DEPLOYD_CURRENT` y las variables listadas en **Environment passthrough**; los
secrets del propio servicio nunca les llegan. Un script que necesita root corre
como `/usr/bin/sudo <script>` con una línea `NOPASSWD` de sudoers equivalente.

Un repositorio con frontend y backend son dos aplicaciones, cada una con su
secret y su workflow. Un token de GitHub puesto en el formulario de la app
aplica sin reiniciar y tiene prioridad sobre el fallback global
`DEPLOYD_GITHUB_TOKEN` en `.env` (que sí requiere reinicio al cambiar).

## Conectar GitHub Actions

En la tarjeta de la app abre **GitHub Actions setup**:

1. Elige el preset de build (pnpm, npm, HTML plano, o un comando propio como
   `dotnet publish -c Release -o dist` o `go build -o dist/api ./cmd/api`), la
   carpeta del proyecto (`.` o `frontend`) y la carpeta de salida.
2. Elige el **trigger**: pushes a una rama, o tags de Git que coincidan con un
   patrón (`v*`, `web-v*`). El checkbox de abajo decide automático versus
   manual: marcado, el push o el tag despliega solo; desmarcado, corres el
   workflow desde Actions y eliges la rama o el tag.
3. **Deployment targets** opcionales (`web-01, web-02`) para nodos espejo,
   cada uno con su propio deployd y la app registrada.
4. **Preview workflow** para leerlo, **Save settings and download ZIP** para
   obtener `.github/workflows/deployd.yml`, `scripts/notify_deploy.py` y
   `DEPLOYD-SETUP.md`. Descomprime en la raíz del repositorio.

Ajustes del repositorio (Settings, Secrets and variables, Actions):

| | Un solo servidor | Deployment targets |
|---|---|---|
| Variable | `DEPLOYD_URL` | `DEPLOYD_URL_WEB_01`, `DEPLOYD_URL_WEB_02` |
| Secret | `DEPLOYD_SECRET_<APP>` (fallback a `DEPLOYD_SECRET`) | `DEPLOYD_SECRET_WEB_01`, `DEPLOYD_SECRET_WEB_02` |

Varias apps en un repositorio: renombra el workflow por app
(`deployd-api.yml`, `deployd-web.yml`), usa prefijos de tag distintos en modo
tag, un secret por app. El script notificador es compartido.

Qué hace el workflow: un job `build` de solo lectura empaqueta la carpeta de
salida (dotfiles permitidos, archivos de credenciales y `node_modules`
rechazados) y lo sube; un job `publish` vuelve a verificar el checksum, crea el
release `deploy-<app>-<sha>`, notifica a deployd y poda esos releases más allá
de los diez más recientes. Con targets, un job matriz `deploy` notifica a cada
nodo tras una sola publicación. `workflow_dispatch` tiene un input `dry_run`;
descomenta la línea `environment:` para exigir revisores. Las actions y los
runners están fijados por versión.

El notificador firma la petición, consulta `GET /deploys/{id}` y falla el run
en `failed`, `rolled_back`, `superseded` (un push más nuevo lo reemplazó en la
cola) o `cancelled`. CI ve nombres y estados de los pasos; la salida de los
comandos solo se ve en la UI de administración.

## Salir a producción: conectar el sitio web

Para sitios estáticos que un server block de Nginx ya sirve desde una carpeta
(`/var/www/example.com`, `/srv/sites/example.com`):

1. Despliega una vez para que exista `/srv/deployd/<app>/releases/current`.
2. En la tarjeta de la app abre **Website connection**, revisa el estado,
   pulsa **Connect website** y escribe el nombre de la app.

La carpeta se intercambia atómicamente por un symlink a `releases/current`;
los archivos originales se conservan como la versión `b4deployd`, disponible
para activar. La configuración de Nginx no se toca. Si más adelante la ruta es
reemplazada por una carpeta real u otro enlace, la pestaña lo reporta y
**Fix connection** vuelve a enlazarla, guardando el reemplazo bajo
`/var/lib/deployd-connect`. Cada deploy registra un paso `website` que avisa
cuando la ruta no está enlazada, y la tarjeta de estado lo muestra.

Las raíces de sitio permitidas son `/var/www` más cada línea de
`/etc/deployd/site-roots` (propiedad de root); el instalador verifica que cada
raíz sea de root y no tenga escritura de grupo ni de otros
(`DEPLOYD_INSTALL_SITE_ROOTS=/srv/sites` agrega una de forma desatendida).
Detalles, recuperación y el flujo de restauración al eliminar:
[docs/website-connection.md](docs/website-connection.md).

Si prefieres apuntar tú mismo el `root` de Nginx a `releases/current`, hazlo y
omite el conector.

## Operación

- **Tarjeta de estado** por app: release actual, cantidad en cola, último
  deploy, último health, estado del enlace del sitio, **Freeze** (los deploys
  de CI responden `423` hasta descongelar; el rollback sigue funcionando).
- **Recent deploys**: los últimos diez, filtros, búsqueda por commit, cancelar
  deploys en cola, redeploy, enlaces a commit y compare. La lista y el
  **Audit log** se refrescan cada dos minutos mientras la pestaña está
  visible; el Refresh de la barra superior o la tecla `r` actualizan ya.
- **Manage versions**: activar una versión conservada (mismo camino de
  cutover, restart y health; sin download ni migración, así que el código
  viejo debe seguir funcionando con la base de datos actual), borrar archivos
  conservados, cambiar la retención.
- **Rollback** es automático ante un restart o health fallido tras el cutover,
  y manual mediante Manage versions.
- **`deployd check`** valida ajustes, base de estado, secrets y su modo de
  archivo, rutas y ejecutables, y avisa de rutas de sitio sin enlazar o apps
  congeladas. Ejecútalo tras editar configuración a mano.
- **`/healthz`** responde `ok` solo cuando la base de estado es escribible y
  el worker está vivo. Detrás de Cloudflare el access log muestra la dirección
  del visitante.
- **Actualizar deployd** es `update-ubuntu.sh`; deployd no puede desplegarse a
  sí mismo por su propia cola, porque el restart mataría el deploy en curso.

### Migraciones de base de datos

`deployd-migrate` aplica archivos `NNN_name.sql` en orden numérico, registra el
SHA256 de cada archivo y se niega a correr cuando un archivo aplicado cambió.
Úsalo como comando migrate de la app para que una migración fallida detenga el
deploy antes del cutover.

```bash
deployd-migrate --dir migrations --dialect mssql --dsn-env DEPLOYD_MIGRATE_DSN
deployd-migrate --dir migrations --dialect postgres --dsn-file /etc/app/dsn --dry-run
deployd-migrate --dir migrations status
```

Pasa el DSN con `--dsn-env` (lista esa variable en el environment passthrough
de la app) o `--dsn-file`; `--dsn` es visible en `ps`. SQL Server ejecuta cada
archivo en una transacción con `SET XACT_ABORT ON`, drena cada result set para
que los errores tras un `PRINT` también fallen, y rechaza archivos que manejan
sus propias transacciones; los runners que comparten base se serializan con
`sp_getapplock` (SQL Server) o un advisory lock (PostgreSQL). Instala el
driver con `pip install 'deployd[mssql]'` o `'deployd[postgres]'`.

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

`202 {deploy_id}` y luego `GET /deploys/{deploy_id}` reporta
`queued | running | succeeded | failed | rolled_back | superseded | cancelled`
con la línea de tiempo de pasos. Reintentar exactamente la misma petición
firmada devuelve el id original. Apps desconocidas responden `401`, apps
congeladas `423`, URLs de artefacto no permitidas `403`.

### Rutas

| Ruta | Contenido |
|---|---|
| `/opt/deployd` | checkout de git, `.venv`, `.node`, `.env` (root:deployd 0640) |
| `/var/lib/deployd` | `apps.yaml`, `secrets.env` (0600), `deployd.sqlite3`, `backups/` |
| `/srv/deployd/<app>/releases/` | `current/` más versiones conservadas `<sha>-<deploy_id>/` |
| `/etc/deployd/site-roots` | raíces permitidas para la conexión de sitios |
| `/etc/deployd/tls/` | certificado y clave de origen |
| `/var/lib/deployd-connect` | datos de recuperación de sitios, solo root |
| `/var/lib/deployd-auth/htpasswd` | login de administración |

### `.env`

`DEPLOYD_ADMIN_TOKEN`, `DEPLOYD_GITHUB_TOKEN` (fallback para repos privados),
`DEPLOYD_BIND_HOST` / `DEPLOYD_BIND_PORT` (`127.0.0.1:8300`, solo Nginx le
habla), `DEPLOYD_DRAIN_TIMEOUT_SECONDS` (cuánto espera un stop por un cutover
en curso, default 600; el timeout de stop de systemd está por encima),
`DEPLOYD_DB_PATH`, `DEPLOYD_APPS_CONFIG`, `DEPLOYD_SECRETS_FILE`. Los overrides
por app `DEPLOYD_SECRET_<APP>` y `DEPLOYD_GITHUB_TOKEN_<APP>` tienen prioridad
sobre la UI y no se pueden cambiar desde ella. Los cambios en `.env` requieren
reiniciar el servicio.

### `apps.yaml`

Todo lo que la UI edita vive en `/var/lib/deployd/apps.yaml`;
[config/apps.example.yaml](config/apps.example.yaml) documenta cada clave,
incluidos límites de tamaño de artefacto, timeouts, hooks, aserciones de health
y `env_passthrough`. El almacenamiento de releases, la activación y la
retención se describen en [docs/release-layout.md](docs/release-layout.md).

### Windows

Servicio NSSM, physical path de IIS sobre un junction, `Restart-WebAppPool`:
[deploy/windows.md](deploy/windows.md) (script instalador sin probar en
Windows real). Propiedades de seguridad y checklist de hardening:
[SECURITY.md](SECURITY.md).

## Desarrollo local

Python 3.11.4+, uv, Node 24, pnpm 11.

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
src/deployd/        app FastAPI, pipeline del worker, store SQLite, CLIs migrate y check
web/                UI de administración React (Vite + Tailwind)
deploy/             instalador/actualizador/desinstalador Ubuntu, unidad systemd, helpers root, guía Windows
examples/           workflow de referencia generado + notificador vendorizable
docs/               conexión de sitios y layout de releases en detalle
tests/              pytest (API, worker, instalador); web/ usa vitest
```

Ver [CONTRIBUTING.md](CONTRIBUTING.md). Reportes de seguridad: [SECURITY.md](SECURITY.md).
Apache-2.0, ver [LICENSE](LICENSE) y [NOTICE](NOTICE).
