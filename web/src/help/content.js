// Short answers to the questions operators actually ask. Keep each answer to a few
// sentences; longer material belongs in the README.
const en = [
  {
    q: 'What does deployd do, in one paragraph?',
    a: 'Your CI builds the app and publishes the output as a GitHub release asset. It then sends one signed HTTPS request to this server with the commit SHA and the artifact checksum. deployd downloads the artifact, verifies it, unpacks it into a new release folder, runs migrations, swaps it live atomically, restarts the app, checks its health, and rolls back if that fails. No SSH, no build tools and no containers on the server.',
  },
  {
    q: 'What happens step by step during a deploy?',
    a: 'download, verify (SHA256), unpack, migrate, before_cutover hook, cutover, restart, health, after_health hook. Anything up to migrate leaves the live release untouched. From cutover on, a failure restores the previous release and restarts it again.',
  },
  {
    q: 'My repository has a frontend and a backend. One app or two?',
    a: 'Two deployd apps, each with its own secret, release folder, restart and health. Download a ZIP for each, rename the workflow per app (deployd-api.yml, deployd-web.yml), add one DEPLOYD_SECRET_<APP> secret per app, and use distinct tag prefixes (api-v*, web-v*) in tag mode.',
  },
  {
    q: 'My app has no website (API, worker, binary). Is it the same?',
    a: 'Yes. Pick Service instead of a static site, set the restart command (for example sudo systemctl restart my-api), a health URL, and a custom build command whose output folder holds the deployable files. Give the deployd user a sudoers line for that one restart command.',
  },
  {
    q: 'Does deployd create the Nginx site, systemd unit or app config?',
    a: 'No. deployd only puts files in the release folder and runs the commands you registered. The app service, its Nginx server block, its environment and secrets must exist before the first deploy. That fixed contract is what keeps a compromised CI from doing anything else on the server.',
  },
  {
    q: 'Can I run my own script after a deploy?',
    a: 'Yes. Set the after_health hook to the script path; it runs in the live release once the health check passed, and a non-zero exit rolls the deploy back. before_cutover runs in the new release before it goes live. If the script needs root, use /usr/bin/sudo as the executable and the script as its argument, with a NOPASSWD sudoers line.',
  },
  {
    q: 'Which environment variables do my commands see?',
    a: 'A clean environment plus DEPLOYD_APP, DEPLOYD_DEPLOY_ID, DEPLOYD_COMMIT_SHA, DEPLOYD_RELEASE_DIR and DEPLOYD_CURRENT. Service secrets never reach them. List any service variable a command needs, such as a database DSN, under Environment variables passed to commands.',
  },
  {
    q: 'Branch trigger or tag trigger?',
    a: 'Branch: deploys come from one branch. Tag: only commits you tag deploy, from any branch, so the tag marks the release. The checkbox underneath only decides automatic versus manual: checked, the push or the tag starts the deploy by itself; unchecked, you press Run workflow in GitHub Actions and pick the branch or tag.',
  },
  {
    q: 'With manual tag mode, how do I deploy?',
    a: 'In GitHub Actions open "Deploy <app>", press Run workflow and choose the tag in the ref picker. Choosing a branch does nothing in tag mode.',
  },
  {
    q: 'Where do I get the workflow for my repository?',
    a: 'App card, GitHub Actions setup tab. Preview workflow shows the file; Save settings and download ZIP gives you .github/workflows/deployd.yml, scripts/notify_deploy.py and instructions. Unzip at the repository root, add the DEPLOYD_URL variable and the DEPLOYD_SECRET_<APP> secret in the repository Actions settings, commit and push.',
  },
  {
    q: 'What does notify_deploy.py do?',
    a: 'It is the CI client. It signs the deploy request with the app secret, posts it to this server, polls the deploy until it finishes, prints each step and fails the workflow if the deploy failed, rolled back, was superseded or cancelled. Standard library only, no tokens of this server involved.',
  },
  {
    q: 'Can anyone download my build artifacts?',
    a: 'Only if the repository is public. For a private repository the release asset needs a token with read access, and deployd verifies the SHA256 before unpacking either way. Keep the server-side GitHub token fine-grained, read-only and scoped to one repository.',
  },
  {
    q: 'Why is there an admin token if the UI is behind a login?',
    a: 'The login protects the browser path. The token protects the API on 127.0.0.1 from local processes, including the apps deployd deploys. Without it, any of them could reconfigure another app. Keep both.',
  },
  {
    q: 'How do I change the login password?',
    a: 'Key icon in the top bar once you are logged in. It changes the Nginx Basic Auth password only; the admin token and app secrets stay. The browser will ask you to sign in again.',
  },
  {
    q: 'How do I roll back?',
    a: 'Manage versions, pick the retained version, Activate. It goes through cutover, restart and health like a deploy, without downloading or migrating. Older code must still work with the current database; use expand/contract migrations.',
  },
  {
    q: 'What do superseded and cancelled mean?',
    a: 'Superseded: a newer deploy for the same app was queued before this one started, so only the newest runs. Cancelled: you cancelled it from the history while it was still queued. Running deploys cannot be cancelled.',
  },
  {
    q: 'What does Freeze do?',
    a: 'A frozen app rejects CI deploys with HTTP 423 until you unfreeze it. Local activation and rollback keep working. Use it during incidents or maintenance windows.',
  },
  {
    q: 'How do I know the new version is really the one serving?',
    a: 'Set a health expectation: a body substring or a header containing {commit_sha}. If the app still answers with the old SHA after restart, the deploy fails and rolls back instead of passing on a 200 from a stale process.',
  },
  {
    q: 'How do I update deployd itself?',
    a: 'On the server run /opt/deployd/deploy/update-ubuntu.sh. It pulls, runs the checks and build before stopping, refuses to stop while a deploy is running, backs up the state database, and restarts. deployd cannot deploy itself through its own queue; the restart would kill the deploy in progress.',
  },
  {
    q: 'Why does the UI not refresh by itself?',
    a: 'By design, to keep the server idle. Press Refresh or the r key. CI still polls its own deploy until it finishes.',
  },
]

