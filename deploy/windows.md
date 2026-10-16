# deployd on Windows Server (IIS / .NET apps)

> Reference only: automated Windows installation and Windows CI are deferred.
> The supported installer targets Ubuntu. This manual integration has not been
> validated on a real Windows server.

The API, HMAC contract, worker pipeline, and `deployd-migrate` are identical to
Linux. Only the touch-points differ.

## Manual service setup

After provisioning Python, the virtual environment, configuration and a dedicated
service account, register the executable with [NSSM](https://nssm.cc):

```powershell
nssm install deployd "C:\deployd\.venv\Scripts\deployd.exe"
nssm set deployd AppDirectory C:\deployd
```

Configure the service identity before starting it. Restrict `.env` and secret
files to Administrators, SYSTEM and that identity. The identity needs read access
to code and `.env`, and modify access to the state, release and configuration
directories: the management API replaces registry and secret files atomically.
Set a random admin token and keep the listener on loopback behind a protected
reverse proxy. No installer creates these files, permissions or firewall rules.

## Environment variables

Set in a manually provisioned `.env` or in the service environment.

| Variable | Meaning |
| --- | --- |
| `DEPLOYD_ADMIN_TOKEN` | Enables `/admin` and the management UI. At least 32 bytes; treat it as command-execution authority. Read it with `Select-String DEPLOYD_ADMIN_TOKEN C:\deployd\.env`. |
| `DEPLOYD_DB_PATH` | Absolute path of the SQLite state file (`C:\deployd\state\deployd.sqlite3`). |
| `DEPLOYD_APPS_CONFIG` | Absolute path of `apps.yaml`. |
| `DEPLOYD_SECRETS_FILE` | Absolute path of the secrets file holding `DEPLOYD_SECRET_<APP>` and `DEPLOYD_GITHUB_TOKEN_<APP>` entries. Keep its ACL restricted to the service account; POSIX mode `0600` has no equivalent on Windows. |
| `DEPLOYD_BIND_HOST`, `DEPLOYD_BIND_PORT` | Listener address; keep it behind IIS ARR or another TLS reverse proxy. |

## Service management

```powershell
nssm status deployd
nssm restart deployd
nssm edit deployd        # GUI for account, environment, and log settings
```

## Cutover

`current` becomes a directory junction (created automatically on Windows;
junctions need no admin rights, unlike symlinks). Point the IIS site's physical
path at the junction once:

```powershell
Import-Module WebAdministration
Set-ItemProperty "IIS:\Sites\example-api" -Name physicalPath -Value "C:\apps\example-api\current"
```

## apps.yaml example

```yaml
apps:
  example-api:
    releases_dir: C:\apps\example-api\releases
    current_link: C:\apps\example-api\current
    keep_previous: 4
    artifact:
      allowed_url_prefix: "https://github.com/your-org/"
      allowed_redirect_hosts: ["release-assets.githubusercontent.com"]
      allow_private_networks: false
      max_download_bytes: 1073741824
      max_extract_bytes: 2147483648
      max_extract_files: 10000
    migrate:
      command: ["C:\\deployd\\.venv\\Scripts\\deployd-migrate.exe", "--dir", "migrations", "--dsn-env", "EXAMPLE_API_DSN"]
      timeout_seconds: 600
    env_passthrough: [EXAMPLE_API_DSN]
    restart:
      command: ["powershell", "-NoProfile", "-Command", "Restart-WebAppPool example-api"]
      timeout_seconds: 120
    health:
      url: "http://127.0.0.1:8080/healthz"
      retries: 10
      interval_seconds: 3
```

`keep_previous` replaces the deprecated `keep_releases` (`keep_releases: 5`
maps to `keep_previous: 4`). `migrate.command` must name the executable by
absolute path: the service environment does not include `.venv\Scripts` on
`PATH`. `timeout_seconds` (default 600, maximum 3600) bounds each command.

The service account needs IIS permissions for `Restart-WebAppPool` (no sudo
model on Windows; grant via IIS configuration or run the service as an account
with that right).

## Migrations

Install the SQL Server extra plus the Microsoft ODBC Driver for SQL Server:

```powershell
C:\deployd\.venv\Scripts\python.exe -m pip install "deployd[mssql]"
```

Provide the DSN through the service environment (`nssm set deployd
AppEnvironmentExtra EXAMPLE_API_DSN=...`) and list the variable in the app's
`env_passthrough`, since commands otherwise run with a scrubbed environment;
or use a file readable only by the service account (`--dsn-file`). Avoid `--dsn` on the command line: the value
is visible to other users in process listings.

```
Driver={ODBC Driver 18 for SQL Server};Server=DBHOST;Database=AppDb;Trusted_Connection=yes;TrustServerCertificate=yes
```

`deployd-migrate status` lists applied and pending files; `--dry-run` verifies
checksums without applying anything. Migrations run one file per transaction
with `SET XACT_ABORT ON` and a `sp_getapplock` session lock, so two apps sharing
a database never interleave.
