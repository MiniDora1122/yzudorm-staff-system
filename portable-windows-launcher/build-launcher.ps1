param([switch]$Clean)

$ErrorActionPreference = "Stop"
$launcherRoot = $PSScriptRoot
$output = Join-Path $launcherRoot "DormStaffLauncher.exe"
$compiler = Join-Path $env:WINDIR "Microsoft.NET\Framework64\v4.0.30319\csc.exe"

if (-not (Test-Path -LiteralPath $compiler)) {
    throw ".NET Framework C# compiler not found: $compiler"
}
if ($Clean -and (Test-Path -LiteralPath $output)) {
    Remove-Item -LiteralPath $output
}

& $compiler /nologo /target:winexe /optimize+ /platform:anycpu /win32manifest:"$launcherRoot\app.manifest" `
    /reference:System.dll /reference:System.Core.dll /reference:System.Drawing.dll `
    /reference:System.Windows.Forms.dll /reference:System.IO.Compression.dll `
    /reference:System.IO.Compression.FileSystem.dll `
    /out:"$output" "$launcherRoot\DormStaffLauncher.cs"

if ($LASTEXITCODE -ne 0 -or -not (Test-Path -LiteralPath $output)) {
    throw "Launcher compilation failed."
}
Write-Output "Built: $output"
