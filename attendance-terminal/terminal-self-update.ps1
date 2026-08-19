param([Parameter(Mandatory=$true)][string]$ProjectRoot, [Parameter(Mandatory=$true)][int]$ParentProcessId)

$ErrorActionPreference = "Stop"
$project = [IO.Path]::GetFullPath($ProjectRoot)
$terminalRoot = Join-Path $project "attendance-terminal"
$runtime = Join-Path $project "portable-windows-launcher\.venv"
$git = Join-Path $runtime "git\cmd\git.exe"
$launcherIni = Join-Path $project "portable-windows-launcher\launcher.ini"
$log = Join-Path $terminalRoot "terminal-update.log"
function Log([string]$message) { Add-Content -LiteralPath $log -Value ("[{0}] {1}" -f (Get-Date -Format "yyyy-MM-dd HH:mm:ss"), $message) -Encoding UTF8 }
function Git([string[]]$arguments) {
    $output = & $git -C $project @arguments 2>&1
    foreach ($line in $output) { Log ([string]$line) }
    if ($LASTEXITCODE -ne 0) { throw "git $($arguments -join ' ') failed" }
    return $output
}
$oldCommit = $null
$applied = $false
$newProcess = $null
try {
    if (-not (Test-Path -LiteralPath $git)) { throw "Portable Git is not installed." }
    $dirty = Git @("status", "--porcelain")
    if ($dirty) { throw "Working tree has local changes; update was cancelled to avoid overwriting them." }
    $branch = "main"; $remote = "origin"
    if (Test-Path -LiteralPath $launcherIni) {
        foreach ($line in Get-Content -LiteralPath $launcherIni -Encoding UTF8) {
            if ($line -match '^GitBranch=(.+)$') { $branch = $matches[1].Trim() }
            if ($line -match '^GitRemote=(.+)$') { $remote = $matches[1].Trim() }
        }
    }
    Git @("fetch", "--prune", $remote, $branch) | Out-Null
    Git @("merge-base", "--is-ancestor", "HEAD", "$remote/$branch") | Out-Null
    $oldCommit = ([string](Git @("rev-parse", "HEAD") | Select-Object -Last 1)).Trim()
    Wait-Process -Id $ParentProcessId -Timeout 30 -ErrorAction SilentlyContinue
    if ($null -ne (Get-Process -Id $ParentProcessId -ErrorAction SilentlyContinue)) { throw "Terminal launcher did not close." }
    Git @("merge", "--ff-only", "$remote/$branch") | Out-Null
    $applied = $true
    $exe = Join-Path $terminalRoot "DormAttendanceTerminal.exe"
    $kiosk = Join-Path $terminalRoot "DormAttendanceKiosk.exe"
    if (-not (Test-Path -LiteralPath $exe) -or -not (Test-Path -LiteralPath $kiosk)) { throw "Updated terminal executables are missing." }
    $newProcess = Start-Process -FilePath $exe -ArgumentList "--update-result=success" -WorkingDirectory $terminalRoot -PassThru
    Start-Sleep -Seconds 3
    if ($newProcess.HasExited) { throw "Updated terminal launcher exited during validation." }
    Log "Terminal safe update completed."
} catch {
    $failure = $_.Exception.Message
    Log ("UPDATE ERROR: " + $failure)
    if ($null -ne $newProcess -and -not $newProcess.HasExited) { Stop-Process -Id $newProcess.Id -Force -ErrorAction SilentlyContinue }
    if ($applied -and -not [string]::IsNullOrWhiteSpace($oldCommit)) {
        try { Git @("reset", "--hard", $oldCommit) | Out-Null; Log "Previous terminal version restored." }
        catch { Log ("ROLLBACK ERROR: " + $_.Exception.Message) }
    }
    Add-Type -AssemblyName PresentationFramework
    [System.Windows.MessageBox]::Show("打卡終端更新失敗，已嘗試回復原版本。請查看 terminal-update.log。`n" + $failure, "更新失敗") | Out-Null
    $exe = Join-Path $terminalRoot "DormAttendanceTerminal.exe"
    if (Test-Path -LiteralPath $exe) { Start-Process -FilePath $exe -WorkingDirectory $terminalRoot }
} finally { Remove-Item -LiteralPath $PSCommandPath -Force -ErrorAction SilentlyContinue }
