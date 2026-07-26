# Removes the user-scoped Poyi Control Center install.
#
# Window geometry and theme survive by default, so a reinstall reopens where the
# operator left off; -Purge takes those too.

[CmdletBinding()]
param(
    [string]$InstallDir,
    [switch]$Purge
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
. (Join-Path $PSScriptRoot 'desktop-common.ps1')

$layout = Get-DesktopLayout -InstallDir $InstallDir

$stopped = Stop-DesktopProcess -Runtime $layout.Runtime
if ($stopped -gt 0) { Write-Host "Stopped $stopped running instance(s)." }

foreach ($link in @($layout.DesktopLink, $layout.StartMenuLink, $layout.StartupLink)) {
    if (Test-Path -LiteralPath $link) {
        Remove-Item -LiteralPath $link -Force
        Write-Host "Removed $link"
    }
}
# Only when we emptied it: the folder is shared with any other Poyi shortcut.
if ((Test-Path -LiteralPath $layout.StartMenuDir) -and
    -not @(Get-ChildItem -LiteralPath $layout.StartMenuDir -Force)) {
    Remove-Item -LiteralPath $layout.StartMenuDir -Force
}

foreach ($path in @($layout.Runtime, $layout.Build, $layout.Icon)) {
    if (Test-Path -LiteralPath $path) {
        Remove-Item -LiteralPath $path -Recurse -Force
        Write-Host "Removed $path"
    }
}

if ($Purge) {
    if (Test-Path -LiteralPath $layout.Root) {
        Remove-Item -LiteralPath $layout.Root -Recurse -Force
        Write-Host "Removed $($layout.Root)"
    }
} else {
    Write-Host "Kept saved layout in $($layout.Root) (re-run with -Purge to remove it)."
}

Write-Host 'Poyi Control Center removed.' -ForegroundColor Green
