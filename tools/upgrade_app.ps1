param(
    [string]$SourceDirectory = $PSScriptRoot,
    [string]$TargetDirectory
)
$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
if (-not $TargetDirectory) { $TargetDirectory = Read-Host 'Existing production application folder (not the new release folder)' }
$source = (Resolve-Path -LiteralPath $SourceDirectory).Path.TrimEnd('\')
$target = (Resolve-Path -LiteralPath $TargetDirectory).Path.TrimEnd('\')
if ($source -eq $target -or $source.StartsWith($target + '\', [StringComparison]::OrdinalIgnoreCase) -or $target.StartsWith($source + '\', [StringComparison]::OrdinalIgnoreCase)) {
    throw 'Source and target must be separate, non-nested folders.'
}
foreach ($root in @($source,$target)) {
    if ((Get-Item -LiteralPath $root).Attributes -band [IO.FileAttributes]::ReparsePoint) { throw 'Linked application folders are not supported.' }
}
$info = Get-Content -LiteralPath (Join-Path $source 'build-info.json') -Raw -Encoding UTF8 | ConvertFrom-Json
$appName = [string]$info.executable
if (-not $appName -or [IO.Path]::GetFileName($appName) -ne $appName -or -not $appName.EndsWith('.exe')) { throw 'Invalid application manifest.' }
foreach ($root in @($source,$target)) {
    if (-not (Test-Path -LiteralPath (Join-Path $root $appName) -PathType Leaf)) { throw 'Application executable is missing.' }
}
if (-not (Test-Path -LiteralPath (Join-Path $target 'bms_test_data.db') -PathType Leaf)) { throw 'Existing production database is missing. This tool only upgrades an existing installation.' }
if ((Get-FileHash -LiteralPath (Join-Path $source $appName) -Algorithm SHA256).Hash -ne $info.sha256) { throw 'Release executable checksum mismatch.' }
if ($info.PSObject.Properties['mode'] -and $info.mode -eq 'onedir' -and -not (Test-Path -LiteralPath (Join-Path $source '_internal') -PathType Container)) { throw 'Incomplete release: _internal is missing.' }
$running = @(Get-CimInstance Win32_Process | Where-Object {
    $_.ExecutablePath -and ($_.ExecutablePath.StartsWith($target + '\', [StringComparison]::OrdinalIgnoreCase) -or $_.ExecutablePath.StartsWith($source + '\', [StringComparison]::OrdinalIgnoreCase))
})
if ($running.Count) { throw 'Close both application versions and their helper processes before upgrading.' }
$mutable = @('bms_test_data.db','bms_test_data.db-wal','bms_test_data.db-shm','config','recipes')
$versionName = (-join ([char[]](0x7248,0x672C,0x4FE1,0x606F))) + '.txt'
$program = @($appName,'_internal','app_version.json','build-info.json',$versionName)
# Reject links before recursive copies, including links within program/data trees.
foreach ($root in @($source,$target)) {
    foreach ($name in ($program + $mutable)) {
        $path = Join-Path $root $name
        if (Test-Path -LiteralPath $path) {
            $links = @(Get-Item -LiteralPath $path; Get-ChildItem -LiteralPath $path -Recurse -Force) | Where-Object { $_.Attributes -band [IO.FileAttributes]::ReparsePoint }
            if ($links) { throw 'Linked files/directories are not supported in an upgrade.' }
        }
    }
}
$backupRoot = Join-Path $target 'update_backups'
if ((Test-Path -LiteralPath $backupRoot) -and ((Get-Item -LiteralPath $backupRoot).Attributes -band [IO.FileAttributes]::ReparsePoint)) { throw 'Backup folder cannot be a link.' }
$backup = Join-Path $backupRoot ((Get-Date -Format 'yyyyMMdd_HHmmss_fff') + '_' + [guid]::NewGuid().ToString('N').Substring(0,8))
New-Item -ItemType Directory -Path $backup | Out-Null
Write-Host 'Backing up the existing program and production data...'
foreach ($name in ($program + $mutable)) {
    $path = Join-Path $target $name
    if (Test-Path -LiteralPath $path) { Copy-Item -LiteralPath $path -Destination (Join-Path $backup $name) -Recurse -Force }
}
function Get-DataHashes {
    $hashes = @{}
    foreach ($name in $mutable) {
        $path = Join-Path $target $name
        if (Test-Path -LiteralPath $path) {
            $item = Get-Item -LiteralPath $path
            $files = if ($item.PSIsContainer) { @(Get-ChildItem -LiteralPath $path -Recurse -File -Force) } else { @($item) }
            foreach ($file in $files) { $hashes[$file.FullName.Substring($target.Length)] = (Get-FileHash -LiteralPath $file.FullName -Algorithm SHA256).Hash }
        }
    }
    return $hashes
}
$before = Get-DataHashes
try {
    Write-Host 'Updating program files only; database, recipes and configuration are retained...'
    foreach ($name in $program) {
        $path = Join-Path $source $name
        if (Test-Path -LiteralPath $path) { Copy-Item -LiteralPath $path -Destination $target -Recurse -Force }
    }
    $after = Get-DataHashes
    if ($before.Count -ne $after.Count) { throw 'Production data file count changed.' }
    foreach ($key in $before.Keys) { if ($before[$key] -ne $after[$key]) { throw "Production data changed: $key" } }
    [ordered]@{time=(Get-Date).ToString('o'); version=$info.app_version; source=$source; target=$target; backup=$backup; preserved=$before} |
        ConvertTo-Json -Depth 8 | Set-Content -LiteralPath (Join-Path $backup 'upgrade-result.json') -Encoding UTF8
} catch {
    # Restore the previous program. Production data is never an upgrade source.
    foreach ($name in $program) {
        $old = Join-Path $backup $name
        if (Test-Path -LiteralPath $old) { Copy-Item -LiteralPath $old -Destination $target -Recurse -Force }
    }
    throw
}
Write-Host "Upgrade complete: $($info.app_version). Start the executable from the EXISTING folder: $target"
Write-Host "Backup: $backup"
