param(
    [Parameter(Mandatory=$true)][string]$TerminalRoot,
    [Parameter(Mandatory=$true)][int]$ParentProcessId
)

$ErrorActionPreference = "Stop"
$terminalRoot = [IO.Path]::GetFullPath($TerminalRoot).TrimEnd('\')
$deviceData = Join-Path $env:LOCALAPPDATA "DormAttendanceTerminal"
$terminalIni = Join-Path $deviceData "terminal.ini"
$defaultsIni = Join-Path $terminalRoot "terminal-defaults.ini"
$updateMarker = Join-Path $deviceData "update-in-progress"
$log = Join-Path $deviceData "terminal-update.log"
$temporaryRoot = Join-Path ([IO.Path]::GetTempPath()) ("DormAttendanceUpdate-" + [Guid]::NewGuid().ToString("N"))
$archive = Join-Path $temporaryRoot "repository.zip"
$expanded = Join-Path $temporaryRoot "expanded"
$backup = Join-Path $temporaryRoot "backup"

function Log([string]$message) {
    Add-Content -LiteralPath $log -Value ("[{0}] {1}" -f (Get-Date -Format "yyyy-MM-dd HH:mm:ss"), $message) -Encoding UTF8
}

function Read-Setting([string]$name, [string]$fallback) {
    $value = $fallback
    foreach ($path in @($defaultsIni, $terminalIni)) {
        if (-not (Test-Path -LiteralPath $path)) { continue }
        foreach ($line in Get-Content -LiteralPath $path -Encoding UTF8) {
            if ($line -match ('^' + [Regex]::Escape($name) + '=(.*)$')) { $value = $matches[1].Trim() }
        }
    }
    return $value
}

$applied = $false
$newProcess = $null
$createdFiles = [Collections.Generic.List[string]]::new()
try {
    if (-not (Test-Path -LiteralPath $terminalRoot -PathType Container)) { throw "Terminal folder does not exist." }
    New-Item -ItemType Directory -Force -Path $deviceData, $temporaryRoot, $expanded, $backup | Out-Null
    Set-Content -LiteralPath $updateMarker -Value $PID -Encoding ASCII

    $repositoryUrl = Read-Setting "RepositoryUrl" ""
    $branch = Read-Setting "GitBranch" "main"
    $uri = $null
    if (-not [Uri]::TryCreate($repositoryUrl, [UriKind]::Absolute, [ref]$uri) -or
        $uri.Scheme -ne "https" -or $uri.Host -ne "github.com" -or
        -not [string]::IsNullOrEmpty($uri.UserInfo) -or -not [string]::IsNullOrEmpty($uri.Query) -or
        -not [string]::IsNullOrEmpty($uri.Fragment)) {
        throw "Enter a public GitHub HTTPS repository URL without credentials or tokens."
    }
    $parts = @($uri.AbsolutePath.Trim('/') -split '/')
    if ($parts.Count -ne 2 -or $parts[0] -notmatch '^[A-Za-z0-9_.-]+$' -or $parts[1] -notmatch '^[A-Za-z0-9_.-]+(?:\.git)?$') {
        throw "GitHub repository URL must be https://github.com/owner/repository."
    }
    if ($branch -notmatch '^[A-Za-z0-9._/-]+$' -or $branch.Contains('..') -or $branch.StartsWith('/') -or $branch.EndsWith('/')) {
        throw "Git branch name is invalid."
    }
    $owner = $parts[0]
    $repository = $parts[1] -replace '\.git$', ''
    if ([string]::IsNullOrWhiteSpace($repository)) { throw "GitHub repository name is invalid." }
    $escapedBranch = (($branch -split '/') | ForEach-Object { [Uri]::EscapeDataString($_) }) -join '/'
    $archiveUrl = "https://codeload.github.com/$owner/$repository/zip/refs/heads/$escapedBranch"

    [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
    Log "Downloading $repositoryUrl branch $branch."
    Invoke-WebRequest -Uri $archiveUrl -OutFile $archive -UseBasicParsing
    Expand-Archive -LiteralPath $archive -DestinationPath $expanded -Force
    $repositoryRoot = @(Get-ChildItem -LiteralPath $expanded -Directory)
    if ($repositoryRoot.Count -ne 1) { throw "Downloaded repository archive structure is invalid." }
    $source = Join-Path $repositoryRoot[0].FullName "attendance-terminal"
    if (-not (Test-Path -LiteralPath $source -PathType Container)) { $source = $repositoryRoot[0].FullName }

    $required = @("DormAttendanceTerminal.exe", "DormAttendanceKiosk.exe", "configure-autostart.ps1", "terminal-self-update.ps1", "terminal-defaults.ini")
    foreach ($name in $required) {
        if (-not (Test-Path -LiteralPath (Join-Path $source $name) -PathType Leaf)) { throw "Downloaded update is missing $name." }
    }

    Wait-Process -Id $ParentProcessId -Timeout 30 -ErrorAction SilentlyContinue
    if ($null -ne (Get-Process -Id $ParentProcessId -ErrorAction SilentlyContinue)) { throw "Terminal launcher did not close." }

    $sourceFiles = @(Get-ChildItem -LiteralPath $source -File -Recurse)
    $applied = $true
    foreach ($file in $sourceFiles) {
        $relative = $file.FullName.Substring($source.Length).TrimStart('\')
        $target = Join-Path $terminalRoot $relative
        $backupTarget = Join-Path $backup $relative
        if (Test-Path -LiteralPath $target -PathType Leaf) {
            New-Item -ItemType Directory -Force -Path (Split-Path $backupTarget -Parent) | Out-Null
            Copy-Item -LiteralPath $target -Destination $backupTarget -Force
        } else {
            $createdFiles.Add($target)
        }
        New-Item -ItemType Directory -Force -Path (Split-Path $target -Parent) | Out-Null
        Copy-Item -LiteralPath $file.FullName -Destination $target -Force
    }

    $exe = Join-Path $terminalRoot "DormAttendanceTerminal.exe"
    $newProcess = Start-Process -FilePath $exe -ArgumentList "--update-result=success" -WorkingDirectory $terminalRoot -PassThru
    Start-Sleep -Seconds 3
    if ($newProcess.HasExited) { throw "Updated terminal launcher exited during validation." }
    Log "Standalone terminal update completed."
} catch {
    $failure = $_.Exception.Message
    Log ("UPDATE ERROR: " + $failure)
    if ($null -ne $newProcess -and -not $newProcess.HasExited) { Stop-Process -Id $newProcess.Id -Force -ErrorAction SilentlyContinue }
    if ($applied) {
        foreach ($path in $createdFiles) { Remove-Item -LiteralPath $path -Force -ErrorAction SilentlyContinue }
        if (Test-Path -LiteralPath $backup) {
            foreach ($file in Get-ChildItem -LiteralPath $backup -File -Recurse) {
                $relative = $file.FullName.Substring($backup.Length).TrimStart('\')
                $target = Join-Path $terminalRoot $relative
                New-Item -ItemType Directory -Force -Path (Split-Path $target -Parent) | Out-Null
                Copy-Item -LiteralPath $file.FullName -Destination $target -Force
            }
        }
        Log "Previous terminal files restored."
    }
    Wait-Process -Id $ParentProcessId -Timeout 10 -ErrorAction SilentlyContinue
    Add-Type -AssemblyName PresentationFramework
    [System.Windows.MessageBox]::Show("打卡終端更新失敗，已嘗試回復原版本。請查看裝置資料夾內的 terminal-update.log。`n" + $failure, "更新失敗") | Out-Null
    $exe = Join-Path $terminalRoot "DormAttendanceTerminal.exe"
    if (Test-Path -LiteralPath $exe) { Start-Process -FilePath $exe -WorkingDirectory $terminalRoot }
} finally {
    Remove-Item -LiteralPath $updateMarker -Force -ErrorAction SilentlyContinue
    Remove-Item -LiteralPath $temporaryRoot -Recurse -Force -ErrorAction SilentlyContinue
    Remove-Item -LiteralPath $PSCommandPath -Force -ErrorAction SilentlyContinue
}
