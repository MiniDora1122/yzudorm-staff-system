param([switch]$SkipDatabaseUpgrade)

$ErrorActionPreference = "Stop"
$projectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$venvPython = Join-Path $projectRoot ".venv\Scripts\python.exe"
$envFile = Join-Path $projectRoot ".env"
$envTemplate = Join-Path $projectRoot ".env.production.example"

Set-Location -LiteralPath $projectRoot

if (-not (Test-Path -LiteralPath $envFile)) {
    Copy-Item -LiteralPath $envTemplate -Destination $envFile
    throw "Created .env from .env.production.example. Configure SECRET_KEY and HTTPS settings, then run this script again."
}

if (-not (Test-Path -LiteralPath $venvPython)) {
    py -3 -m venv .venv
}

& $venvPython -m pip install --upgrade pip
& $venvPython -m pip install -r requirements.txt

if (-not $SkipDatabaseUpgrade) {
    & $venvPython -m flask --app wsgi.py db upgrade
}

& $venvPython -c "from app import create_app; app=create_app(); print('Production import OK')"
Write-Host "Installation complete. Run deployment\start-production.ps1 to start the service."
