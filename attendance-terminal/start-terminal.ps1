# Copyright (c) 2026 Tay Yang Long. All Rights Reserved. Designed & Developed by Tay Yang Long.
$ErrorActionPreference = "Stop"
$launcher = Join-Path $PSScriptRoot "DormAttendanceTerminal.exe"
if (-not (Test-Path -LiteralPath $launcher)) { throw "DormAttendanceTerminal.exe is missing." }
Start-Process -FilePath $launcher -WorkingDirectory $PSScriptRoot
