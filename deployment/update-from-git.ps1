param(
    [string]$Remote = "origin",
    [string]$Branch = "main",
    [string]$TaskName = "DormStaffSystem-Waitress",
    [string]$BackupDirectory = "",
    [switch]$SkipBackup
)

$ErrorActionPreference = "Stop"
$projectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$python = Join-Path $projectRoot ".venv\Scripts\python.exe"
$envFile = Join-Path $projectRoot ".env"

Set-Location -LiteralPath $projectRoot

if (-not (Test-Path -LiteralPath $python)) {
    throw "Virtual environment not found. Run deployment\install-production.ps1 first."
}
if (-not (Test-Path -LiteralPath $envFile)) {
    throw ".env not found. Restore or configure production settings before updating."
}

$gitRoot = (& git rev-parse --show-toplevel 2>$null)
if ($LASTEXITCODE -ne 0 -or [string]::IsNullOrWhiteSpace($gitRoot)) {
    throw "This project is not connected to a Git repository."
}
if ([IO.Path]::GetFullPath($gitRoot.Trim()) -ne $projectRoot) {
    throw "Git repository root does not match the application root."
}

$dirty = (& git status --porcelain)
if ($LASTEXITCODE -ne 0) {
    throw "Unable to inspect Git working tree."
}
if ($dirty) {
    Write-Host $dirty
    throw "Working tree is not clean. Review local changes before production update."
}

$currentBranch = (& git branch --show-current).Trim()
if ($currentBranch -ne $Branch) {
    throw "Current branch '$currentBranch' does not match requested production branch '$Branch'."
}

$remotes = @(& git remote)
if ($remotes -notcontains $Remote) {
    throw "Git remote '$Remote' is not configured."
}

$oldCommit = (& git rev-parse HEAD).Trim()
$task = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
if ($null -ne $task) {
    Stop-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
    Start-Sleep -Seconds 1
}

if (-not $SkipBackup) {
    if ([string]::IsNullOrWhiteSpace($BackupDirectory)) {
        $BackupDirectory = Join-Path $projectRoot "outputs\portable-backups"
    }
    $backupRoot = [IO.Path]::GetFullPath($BackupDirectory)
    New-Item -ItemType Directory -Force -Path $backupRoot | Out-Null
    $backupFile = Join-Path $backupRoot ("before-git-update-{0}.zip" -f (Get-Date -Format "yyyyMMdd-HHmmss"))
    & $python (Join-Path $PSScriptRoot "create_portable_backup.py") $backupFile
    if ($LASTEXITCODE -ne 0) {
        throw "Pre-update backup failed. Git update was not started."
    }
    Write-Host "Pre-update backup: $backupFile"
}

try {
    & git fetch $Remote $Branch
    if ($LASTEXITCODE -ne 0) { throw "git fetch failed." }

    & git merge --ff-only "$Remote/$Branch"
    if ($LASTEXITCODE -ne 0) { throw "Fast-forward merge failed." }

    & $python -m pip install -r requirements.txt
    if ($LASTEXITCODE -ne 0) { throw "Dependency installation failed." }

    & $python -m flask --app wsgi.py db upgrade
    if ($LASTEXITCODE -ne 0) { throw "Database migration failed." }

    & $python -c "from app import create_app; app=create_app(); print('Application import OK')"
    if ($LASTEXITCODE -ne 0) { throw "Application import check failed." }

    if ($null -ne $task) {
        Start-ScheduledTask -TaskName $TaskName
    }

    $newCommit = (& git rev-parse HEAD).Trim()
    Write-Host "Production update completed."
    Write-Host "Previous commit: $oldCommit"
    Write-Host "Current commit:  $newCommit"
} catch {
    Write-Error $_
    Write-Host "The service has been left stopped for safety."
    Write-Host "Previous commit: $oldCommit"
    Write-Host "Restore the pre-update portable backup before retrying if a migration ran."
    exit 1
}
