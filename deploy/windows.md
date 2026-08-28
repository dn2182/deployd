# deployd on Windows Server (IIS / .NET apps)

The API, HMAC contract, worker pipeline, and `deployd-migrate` are identical to
Linux. Only the touch-points differ.

## Service (instead of systemd)

Run deployd under [NSSM](https://nssm.cc):

```powershell
nssm install deployd "C:\deployd\.venv\Scripts\python.exe" `
  "-m" "uvicorn" "deployd.main:app" "--host" "127.0.0.1" "--port" "8300"
nssm set deployd AppDirectory C:\deployd
nssm set deployd AppEnvironmentExtra DEPLOYD_APPS_CONFIG=C:\deployd\config\apps.yaml
nssm start deployd
```

Put secrets in the service environment (or `config\secrets.env`), never in git.

## Cutover

`current` becomes a **directory junction** (created automatically on Windows —
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
    keep_releases: 5
    artifact:
      allowed_url_prefix: "https://github.com/your-org/"
    migrate:
      command: ["deployd-migrate", "--dir", "migrations"]
    restart:
      command: ["powershell", "-NoProfile", "-Command", "Restart-WebAppPool example-api"]
    health:
      url: "http://127.0.0.1:8080/healthz"
      retries: 10
      interval_seconds: 3
```

The service account needs IIS permissions for `Restart-WebAppPool` (no sudo
model on Windows — grant via IIS configuration or run the service as an
account with that right).

## Migrations

`pip install "deployd[mssql]"` plus the Microsoft ODBC Driver for SQL Server.
Set `DEPLOYD_MIGRATE_DSN` in the app's environment, e.g.:

```
Driver={ODBC Driver 18 for SQL Server};Server=DBHOST;Database=AppDb;Trusted_Connection=yes;TrustServerCertificate=yes
```
