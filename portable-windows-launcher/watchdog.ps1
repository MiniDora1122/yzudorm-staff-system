param([string]$ConfigPath = (Join-Path $PSScriptRoot "launcher.ini"))

$ErrorActionPreference = "Stop"

function Write-WatchdogLog([string]$Message) {
    $logDirectory = Join-Path $PSScriptRoot ".venv\logs"
    New-Item -ItemType Directory -Force -Path $logDirectory | Out-Null
    $logPath = Join-Path $logDirectory "watchdog.log"
    if ((Test-Path -LiteralPath $logPath) -and (Get-Item -LiteralPath $logPath).Length -gt 2MB) {
        Move-Item -LiteralPath $logPath -Destination (Join-Path $logDirectory "watchdog.previous.log") -Force
    }
    Add-Content -LiteralPath $logPath `
        -Value ("[{0}] {1}" -f (Get-Date -Format "yyyy-MM-dd HH:mm:ss"), $Message) `
        -Encoding UTF8
}

function Test-ActiveMarker([string]$Path) {
    if (-not (Test-Path -LiteralPath $Path)) { return $false }
    $values = @{}
    foreach ($line in Get-Content -LiteralPath $Path -Encoding UTF8 -ErrorAction SilentlyContinue) {
        if ($line -match '^\s*([^=]+)=(.*)$') { $values[$matches[1].Trim()] = $matches[2].Trim() }
    }
    $ownerPid = 0
    if ([int]::TryParse($values.Pid, [ref]$ownerPid)) {
        $owner = Get-Process -Id $ownerPid -ErrorAction SilentlyContinue
        if ($null -ne $owner) {
            $startedUtc = [DateTime]::MinValue
            if (-not [DateTime]::TryParse($values.StartedUtc, [ref]$startedUtc) -or
                $owner.StartTime.ToUniversalTime() -le $startedUtc.ToUniversalTime().AddMinutes(1)) {
                return $true
            }
        }
    }
    $age = (Get-Date).ToUniversalTime() - (Get-Item -LiteralPath $Path).LastWriteTimeUtc
    if ($values.Count -eq 0 -and $age.TotalMinutes -lt 120) { return $true }
    Remove-Item -LiteralPath $Path -Force -ErrorAction SilentlyContinue
    Write-WatchdogLog ("Removed stale maintenance marker: " + [IO.Path]::GetFileName($Path))
    return $false
}

function Test-AppHealthy([string]$Url) {
    try {
        $response = Invoke-WebRequest -Uri $Url -UseBasicParsing -TimeoutSec 5
        return $response.StatusCode -eq 200 -and $response.Content -match '"service"\s*:\s*"dorm-staff-system"'
    } catch { return $false }
}

$runtime = Join-Path $PSScriptRoot ".venv"
if (Test-Path -LiteralPath (Join-Path $runtime "update-state.ini")) {
    Write-WatchdogLog "An interrupted update requires Launcher recovery; automatic server startup is paused."
    exit 5
}
foreach ($markerName in @("update-in-progress", "maintenance.lock")) {
    if (Test-ActiveMarker (Join-Path $runtime $markerName)) { exit 0 }
}

if (-not (Test-Path -LiteralPath $ConfigPath)) {
    throw "launcher.ini not found: $ConfigPath"
}

$config = @{}
foreach ($line in Get-Content -LiteralPath $ConfigPath -Encoding UTF8) {
    if ($line -match '^\s*([^#;][^=]*)=(.*)$') {
        $config[$matches[1].Trim()] = $matches[2].Trim()
    }
}

$projectSetting = if ([string]::IsNullOrWhiteSpace($config.ProjectPath)) { ".." } else { $config.ProjectPath }
$projectRoot = if ([IO.Path]::IsPathRooted($projectSetting)) {
    [IO.Path]::GetFullPath($projectSetting)
} else {
    [IO.Path]::GetFullPath((Join-Path $PSScriptRoot $projectSetting))
}
$port = 0
if (-not [int]::TryParse($config.Port, [ref]$port) -or $port -lt 1 -or $port -gt 65535) {
    throw "Invalid Port in launcher.ini"
}
$listenAddress = if ($config.ListenAddress -eq "0.0.0.0") { "0.0.0.0" } else { "127.0.0.1" }
$python = Join-Path $PSScriptRoot ".venv\python\python.exe"

