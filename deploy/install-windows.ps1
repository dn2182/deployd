#Requires -Version 5.1
#Requires -RunAsAdministrator
<#
.SYNOPSIS
  Installs deployd as a Windows service (NSSM) with a restricted runtime layout.

.DESCRIPTION
  UNTESTED ON REAL WINDOWS: review every step before running on a server.

  Layout under -InstallDir (default C:\deployd):
    .venv\                 Python environment with deployd installed
    .env                   settings, including the generated DEPLOYD_ADMIN_TOKEN
    config\apps.yaml       app registry (empty until apps are added)
    config\secrets.env     per-app signing secrets, ACL restricted to the service account
    state\deployd.sqlite3  runtime state
    logs\                  service stdout/stderr

  Rerunning is safe: existing .env, apps.yaml and secrets.env are never overwritten.

.PARAMETER Source
  Path to the deployd checkout (default: the parent of this script) or a wheel file.
#>
[CmdletBinding()]
param(
    [string]$InstallDir = 'C:\deployd',
    [string]$Source = (Split-Path -Parent $PSScriptRoot),
    [string]$ServiceName = 'deployd',
    [string]$ServiceAccount = 'NT SERVICE\deployd',
    [ValidatePattern('^(\d{1,3}\.){3}\d{1,3}$')]
    [string]$BindHost = '127.0.0.1',
    [ValidateRange(1, 65535)]
    [int]$BindPort = 8300,
    [string]$FirewallRemoteAddress = 'LocalSubnet'
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

function Require-Command([string]$Name, [string]$Hint) {
    if (-not (Get-Command $Name -ErrorAction SilentlyContinue)) {
        throw "$Name is required: $Hint"
    }
}

function New-HexToken([int]$Bytes) {
    $buffer = New-Object byte[] $Bytes
    [System.Security.Cryptography.RandomNumberGenerator]::Create().GetBytes($buffer)
    return ($buffer | ForEach-Object { $_.ToString('x2') }) -join ''
}

function Protect-File([string]$Path, [string]$Account) {
    # Only SYSTEM, Administrators and the service account may read; no inherited ACEs remain.
    & icacls $Path /inheritance:r /grant:r 'SYSTEM:F' 'BUILTIN\Administrators:F' "${Account}:R" | Out-Null
    if ($LASTEXITCODE -ne 0) { throw "icacls failed for $Path" }
}

function Write-Utf8NoBom([string]$Path, [string]$Content) {
    [System.IO.File]::WriteAllText($Path, $Content, (New-Object System.Text.UTF8Encoding $false))
}

Require-Command nssm 'install NSSM (https://nssm.cc) and put nssm.exe on PATH'
if (-not (Test-Path -LiteralPath $Source)) { throw "source not found: $Source" }

$venv = Join-Path $InstallDir '.venv'
$configDir = Join-Path $InstallDir 'config'
$stateDir = Join-Path $InstallDir 'state'
$logDir = Join-Path $InstallDir 'logs'
foreach ($dir in @($InstallDir, $configDir, $stateDir, $logDir)) {
    New-Item -ItemType Directory -Path $dir -Force | Out-Null
}

# Python environment: uv when available, otherwise the system Python launcher.
$uv = Get-Command uv -ErrorAction SilentlyContinue
if ($uv) {
    if (-not (Test-Path -LiteralPath (Join-Path $venv 'Scripts\python.exe'))) {
        & uv venv $venv --python 3.12
        if ($LASTEXITCODE -ne 0) { throw 'uv venv failed' }
    }
    & uv pip install --python (Join-Path $venv 'Scripts\python.exe') --upgrade $Source
    if ($LASTEXITCODE -ne 0) { throw 'uv pip install failed' }
} else {
    Require-Command py 'install Python 3.12+ from python.org or install uv'
    if (-not (Test-Path -LiteralPath (Join-Path $venv 'Scripts\python.exe'))) {
        & py -3 -m venv $venv
        if ($LASTEXITCODE -ne 0) { throw 'python -m venv failed' }
    }
    & (Join-Path $venv 'Scripts\python.exe') -m pip install --upgrade pip $Source
    if ($LASTEXITCODE -ne 0) { throw 'pip install failed' }
}
$exe = Join-Path $venv 'Scripts\deployd.exe'
if (-not (Test-Path -LiteralPath $exe)) { throw "deployd.exe missing after install: $exe" }

# Runtime files are created once and never overwritten by later runs.
$envFile = Join-Path $InstallDir '.env'
$appsFile = Join-Path $configDir 'apps.yaml'
$secretsFile = Join-Path $configDir 'secrets.env'
$tokenGenerated = $false
if (-not (Test-Path -LiteralPath $envFile)) {
    $token = New-HexToken 32
    $tokenGenerated = $true
    $content = @(
        "DEPLOYD_BIND_HOST=$BindHost",
        "DEPLOYD_BIND_PORT=$BindPort",
        "DEPLOYD_DB_PATH=$(Join-Path $stateDir 'deployd.sqlite3')",
        "DEPLOYD_APPS_CONFIG=$appsFile",
        "DEPLOYD_SECRETS_FILE=$secretsFile",
        "DEPLOYD_ADMIN_TOKEN=$token"
    ) -join "`r`n"
    Write-Utf8NoBom $envFile ($content + "`r`n")
}
if (-not (Test-Path -LiteralPath $appsFile)) { Write-Utf8NoBom $appsFile "apps: {}`r`n" }
if (-not (Test-Path -LiteralPath $secretsFile)) { Write-Utf8NoBom $secretsFile '' }

# The service must exist before a virtual account (NT SERVICE\name) can appear in an ACL.
$existing = Get-Service -Name $ServiceName -ErrorAction SilentlyContinue
if ($existing) {
    if ($existing.Status -ne 'Stopped') { Stop-Service -Name $ServiceName -Force }
} else {
    & nssm install $ServiceName $exe
    if ($LASTEXITCODE -ne 0) { throw 'nssm install failed' }
}
$settings = @(
    @('AppDirectory', $InstallDir),
    @('DisplayName', 'deployd deploy agent'),
    @('Description', 'Pull-style deploy agent; management API on the configured bind port'),
    @('Start', 'SERVICE_AUTO_START'),
    @('ObjectName', $ServiceAccount),
    @('AppStdout', (Join-Path $logDir 'deployd.log')),
    @('AppStderr', (Join-Path $logDir 'deployd.log')),
    @('AppRotateFiles', '1'),
    @('AppRotateBytes', '10485760'),
    @('AppExit', 'Default', 'Restart'),
    @('AppRestartDelay', '3000'),
    @('AppStopMethodConsole', '660000')
)
foreach ($setting in $settings) {
    & nssm set $ServiceName @setting
    if ($LASTEXITCODE -ne 0) { throw "nssm set $($setting[0]) failed" }
}

foreach ($path in @($envFile, $secretsFile, $appsFile)) { Protect-File $path $ServiceAccount }
& icacls $stateDir /inheritance:r /grant:r 'SYSTEM:(OI)(CI)F' 'BUILTIN\Administrators:(OI)(CI)F' "${ServiceAccount}:(OI)(CI)M" | Out-Null
if ($LASTEXITCODE -ne 0) { throw "icacls failed for $stateDir" }
& icacls $logDir /grant:r "${ServiceAccount}:(OI)(CI)M" | Out-Null
if ($LASTEXITCODE -ne 0) { throw "icacls failed for $logDir" }
& icacls $InstallDir /grant:r "${ServiceAccount}:(OI)(CI)RX" | Out-Null
if ($LASTEXITCODE -ne 0) { throw "icacls failed for $InstallDir" }

# Loopback binds need no firewall rule; anything else is limited to the given remote scope.
$ruleName = "deployd management ($BindPort)"
if ($BindHost -ne '127.0.0.1') {
    if (-not (Get-NetFirewallRule -DisplayName $ruleName -ErrorAction SilentlyContinue)) {
        New-NetFirewallRule -DisplayName $ruleName -Direction Inbound -Protocol TCP `
            -LocalPort $BindPort -RemoteAddress $FirewallRemoteAddress -Action Allow | Out-Null
    }
} else {
    Get-NetFirewallRule -DisplayName $ruleName -ErrorAction SilentlyContinue | Remove-NetFirewallRule
}

Start-Service -Name $ServiceName
Start-Sleep -Seconds 3
$health = "http://${BindHost}:${BindPort}/healthz"
try {
    Invoke-WebRequest -UseBasicParsing -Uri $health -TimeoutSec 5 | Out-Null
} catch {
    throw "deployd did not answer at $health; check $(Join-Path $logDir 'deployd.log')"
}

Write-Host ''
Write-Host "deployd is running as service '$ServiceName' ($ServiceAccount) on $BindHost`:$BindPort"
if ($tokenGenerated) {
    Write-Host "Admin token generated in $envFile; read it with:"
    Write-Host "  Select-String DEPLOYD_ADMIN_TOKEN $envFile"
} else {
    Write-Host "Existing $envFile kept (admin token unchanged)."
}
Write-Host 'Next steps:'
Write-Host "  1. Register apps in $appsFile (see deploy\windows.md) and restart: nssm restart $ServiceName"
Write-Host "  2. Put per-app secrets in $secretsFile as DEPLOYD_SECRET_<APP>=... or rotate them via the admin API"
Write-Host '  3. Front the bind port with IIS ARR or another TLS reverse proxy before exposing it'
Write-Host "  4. Logs: $(Join-Path $logDir 'deployd.log')"
