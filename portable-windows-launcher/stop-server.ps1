param([string]$ConfigPath = (Join-Path $PSScriptRoot "launcher.ini"))

$ErrorActionPreference = "Stop"
$config = @{}
foreach ($line in Get-Content -LiteralPath $ConfigPath -Encoding UTF8) {
    if ($line -match '^\s*([^#;][^=]*)=(.*)$') {
        $config[$matches[1].Trim()] = $matches[2].Trim()
    }
}

$port = 0
if (-not [int]::TryParse($config.Port, [ref]$port) -or $port -lt 1 -or $port -gt 65535) {
    throw "Invalid Port in launcher.ini"
}
$connection = Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue |
    Select-Object -First 1
if ($null -eq $connection) {
    Remove-Item -LiteralPath (Join-Path $PSScriptRoot ".venv\server.pid") -Force -ErrorAction SilentlyContinue
    exit 0
}

$python = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot ".venv\python\python.exe"))
$process = Get-Process -Id $connection.OwningProcess -ErrorAction Stop
if ([string]::IsNullOrWhiteSpace($process.Path) -or
    -not [IO.Path]::GetFullPath($process.Path).Equals($python, [StringComparison]::OrdinalIgnoreCase)) {
    throw "Port $port is not owned by this Launcher's portable Python; refusing to stop it."
}

Stop-Process -Id $process.Id -Force -ErrorAction Stop
$process.WaitForExit(5000)
Remove-Item -LiteralPath (Join-Path $PSScriptRoot ".venv\server.pid") -Force -ErrorAction SilentlyContinue
