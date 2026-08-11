param(
    [Parameter(Mandatory = $true)][string]$Archive,
    [Parameter(Mandatory = $true)][string]$Destination,
    [switch]$Install
)

$ErrorActionPreference = "Stop"
$archivePath = (Resolve-Path -LiteralPath $Archive).Path
$destinationPath = [IO.Path]::GetFullPath($Destination)

if (Test-Path -LiteralPath $destinationPath) {
    $existing = Get-ChildItem -LiteralPath $destinationPath -Force -ErrorAction Stop | Select-Object -First 1
    if ($null -ne $existing) {
        throw "Destination is not empty; restore stopped to prevent overwrite: $destinationPath"
    }
} else {
    New-Item -ItemType Directory -Path $destinationPath | Out-Null
}

Expand-Archive -LiteralPath $archivePath -DestinationPath $destinationPath
Write-Host "Restored to: $destinationPath"

if ($Install) {
    $installer = Join-Path $destinationPath "deployment\install-production.ps1"
    if (-not (Test-Path -LiteralPath $installer)) {
        throw "Production installer not found in the archive."
    }
    & $installer
}
