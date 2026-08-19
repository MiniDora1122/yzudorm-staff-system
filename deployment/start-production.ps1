param(
    [string]$ListenAddress = "127.0.0.1",
    [ValidateRange(1, 65535)][int]$Port = 8000,
    [ValidateRange(1, 64)][int]$Threads = 8
)

$ErrorActionPreference = "Stop"
$projectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$python = Join-Path $projectRoot ".venv\Scripts\python.exe"
$envFile = Join-Path $projectRoot ".env"
$logDir = Join-Path $projectRoot "instance\logs"

if (-not (Test-Path -LiteralPath $envFile)) {
    throw ".env not found. Run deployment\install-production.ps1 and finish production configuration."
}
if (-not (Test-Path -LiteralPath $python)) {
    throw "Waitress not found. Run deployment\install-production.ps1 first."
}

New-Item -ItemType Directory -Force -Path $logDir | Out-Null
$logFile = Join-Path $logDir ("waitress-{0}.log" -f (Get-Date -Format "yyyy-MM-dd"))
$listen = "{0}:{1}" -f $ListenAddress, $Port

Set-Location -LiteralPath $projectRoot
# Windows PowerShell 5 wraps a native process's stderr as NativeCommandError.
# Waitress writes normal startup logs to stderr, so keep the long-running
# process alive while still teeing both streams to the daily log.
$ErrorActionPreference = "Continue"
& $python -m waitress "--listen=$listen" "--threads=$Threads" "--trusted-proxy=127.0.0.1" "--trusted-proxy-count=1" "--trusted-proxy-headers=x-forwarded-for x-forwarded-proto x-forwarded-host x-forwarded-port" "--ident=dorm-staff-system" "wsgi:app" 2>&1 |
    Tee-Object -FilePath $logFile -Append
