param([string]$ConfigPath = (Join-Path $PSScriptRoot "launcher.ini"))

$ErrorActionPreference = "Stop"

if (Test-Path -LiteralPath (Join-Path $PSScriptRoot ".venv\update-in-progress")) { exit 0 }

function Write-WatchdogLog([string]$Message) {
    $logDirectory = Join-Path $PSScriptRoot ".venv\logs"
    New-Item -ItemType Directory -Force -Path $logDirectory | Out-Null
    Add-Content -LiteralPath (Join-Path $logDirectory "watchdog.log") `
        -Value ("[{0}] {1}" -f (Get-Date -Format "yyyy-MM-dd HH:mm:ss"), $Message) `
        -Encoding UTF8
}

function Test-AppHealthy([string]$Url) {
    try {
        $response = Invoke-WebRequest -Uri $Url -UseBasicParsing -TimeoutSec 5
        return $response.StatusCode -eq 200 -and $response.Content -match '宿舍工讀生|Dormitory Student Worker System'
    } catch { return $false }
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

$healthUrl = "http://127.0.0.1:$port/auth/login"
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
$stamp = Get-Date -Format "yyyyMMdd-HHmmss"
$stdout = Join-Path $logDirectory "watchdog-server-$stamp.out.log"
$stderr = Join-Path $logDirectory "watchdog-server-$stamp.error.log"
$env:PYTHONPATH = $projectRoot
$arguments = @(
    "-m", "waitress",
    "--listen=$listenAddress`:$port",
    "--threads=8",
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