const es = [
  {
    q: 'Qué hace deployd, en un párrafo?',
    a: 'Tu CI compila la app y publica la salida como asset de un release de GitHub. Luego envía una sola solicitud HTTPS firmada a este servidor con el SHA del commit y el checksum del artefacto. deployd descarga el artefacto, lo verifica, lo descomprime en una carpeta de release nueva, ejecuta migraciones, lo pone en vivo con un cambio atómico, reinicia la app, revisa su salud y hace rollback si falla. Sin SSH, sin herramientas de build ni contenedores en el servidor.',
  },
  {
    q: 'Qué pasa paso a paso durante un deploy?',
    a: 'download, verify (SHA256), unpack, migrate, hook before_cutover, cutover, restart, health, hook after_health. Hasta migrate el release vivo no se toca. Desde cutover en adelante, un fallo restaura el release anterior y lo reinicia de nuevo.',
  },
  {
    q: 'Mi repositorio tiene frontend y backend. Una app o dos?',
    a: 'Dos apps de deployd, cada una con su secreto, carpeta de releases, restart y salud. Descarga un ZIP para cada una, renombra el workflow por app (deployd-api.yml, deployd-web.yml), agrega un secreto DEPLOYD_SECRET_<APP> por app y usa prefijos de tag distintos (api-v*, web-v*) en modo tag.',
  },
  {
    q: 'Mi app no tiene sitio (API, worker, binario). Es igual?',
    a: 'Sí. Elige Servicio en vez de sitio estático, define el comando de restart (por ejemplo sudo systemctl restart my-api), una URL de salud y un comando de build personalizado cuya carpeta de salida contenga los archivos a desplegar. Dale al usuario deployd una línea de sudoers para ese único comando de restart.',
  },
  {
    q: 'deployd crea el sitio de Nginx, la unidad de systemd o la configuración de la app?',
    a: 'No. deployd solo deja archivos en la carpeta de releases y ejecuta los comandos que registraste. El servicio de la app, su server block de Nginx, su entorno y sus secretos deben existir antes del primer deploy. Ese contrato fijo es lo que impide que un CI comprometido haga otra cosa en el servidor.',
  },
  {
    q: 'Puedo ejecutar mi propio script después de un deploy?',
    a: 'Sí. Pon la ruta del script en el hook after_health; corre en el release vivo cuando el health check pasó, y una salida distinta de cero hace rollback. before_cutover corre en el release nuevo antes de ponerlo en vivo. Si el script necesita root, usa /usr/bin/sudo como ejecutable y el script como argumento, con una línea NOPASSWD en sudoers.',
  },
  {
    q: 'Qué variables de entorno ven mis comandos?',
    a: 'Un entorno limpio más DEPLOYD_APP, DEPLOYD_DEPLOY_ID, DEPLOYD_COMMIT_SHA, DEPLOYD_RELEASE_DIR y DEPLOYD_CURRENT. Los secretos del servicio nunca les llegan. Lista cualquier variable del servicio que un comando necesite, como el DSN de la base de datos, en Variables de entorno que reciben los comandos.',
  },
  {
    q: 'Disparador por rama o por tag?',
    a: 'Rama: los deploys salen de una rama. Tag: solo despliegan los commits que etiquetes, desde cualquier rama, así el tag marca el release. La casilla de abajo solo decide automático o manual: marcada, el push o el tag inician el deploy solos; sin marcar, presionas Run workflow en GitHub Actions y eliges la rama o el tag.',
  },
  {
    q: 'En modo tag manual, cómo despliego?',
    a: 'En GitHub Actions abre "Deploy <app>", presiona Run workflow y elige el tag en el selector de ref. Elegir una rama no hace nada en modo tag.',
  },
  {
    q: 'De dónde saco el workflow para mi repositorio?',
    a: 'Tarjeta de la app, pestaña Configuración de GitHub Actions. Previsualizar workflow muestra el archivo; Guardar ajustes y descargar ZIP te da .github/workflows/deployd.yml, scripts/notify_deploy.py e instrucciones. Descomprime en la raíz del repositorio, agrega la variable DEPLOYD_URL y el secreto DEPLOYD_SECRET_<APP> en los ajustes de Actions del repositorio, haz commit y push.',
  },
  {
    q: 'Qué hace notify_deploy.py?',
    a: 'Es el cliente de CI. Firma la solicitud de deploy con el secreto de la app, la envía a este servidor, consulta el deploy hasta que termina, imprime cada paso y hace fallar el workflow si el deploy falló, hizo rollback, fue reemplazado o cancelado. Solo librería estándar, sin tokens de este servidor.',
  },
  {
    q: 'Cualquiera puede descargar mis artefactos?',
    a: 'Solo si el repositorio es público. En un repositorio privado el asset del release exige un token con acceso de lectura, y deployd verifica el SHA256 antes de descomprimir en cualquier caso. Mantén el token de GitHub del servidor granular, de solo lectura y limitado a un repositorio.',
  },
  {
    q: 'Por qué hay un token de administración si la UI tiene login?',
    a: 'El login protege el camino del navegador. El token protege la API en 127.0.0.1 de los procesos locales, incluidas las apps que deployd despliega. Sin él, cualquiera de ellas podría reconfigurar a otra. Conserva los dos.',
  },
  {
    q: 'Cómo cambio la contraseña de acceso?',
    a: 'Ícono de llave en la barra superior una vez que iniciaste sesión. Solo cambia la contraseña de Basic Auth de Nginx; el token de administración y los secretos de las apps no cambian. El navegador te pedirá ingresar de nuevo.',
  },
  {
    q: 'Cómo hago rollback?',
    a: 'Administrar versiones, elige la versión retenida, Activar. Pasa por cutover, restart y salud como un deploy, sin descargar ni migrar. El código viejo debe seguir funcionando con la base actual; usa migraciones expand/contract.',
  },
  {
    q: 'Qué significan superseded y cancelled?',
    a: 'Superseded: un deploy más nuevo de la misma app entró en cola antes de que este empezara, así que solo corre el más nuevo. Cancelled: lo cancelaste desde el historial mientras seguía en cola. Los deploys en ejecución no se cancelan.',
  },
  {
    q: 'Qué hace Congelar?',
    a: 'Una app congelada rechaza los deploys de CI con HTTP 423 hasta que la descongeles. La activación local y el rollback siguen funcionando. Úsalo en incidentes o ventanas de mantenimiento.',
  },
  {
    q: 'Cómo sé que la versión nueva es la que realmente sirve?',
    a: 'Configura una expectativa de salud: una subcadena del cuerpo o un header que contenga {commit_sha}. Si la app sigue respondiendo con el SHA viejo tras el restart, el deploy falla y hace rollback en vez de pasar con un 200 de un proceso viejo.',
  },
  {
    q: 'Cómo actualizo deployd?',
    a: 'En el servidor ejecuta /opt/deployd/deploy/update-ubuntu.sh. Trae los cambios, corre las pruebas y el build antes de detener, se niega a detener mientras hay un deploy en ejecución, respalda la base de estado y reinicia. deployd no puede desplegarse a sí mismo por su propia cola; el restart mataría el deploy en curso.',
  },
  {
    q: 'Por qué la UI no se actualiza sola?',
    a: 'Por diseño, para mantener el servidor en reposo. Presiona Actualizar o la tecla r. CI sigue consultando su propio deploy hasta que termina.',
  },
]

export const HELP = { en, es }
