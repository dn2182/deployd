# deployd — agente de despliegue estilo pull sobre HTTPS

[![CI](https://github.com/dn2182/deployd/actions/workflows/ci.yml/badge.svg)](https://github.com/dn2182/deployd/actions/workflows/ci.yml)
[![Licencia](https://img.shields.io/github/license/dn2182/deployd)](LICENSE)

*Read this in [English](README.md).*

> **Estado:** versión preliminar. El núcleo está probado, pero `0.1.0` seguirá
> sin publicar hasta completar el primer despliegue en producción y una prueba
> de rollback.

Despliega a tus propios servidores desde GitHub Actions **sin SSH ni FTP
entrante, sin toolchain de build en producción y sin contenedores**. Un solo
endpoint HTTPS con un contrato fijo firmado por HMAC es toda la superficie de
ataque.

## Características

- **Despliegues firmados** — HMAC-SHA256 por aplicación sobre timestamp,
  nonce y cuerpo; protección atómica y persistente contra replay
- **Basado en artefactos** — CI compila y publica; el servidor descarga y
  verifica el SHA256; producción nunca compila nada
- **Cutover seguro, rollback instantáneo** — intentos inmutables en
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
      +--> cutover                           (symlink o junction controlado)
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

## Inicio rápido

```bash
brew install uv pnpm                            # o instálalos para tu sistema
make install                                   # venv de python + deps, deps web (pnpm)
cp .env.example .env                           # define DEPLOYD_ADMIN_TOKEN
cp config/apps.example.yaml config/apps.yaml   # registra tus aplicaciones
make dev                                       # API en 127.0.0.1:8300
make dev-web                                   # UI de administración (Vite dev server)
```

Abre la UI, ingresa el token de administración y rota el secreto de tu app —
ese valor es el `DEPLOYD_SECRET` del CI del repo de tu aplicación.

## Integración con CI

Copia [`examples/github-actions-deploy.yml`](examples/github-actions-deploy.yml)
al repo de tu aplicación e incorpora
[`examples/notify_deploy.py`](examples/notify_deploy.py) como
`scripts/notify_deploy.py`. El repo necesita un secreto (`DEPLOYD_SECRET`) y
una variable (`DEPLOYD_URL`) — el servidor no guarda credenciales de GitHub
en absoluto.

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

## Producción

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
