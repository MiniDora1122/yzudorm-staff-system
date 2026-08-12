param(
    [Parameter(Mandatory = $true)][string]$LauncherDirectory,
    [Parameter(Mandatory = $true)][int]$ParentProcessId
)

$ErrorActionPreference = "Stop"
$launcherRoot = [IO.Path]::GetFullPath($LauncherDirectory)
$configPath = Join-Path $launcherRoot "launcher.ini"
$runtime = Join-Path $launcherRoot ".venv"
$logDirectory = Join-Path $runtime "logs"
$maintenanceMarker = Join-Path $runtime "update-in-progress"
New-Item -ItemType Directory -Force -Path $logDirectory | Out-Null
$logPath = Join-Path $logDirectory "self-update.log"
$launcher = Join-Path $launcherRoot "DormStaffLauncher.exe"
$launcherBackup = Join-Path $runtime "launcher-before-update.exe"

function Write-UpdateLog([string]$Message) {
    Add-Content -LiteralPath $logPath -Value ("[{0}] {1}" -f (Get-Date -Format "yyyy-MM-dd HH:mm:ss"), $Message) -Encoding UTF8
}

function Invoke-Checked([string]$FilePath, [string[]]$Arguments, [string]$WorkingDirectory) {
    Push-Location $WorkingDirectory
    try {
        $output = & $FilePath @Arguments 2>&1
        foreach ($line in $output) { Write-UpdateLog ([string]$line) }
        if ($LASTEXITCODE -ne 0) { throw "$FilePath failed with exit code $LASTEXITCODE" }
    } finally { Pop-Location }
}

$result = "failed"
try {
    Copy-Item -LiteralPath $launcher -Destination $launcherBackup -Force
    Wait-Process -Id $ParentProcessId -Timeout 30 -ErrorAction SilentlyContinue
    if ($null -ne (Get-Process -Id $ParentProcessId -ErrorAction SilentlyContinue)) {
        throw "Launcher did not close within 30 seconds."
    }

    $config = @{}
    foreach ($line in Get-Content -LiteralPath $configPath -Encoding UTF8) {
        if ($line -match '^\s*([^#;][^=]*)=(.*)$') { $config[$matches[1].Trim()] = $matches[2].Trim() }
    }
    $projectSetting = if ([string]::IsNullOrWhiteSpace($config.ProjectPath)) { ".." } else { $config.ProjectPath }
    $projectRoot = if ([IO.Path]::IsPathRooted($projectSetting)) {
        [IO.Path]::GetFullPath($projectSetting)
    } else { [IO.Path]::GetFullPath((Join-Path $launcherRoot $projectSetting)) }
    $git = Join-Path $runtime "git\cmd\git.exe"
    $python = Join-Path $runtime "python\python.exe"
    if (-not (Test-Path -LiteralPath $git) -or -not (Test-Path -LiteralPath $python)) { throw "Portable Git or Python is missing." }

    Write-UpdateLog "Launcher closed; applying fetched Git update."
    Invoke-Checked $git @("merge", "--ff-only", "$($config.GitRemote)/$($config.GitBranch)") $projectRoot
    Invoke-Checked $python @("-m", "pip", "install", "--disable-pip-version-check", "--no-warn-script-location", "-r", (Join-Path $projectRoot "requirements.txt")) $projectRoot
    $env:PYTHONPATH = $projectRoot
    Invoke-Checked $python @("-m", "flask", "--app", "wsgi.py", "db", "upgrade") $projectRoot
    Invoke-Checked $python @("-c", "from app import create_app; create_app(); print('Application import OK')") $projectRoot
    $result = "success"
    Write-UpdateLog "Git safe update completed."
} catch {
    Write-UpdateLog ("ERROR: " + $_.Exception.Message)
} finally {
    Remove-Item -LiteralPath $maintenanceMarker -Force -ErrorAction SilentlyContinue
    if (-not (Test-Path -LiteralPath $launcher) -and (Test-Path -LiteralPath $launcherBackup)) {
        Copy-Item -LiteralPath $launcherBackup -Destination $launcher -Force
    }
    if (Test-Path -LiteralPath $launcher) {
        Start-Process -FilePath $launcher -ArgumentList "--update-result=$result" -WorkingDirectory $launcherRoot
    }
    Remove-Item -LiteralPath $launcherBackup -Force -ErrorAction SilentlyContinue
    Remove-Item -LiteralPath $PSCommandPath -Force -ErrorAction SilentlyContinue
}
