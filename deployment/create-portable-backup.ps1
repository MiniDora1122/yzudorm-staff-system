param(
    [string]$Destination,
    [switch]$AllowRunning
)

$ErrorActionPreference = "Stop"
$projectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$python = Join-Path $projectRoot ".venv\Scripts\python.exe"
if (-not (Test-Path -LiteralPath $python)) {
    throw "Virtual environment not found. Run deployment\install-production.ps1 first."
}
if ([string]::IsNullOrWhiteSpace($Destination)) {
    $backupDir = Join-Path $projectRoot "outputs\portable-backups"
    New-Item -ItemType Directory -Force -Path $backupDir | Out-Null
    $Destination = Join-Path $backupDir ("dorm-staff-{0}.zip" -f (Get-Date -Format "yyyyMMdd-HHmmss"))
}

$arguments = @((Join-Path $PSScriptRoot "create_portable_backup.py"), $Destination)
if ($AllowRunning) { $arguments += "--allow-running" }
& $python @arguments
