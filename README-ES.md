# deployd — agente de despliegue estilo pull sobre HTTPS

[![CI](https://github.com/dn2182/deployd/actions/workflows/ci.yml/badge.svg)](https://github.com/dn2182/deployd/actions/workflows/ci.yml)
[![Licencia](https://img.shields.io/github/license/dn2182/deployd)](LICENSE)

*Read this in [English](README.md).*

> **Estado:** versión preliminar. El núcleo está probado, pero `0.1.0` seguirá
> sin publicar hasta completar el primer despliegue en producción y una prueba
> de rollback.

Despliega artefactos de aplicaciones a tus propios servidores desde GitHub
Actions **sin SSH ni FTP entrante y sin toolchain de compilación ni
contenedores en la ruta de despliegue**. Un solo endpoint HTTPS con un contrato
fijo firmado por HMAC es toda la superficie de ataque.

## Características

- **Despliegues firmados** — HMAC-SHA256 por aplicación sobre timestamp,
  nonce y cuerpo; protección atómica y persistente contra replay
- **Basado en artefactos** — CI compila y publica; el servidor descarga y
  verifica el SHA256; las aplicaciones desplegadas nunca se compilan en el
  servidor
- **Cutover seguro, rollback local** — carpeta real `releases/current/` con
  intercambio atómico de directorios, o el modo anterior de intentos inmutables en
  `releases/<sha>-<deploy_id>/` más un symlink `current` (atómico en Linux) o
  un cambio controlado de junction (Windows); si el health check falla, el
  rollback es automático
- **Migraciones que condicionan el release** — migraciones SQL forward-only
  con checksum (`deployd-migrate`, SQL Server vía pyodbc) corren antes del
  cutover y detienen el despliegue en seco si fallan
- **UI de administración** — registro de aplicaciones, rotación de secretos
  en un clic (se muestran una sola vez), historial de despliegues con log
  por paso, redeploy, estado en vivo
- **Nativo en bare-host** — systemd en Linux, NSSM/IIS en Windows; un solo
  servicio Python con un archivo de estado SQLite
- **Cola consciente de reinicios** — los deploys en cola se recuperan; los
  interrumpidos fallan explícitamente en vez de quedar en `running`

## Cómo funciona

```
GitHub Actions (build + publicación del artefacto)
      |
      |  HTTPS + HMAC (timestamp + nonce, secreto por app)
      v
API de despliegue (FastAPI)  -- valida, encola, 202 + deploy_id
      |
      v
Worker de despliegue (cola serializada por app)
      +--> descarga el artefacto + verifica SHA256
      +--> desempaqueta en release único     (límites + path traversal seguro)
      +--> ejecuta migraciones               (forward-only, se detiene si falla)
      +--> cutover                           (intercambio de directorios o enlaces)
      +--> restart + health check            (falla => rollback automático)
      +--> registra estado + log por paso    (CI consulta GET /deploys/{id})
```

### Decisiones de diseño

- **Artefactos, no código fuente.** Lo que se firmó es lo que corre;
  producción no necesita SDKs.
- **Se despliega por SHA de commit, nunca por nombre de rama.**
- **API y worker separados.** La API solo valida y encola; los despliegues
  de una misma app se serializan, apps distintas corren en paralelo.
- **Un contrato fijo reduce el radio de impacto.** El workflow no puede
  reemplazar comandos de deploy ni apuntar a una app no registrada. Un build
  comprometido todavía puede empacar código malicioso, así que cada app debe
  ejecutarse con una identidad propia de privilegios mínimos.
- **Sin base de datos externa.** La configuración es YAML + env; el estado
  de runtime es SQLite. Un agente de despliegue no debe depender de
  infraestructura que él mismo podría estar desplegando.

## Instalación en Ubuntu

Git es la única dependencia inicial. El instalador guiado instala los demás
requisitos del sistema y del proyecto, ejecuta las validaciones y configura el
servicio, Nginx, el estado y la interfaz de administración.

```bash
sudo apt update
sudo apt install -y git
sudo install -d -m 0755 -o "$(id -un)" -g "$(id -gn)" /opt/deployd
git clone https://github.com/dn2182/deployd.git /opt/deployd
cd /opt/deployd
./deploy/install-ubuntu.sh
```

Ejecuta [`deploy/install-ubuntu.sh`](deploy/install-ubuntu.sh) con el usuario
normal propietario del repositorio, no con `sudo`. El script eleva únicamente
las operaciones que necesitan acceso al sistema. El modo Flexible de
Cloudflare es solo para pruebas; restringe el puerto de administración con el
firewall y usa Full (strict) antes de producción.
El instalador solicita el dominio público, bind/puerto de administración y
usuario de Basic Auth, y genera el token de administración cuando hace falta.
Repara rutas de desarrollo ausentes o sin datos en un `.env` existente,
conserva tokens y rutas absolutas, y respalda `.env` antes de modificarlo.
Si una ruta de desarrollo contiene datos, se detiene con instrucciones de
migración. Valida las rutas y la configuración de aplicaciones con el usuario
del servicio antes de iniciarlo.

## Actualización en Ubuntu

```bash
cd /opt/deployd
git pull --ff-only origin main
make install
make build
sudo systemctl restart deployd
```

Para cambios únicamente del frontend basta con ejecutar
`git pull --ff-only origin main` y `make build`; después refresca el navegador.

## Desinstalación en Ubuntu

```bash
cd /opt/deployd
./deploy/uninstall-ubuntu.sh
```

[`deploy/uninstall-ubuntu.sh`](deploy/uninstall-ubuntu.sh) ofrece un respaldo
con permisos restringidos y exige confirmación explícita. Elimina el servicio
deployd, la configuración de Nginx, las credenciales, el estado estándar y el
repositorio. Conserva los paquetes compartidos y las aplicaciones desplegadas.
Si quedan datos de aplicaciones, conserva también la cuenta de servicio para
mantener su propietario. Las rutas de estado personalizadas fuera del checkout y
`/var/lib/deployd` no se respaldan ni eliminan; respáldalas por separado.
La desinstalación se detiene si no puede parar el servicio y restaura los cambios
de Nginx si su validación o recarga falla.

El instalador detiene deployd antes de reconstruir dependencias, repara permisos
del estado estándar y verifica el acceso del usuario de servicio. Python
administrado se instala en `.python` dentro del checkout para que `ProtectHome`
no lo oculte. Si una `.venv` existente apunta al directorio personal, debe
recrearse. Si falla una actualización tras detener el servicio, corrige el error
indicado y ejecuta el instalador otra vez. CI prueba instalación nueva,
reinstalación, respaldo y desinstalación con systemd y Nginx reales en Ubuntu.

## Desarrollo local

Instala Python 3.11+, `uv`, Node.js 22.19+ y pnpm 11.20.0 para tu sistema
operativo. Luego ejecuta:

```bash
make install                                   # dependencias Python y frontend
cp .env.example .env                           # ajusta las rutas y define el token
cp config/apps.example.yaml config/apps.yaml   # registra tus aplicaciones
make dev                                       # API en 127.0.0.1:8300
make dev-web                                   # interfaz Vite de administración
```

Antes de `make dev`, define `DEPLOYD_DB_PATH=deployd.sqlite3`,
`DEPLOYD_APPS_CONFIG=config/apps.yaml` y
`DEPLOYD_SECRETS_FILE=config/secrets.env` en `.env`. El ejemplo usa las rutas
del servicio Ubuntu bajo `/var/lib/deployd`.

## Configuración guiada de aplicaciones

Abre la interfaz por HTTPS o un túnel SSH e ingresa el token de administración.
En **Agregar aplicación** puedes configurar:

- Nombre, repositorio de GitHub (`PROPIETARIO/REPO` o su URL) y URL pública de
  la API de deployd, distinta de la URL del sitio que se despliega.
- Sitio estático (HTML directo o React/Vite compilado) o servicio, ruta local,
  URL de salud y retención. Las rutas internas se asignan automáticamente. Los
  archivos activos están en `/srv/deployd/<app>/releases/current` y se conserva
  una versión anterior. El modo estático verifica `index.html`; Nginx sigue
  siendo el servidor web.
- Un secreto de firma aleatorio (se muestra una sola vez), o uno propio de al
  menos 32 bytes.
- Un token de GitHub opcional por aplicación, de solo lectura, para artefactos
  de repositorios privados.

**Editar** usa el mismo formulario. Los campos de credenciales vacíos conservan
los valores existentes; reemplazar el secreto de firma requiere confirmación.
No hay editor JSON ni controles para cambiar rutas internas o diseño de carpetas.
La ruta local del sitio (por ejemplo `/var/www/bluedatos.com`) queda fija al
guardar. La ficha de la app y el resumen muestran las rutas en modo de solo
lectura. Las apps existentes conservan sus carpetas y comandos de migración;
no se trasladan automáticamente.

La URL de salud es opcional. Déjala vacía para omitir la comprobación HTTP en
despliegues, activaciones y rollback; el registro la muestra como omitida. Los
sitios estáticos siguen requiriendo un `current/index.html` no vacío. Sin URL de
salud, los fallos de disponibilidad HTTP no activan rollback automático. Las
URLs existentes se siguen comprobando salvo que se borren explícitamente.

Para un frontend y una API, registra dos aplicaciones aunque compartan repositorio
y dominio. Cada una tiene su artefacto, secreto de firma, versiones y rollback.
Un solo workflow de GitHub Actions puede desplegar una o ambas.

Las credenciales se guardan separadas en `DEPLOYD_SECRETS_FILE`, con modo `0600`.
La API nunca devuelve los tokens de GitHub. Las variables de entorno por app
(`DEPLOYD_SECRET_<APP_NAME_UPPER_SNAKE>` y
`DEPLOYD_GITHUB_TOKEN_<APP_NAME_UPPER_SNAKE>`) tienen prioridad y no se modifican
desde la UI. En estas claves, los guiones del nombre se sustituyen por guiones
bajos y el nombre se convierte a mayúsculas.

La pantalla de confirmación y cada tarjeta ofrecen **Configuración de GitHub Actions**.
Elige pnpm/npm, HTML simple (sin compilación) o un comando personalizado; indica
la carpeta del repositorio, el comando, la carpeta de salida y la rama de despliegue.
Los presets de Node instalan dependencias desde el lockfile; los comandos
personalizados deben instalar sus propias herramientas.
**Guardar ajustes y descargar ZIP** guarda estos datos y genera
`.github/workflows/deploy.yml`, `scripts/notify_deploy.py` e instrucciones.
El ZIP no contiene valores secretos. Copia los archivos a la raíz del repositorio
y revisa los workflows existentes para evitar ejecuciones duplicadas.

Por defecto, el workflow es manual. Súbelo a la rama predeterminada y usa
**Actions → Deploy APP → Run workflow**, seleccionando la rama de despliegue.
Después de una prueba exitosa puedes habilitar despliegues al hacer push,
descargar otra vez y subir el workflow actualizado. Guardar aquí no modifica GitHub.
Solo se empaqueta la carpeta seleccionada, con permisos de lectura para Nginx.
Se excluyen metadatos de Git y se rechazan archivos ocultos, enlaces, dependencias
y archivos comunes de claves privadas. Revisa si hay otro contenido privado.

Guardar configura deployd; **no** agrega secretos ni workflows a GitHub,
prueba el token, modifica Nginx ni despliega la aplicación. Copia el secreto de
firma a `DEPLOYD_SECRET`, define la variable `DEPLOYD_URL` en Actions y agrega el
workflow generado. Configura la raíz de Nginx o el enlace fijo por
separado. La comprobación HTTP confirma disponibilidad, no la versión activa;
verifica el primer despliegue antes de cambiar la ruta de un sitio existente.

## Integración con CI

Usa el ZIP generado o adapta [`examples/github-actions-deploy.yml`](examples/github-actions-deploy.yml)
al repo de tu aplicación e incorpora
[`examples/notify_deploy.py`](examples/notify_deploy.py) como
`scripts/notify_deploy.py`. El repo necesita un secreto (`DEPLOYD_SECRET`) y
una variable (`DEPLOYD_URL`). Las descargas públicas no necesitan una credencial
de GitHub en el servidor.

Para un repositorio privado, agrega en el formulario un token granular con
**Contents: read** para ese repositorio; no requiere reiniciar el servicio.
`DEPLOYD_GITHUB_TOKEN` en el `.env` protegido sigue siendo el respaldo global
cuando no hay token por app (reinicia después de cambiar `.env`). Eliminar el
token de una app vuelve a usar ese respaldo. Estos tokens son distintos del
token de administración y del secreto HMAC. El workflow publica con su
`github.token` incorporado.

El workflow de referencia envía la URL de API del artefacto:
`https://api.github.com/repos/OWNER/REPO/releases/assets/ASSET_ID`.
El formulario configura `artifact.allowed_url_prefix` con el prefijo del repo,
`https://api.github.com/repos/OWNER/REPO/releases/assets/`, y agrega
`release-assets.githubusercontent.com` a `artifact.allowed_redirect_hosts`.
Deployd solicita el archivo binario y envía el token solo en la solicitud HTTPS
inicial a esa API; nunca lo reenvía en redirecciones. Los repositorios públicos
pueden usar las mismas URLs sin `DEPLOYD_GITHUB_TOKEN`.

El contrato del request:

```
POST /deploys
X-Deploy-Timestamp: <epoch unix en segundos>
X-Deploy-Nonce: <uuid4>
X-Deploy-Signature: sha256=<hmac hex de "{timestamp}.{nonce}.{cuerpo crudo}">

{
  "app": "example-api",
  "commit_sha": "<sha git de 40 hex>",
  "artifact_url": "https://...",
  "artifact_sha256": "<64-hex>",
  "triggered_by": "github-actions:<run_id>"
}
```

`202 {deploy_id}` → consulta `GET /deploys/{deploy_id}` para
`queued | running | succeeded | failed | rolled_back` más el log por paso.
Si se pierde la respuesta `202`, reintenta exactamente el mismo request
firmado y nonce; deployd devuelve el `deploy_id` original sin duplicarlo.

## Administración de versiones

Abre **Administrar versiones** en una aplicación para ver versiones locales,
activar una anterior o eliminar sus archivos con confirmación. La activación
usa la misma cola por aplicación, reinicia y verifica la salud. No descarga
artefactos ni ejecuta migraciones; el código anterior debe ser compatible con
la base de datos actual. Si falla, intenta restaurar la versión previa. Las
activaciones interrumpidas se marcan como fallidas y no se repiten al reiniciar;
revisa la versión activa antes de reintentar.

### Carpeta current real

Las nuevas aplicaciones de la UI usan **Carpeta current real (Linux/macOS)**.
Al introducir el nombre se completan `/srv/deployd/<app>/releases` y su ruta
`current`. Configura los artefactos permitidos, el comando de reinicio y la URL
de salud antes de guardar. El servicio crea las carpetas y verifica que ese
sistema de archivos soporte el intercambio atómico; no configura Nginx.

```text
/srv/deployd/bluedatos/releases/
  current/                         # archivos activos reales, no un enlace
  <sha-anterior>-<id-despliegue>/   # carpeta anterior conservada
```

Usa `release_layout: directory` y
`current_link: /srv/deployd/bluedatos/releases/current`. El nombre histórico
`current_link` identifica la ruta activa en ambos modos. El primer despliegue
desde GitHub crea `current`; los siguientes intercambian las carpetas de forma
atómica y archivan la anterior con su identidad original. No queda una segunda
carpeta de la versión activa. **Activar** usa el mismo intercambio y las pruebas
de salud; **Eliminar archivos** solo borra versiones anteriores permitidas.
Se siguen guardando por defecto la activa y una anterior.

Nginx puede servir `releases/current` directamente o mediante un enlace fijo
como `/var/www/bluedatos.com`. Ese enlace nunca cambia y se configura por
separado cuando la primera versión esté lista. No reemplaces el sitio existente
por un enlace cuyo destino aún no existe.

El instalador de Ubuntu crea `/srv/deployd` con propietario `deployd`. Para
instalaciones existentes actualizadas sin ejecutar el instalador, corre una vez:

```bash
sudo install -d -o deployd -g deployd -m 0755 /srv/deployd
```

Las rutas personalizadas también deben permitir escritura al servicio. Los
permisos de lectura para Nginx son independientes: para sitios estáticos usa un
tar que incluya `.` con directorios `0755` y archivos `0644`, y verifica la
lectura como usuario de Nginx. Un ZIP extraído con la umask restrictiva del
servicio no concede esos permisos. No incluyas secretos en archivos públicos.

Se requiere intercambio atómico en el mismo sistema de archivos: Linux
`renameat2(RENAME_EXCHANGE)` o macOS `renamex_np(RENAME_SWAP)`. No hay alternativa
de copia sobre archivos activos ni de dos renombrados. Windows conserva el modo
de enlaces/junctions. Esto no hace atómicos los cambios de base de datos ni la
caché de archivos del navegador.

Cada carpeta contiene `.deployd-release.json`, un archivo de identidad reservado
que no puede venir en el artefacto. Consérvalo si mueves una versión manualmente.
Al iniciar, deployd corrige los nombres de directorios tras un proceso interrumpido
sin eliminar versiones. Identidades inválidas o duplicadas bloquean los cambios
de versión de esa app; la API de administración sigue disponible para diagnóstico.
Para cambios manuales, detén deployd y usa un intercambio atómico, no copies sobre
el sitio activo. Reinicia deployd para reconciliar nombres y verifica la salud.
Los cambios manuales no generan eventos en el historial; usa la UI para un rollback
registrado. Los intentos fallidos después del cambio conservan sus archivos hasta
una limpieza manual o la retención de un despliegue exitoso posterior.

Las apps sin `release_layout` conservan el modo `symlink`. No se permite cambiar
las rutas ni el modo mientras existan versiones: usa una app nueva o planifica
una migración fuera de línea. Los enlaces existentes no se convierten solos.

Por defecto se conservan dos versiones en total: la activa y una anterior para
rollback. La configuración se guarda por aplicación y se puede cambiar:

- `keep_previous: 1` (predeterminado): guarda la activa **más una versión anterior**.
- `keep_previous: 3`: guarda la versión activa **más tres versiones anteriores**.
- `keep_previous: 0`: conserva solo la activa después de la limpieza automática;
  después no habrá rollback local disponible.
- `auto_cleanup: true` (predeterminado): elimina el exceso después de desplegar
  correctamente un artefacto nuevo. Usa `false` para limpiar solo manualmente.

Guardar la configuración no elimina archivos inmediatamente. Durante un despliegue,
la versión previa sigue disponible para rollback automático aunque la retención
esté desactivada. La versión activa siempre está protegida. Si se guardan versiones,
la inmediatamente anterior también está protegida. Las demás
se pueden eliminar manualmente; su historial permanece en SQLite. En modo de
carpeta real, los metadatos de `current` identifican la versión anterior. El modo
de enlaces usa `current.previous`; no uses esa ruta para otros archivos. Los
sitios importados fuera del directorio administrado nunca se eliminan.

Las configuraciones existentes con `keep_releases` siguen funcionando: el total
se convierte a `keep_previous = keep_releases - 1`, conservando su política.
Por ejemplo, `keep_releases: 5` equivale a `keep_previous: 4`.
Un valor anterior `keep_previous: null` ahora usa el valor predeterminado de una versión anterior.

## Notas de despliegue

- **Linux:** [`deploy/deployd.service`](deploy/deployd.service) — unidad de
  systemd, usuario dedicado, reglas sudoers por app para los restarts.
- **Windows:** [`deploy/windows.md`](deploy/windows.md) — servicio NSSM,
  physical path de IIS sobre un junction, `Restart-WebAppPool`.
- **Endurecimiento:** enlaza a localhost detrás de un reverse proxy, mantén
  `/admin` fuera del internet público — checklist completo en
  [`SECURITY.md`](SECURITY.md).

Ejecuta exactamente un proceso deployd por base de estado; ese proceso es
dueño de las colas durables por app. Compila `web/`, sirve `web/dist` desde el
reverse proxy y redirige `/api/*` a deployd removiendo el prefijo `/api`.
Para cambios de base de datos usa migraciones expand/contract, manteniendo la
versión anterior compatible si hace falta un rollback de aplicación.

## Estructura

```
src/deployd/
  main.py                  factory de la app FastAPI + lifespan (inicia el worker)
  config.py                settings, registro de apps, secretos
  security.py              verificación HMAC (firma, ventana, nonce)
  models.py                esquemas de request/response
  api/routes.py            POST /deploys, GET /deploys/{id}, GET /healthz
  api/admin.py             /admin: CRUD del registro, rotación de secretos, redeploy, historial
  worker/queue.py          cola asyncio serializada por app
  worker/runner.py         el pipeline de despliegue de siete pasos
  migrate.py               CLI deployd-migrate
  store/db.py              store de estado SQLite
web/                       UI de administración en React (Vite + Tailwind)
examples/                  workflow de CI + script de notificación para incorporar
deploy/                    unidad systemd, guía de Windows
tests/                     pytest (API/worker) — web/ usa vitest
```

## Contribuir

Ver [`CONTRIBUTING.md`](CONTRIBUTING.md). Reportes de seguridad:
[`SECURITY.md`](SECURITY.md).

## Licencia

Apache-2.0 — ver [`LICENSE`](LICENSE) y [`NOTICE`](NOTICE).
