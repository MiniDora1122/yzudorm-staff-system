param([switch]$Clean)

$ErrorActionPreference = "Stop"
$root = $PSScriptRoot
$output = Join-Path $root "DormAttendanceTerminal.exe"
$kiosk = Join-Path $root "DormAttendanceKiosk.exe"
$projectRoot = Split-Path $root -Parent
$builderPython = Join-Path $projectRoot ".venv\Scripts\python.exe"
$compiler = Join-Path $env:WINDIR "Microsoft.NET\Framework64\v4.0.30319\csc.exe"
if (-not (Test-Path -LiteralPath $compiler)) { throw ".NET Framework C# compiler not found: $compiler" }
if ($Clean -and (Test-Path -LiteralPath $output)) { Remove-Item -LiteralPath $output }
if ($Clean -and (Test-Path -LiteralPath $kiosk)) { Remove-Item -LiteralPath $kiosk }
if (-not (Test-Path -LiteralPath $builderPython)) { throw "Development Python environment not found: $builderPython" }
& $builderPython -m PyInstaller --noconfirm --clean --onefile --windowed `
    --name DormAttendanceKiosk --distpath $root --workpath (Join-Path $root ".build\work") `
    --specpath (Join-Path $root ".build") (Join-Path $root "attendance_terminal.py")
if ($LASTEXITCODE -ne 0 -or -not (Test-Path -LiteralPath $kiosk)) { throw "Attendance kiosk compilation failed." }
& $compiler /nologo /target:winexe /optimize+ /platform:anycpu `
    /reference:System.dll /reference:System.Core.dll /reference:System.Drawing.dll /reference:System.Windows.Forms.dll `
    /out:"$output" (Join-Path $root "DormAttendanceTerminal.cs")
if ($LASTEXITCODE -ne 0 -or -not (Test-Path -LiteralPath $output)) { throw "Terminal launcher compilation failed." }
Write-Output "Built: $output"
Write-Output "Built: $kiosk"