if (-not (Test-Path -LiteralPath $python) -or -not (Test-Path -LiteralPath (Join-Path $projectRoot "wsgi.py"))) {
    Write-WatchdogLog "Runtime or project is missing; run Install / Repair in Launcher."
    exit 2
}

$healthUrl = "http://127.0.0.1:$port/healthz"
if (Test-AppHealthy $healthUrl) { exit 0 }

$maintenanceLock = Join-Path $runtime "maintenance.lock"
try {
    try {
        $lockStream = [IO.File]::Open($maintenanceLock, [IO.FileMode]::CreateNew, [IO.FileAccess]::Write, [IO.FileShare]::None)
    } catch [IO.IOException] { exit 0 }
    try {
        $lockText = "Operation=WATCHDOG_START`r`nPid=$PID`r`nStartedUtc=$([DateTime]::UtcNow.ToString('O'))`r`n"
        $lockBytes = [Text.Encoding]::UTF8.GetBytes($lockText)
        $lockStream.Write($lockBytes, 0, $lockBytes.Length)
        $lockStream.Flush()
    } finally { $lockStream.Dispose() }

    if (Test-AppHealthy $healthUrl) { exit 0 }
    $client = [Net.Sockets.TcpClient]::new()
    try {
        $connection = $client.BeginConnect("127.0.0.1", $port, $null, $null)
        if ($connection.AsyncWaitHandle.WaitOne(500) -and $client.Connected) {
            Write-WatchdogLog "Port $port is occupied but the application health check failed; startup skipped."
            exit 3
        }
    } catch { } finally { $client.Dispose() }

    $logDirectory = Join-Path $PSScriptRoot ".venv\logs"
    New-Item -ItemType Directory -Force -Path $logDirectory | Out-Null
    Get-ChildItem -LiteralPath $logDirectory -File -ErrorAction SilentlyContinue |
        Where-Object { $_.Name -like "watchdog-server-*" -and $_.LastWriteTimeUtc -lt [DateTime]::UtcNow.AddDays(-30) } |
        Remove-Item -Force -ErrorAction SilentlyContinue
    $stamp = Get-Date -Format "yyyyMMdd-HHmmss"
    $stdout = Join-Path $logDirectory "watchdog-server-$stamp.out.log"
    $stderr = Join-Path $logDirectory "watchdog-server-$stamp.error.log"
    $env:PYTHONPATH = $projectRoot
    $arguments = @(
        "-m", "waitress",
        "--listen=$listenAddress`:$port",
        "--threads=8",
        "--trusted-proxy=127.0.0.1",
        "--trusted-proxy-count=1",
        "--trusted-proxy-headers=x-forwarded-for x-forwarded-proto x-forwarded-host x-forwarded-port",
        "--ident=dorm-staff-system",
        "wsgi:app"
    )
    $server = Start-Process -FilePath $python -ArgumentList $arguments -WorkingDirectory $projectRoot `
        -WindowStyle Hidden -RedirectStandardOutput $stdout -RedirectStandardError $stderr -PassThru
    Set-Content -LiteralPath (Join-Path $PSScriptRoot ".venv\server.pid") -Value $server.Id -Encoding ASCII

    for ($attempt = 0; $attempt -lt 10; $attempt++) {
        Start-Sleep -Seconds 1
        if (Test-AppHealthy $healthUrl) {
            Write-WatchdogLog "Application started on port $port."
            exit 0
        }
    }
    Write-WatchdogLog "Application start was requested but health check did not pass within 10 seconds."
    exit 4
} finally {
    Remove-Item -LiteralPath $maintenanceLock -Force -ErrorAction SilentlyContinue
}
