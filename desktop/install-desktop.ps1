# Installs the Poyi Control Center desktop board for the current user only.
#
# No elevation, no Windows service, no machine-wide state. The runtime is a
# private virtual environment under %LOCALAPPDATA% built from the repository's
# uv.lock, so a desktop install can never drift from what CI resolved and can
# never disturb the gateway services it monitors.

[CmdletBinding()]
param(
    [string]$InstallDir,
    [switch]$Autostart,
    [switch]$Force,
    [switch]$Launch
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
. (Join-Path $PSScriptRoot 'desktop-common.ps1')

function Invoke-Checked {
    <# Run a native command and fail loudly: uv reports errors through the exit code. #>
    param([Parameter(Mandatory)][string]$Message, [Parameter(Mandatory)][scriptblock]$Action)
    & $Action
    if ($LASTEXITCODE -ne 0) { throw "$Message (exit code $LASTEXITCODE)." }
}

$repositoryRoot = Split-Path -Parent $PSScriptRoot
$layout = Get-DesktopLayout -InstallDir $InstallDir
$python = $layout.Python
$icon = $layout.Icon

if (Test-RunningElevated) {
    Write-Warning ('This installer is user-scoped and does not need administrator rights. ' +
        "It will install for the account that owns $($layout.Root).")
}
if ($null -eq (Get-Command uv -ErrorAction SilentlyContinue)) {
    throw 'uv was not found on PATH. Install it from https://astral.sh/uv and re-run.'
}

Write-Host "Installing $($layout.AppName) into $($layout.Root)" -ForegroundColor Cyan

$stopped = Stop-DesktopProcess -Runtime $layout.Runtime
if ($stopped -gt 0) {
    Write-Host "  Stopped $stopped running instance(s) to release the runtime files."
}

if ($Force -and (Test-Path -LiteralPath $layout.Runtime)) {
    Remove-Item -LiteralPath $layout.Runtime -Recurse -Force
}
New-Item -ItemType Directory -Path $layout.Root -Force | Out-Null

if (-not (Test-Path -LiteralPath $layout.Python)) {
    Write-Host '  Creating the private Python 3.12 runtime...'
    Invoke-Checked 'uv venv failed' { & uv venv --python 3.12 $layout.Runtime }
}
foreach ($interpreter in @($layout.Python, $layout.PythonW)) {
    if (-not (Test-Path -LiteralPath $interpreter)) {
        throw "The runtime is missing $interpreter. Re-run with -Force to rebuild it."
    }
}

# uv's venv pythonw.exe is a launcher trampoline that can hand off to the base
# console python.exe. Copy the real GUI-subsystem interpreter into Scripts: it
# still discovers runtime\pyvenv.cfg and the private site-packages, but its
# process tree can never own or depend on a console window.
$basePrefix = (& $layout.Python -c 'import sys; print(sys.base_prefix)').Trim()
$basePythonW = Join-Path $basePrefix 'pythonw.exe'
if (-not (Test-Path -LiteralPath $basePythonW)) {
    throw "The Python base runtime is missing $basePythonW."
}
Copy-Item -LiteralPath $basePythonW -Destination $layout.Launcher -Force

# Scratch space inside the install root rather than %TEMP%: the wheel that ends
# up installed should be traceable to this run while it is being built, and the
# directory is removed either way.
if (Test-Path -LiteralPath $layout.Build) { Remove-Item -LiteralPath $layout.Build -Recurse -Force }
New-Item -ItemType Directory -Path $layout.Build -Force | Out-Null
Push-Location $repositoryRoot
try {
    $requirements = Join-Path $layout.Build 'requirements.txt'
    Write-Host '  Resolving locked dependencies (desktop extra)...'
    Invoke-Checked 'uv export failed' {
        & uv export --locked --no-dev --extra desktop --no-emit-project `
            --format requirements.txt --output-file $requirements
    }
    Invoke-Checked 'Locked dependency installation failed' {
        & uv pip install --python $python --requirement $requirements
    }

    Write-Host '  Building and installing the gateway wheel...'
    Invoke-Checked 'uv build failed' { & uv build --wheel --out-dir $layout.Build }
    $wheels = @(Get-ChildItem -LiteralPath $layout.Build -Filter '*.whl' -File)
    if ($wheels.Count -ne 1) { throw "Expected exactly one wheel, found $($wheels.Count)." }
    $wheel = $wheels[0].FullName
    Invoke-Checked 'Wheel installation failed' {
        & uv pip install --python $python --no-deps --reinstall $wheel
    }
} finally {
    Pop-Location
    Remove-Item -LiteralPath $layout.Build -Recurse -Force -ErrorAction SilentlyContinue
}

Invoke-Checked 'Desktop import verification failed' {
    & $python -c 'import personal_mcp_gateway.desktop.app, webview, pystray, PIL'
}
# The package name travels as argv, not inside the -c source: Windows PowerShell
# strips double quotes out of native arguments, so a quoted literal in the source
# would reach Python as a bare name.
$version = & $python -c 'import importlib.metadata as m, sys; print(m.version(sys.argv[1]))' `
    personal-mcp-gateway
if ($LASTEXITCODE -ne 0) { throw 'Could not read the installed package version.' }
$version = "$version".Trim()

# The .ico is generated rather than checked in: the shortcut icon and the tray
# icon are then provably the same artwork from the same code path.
Write-Host '  Generating the shortcut icon...'
Invoke-Checked 'Icon generation failed' {
    & $python -c ('import sys; from pathlib import Path; ' +
        'from personal_mcp_gateway.desktop.icons import write_app_icon; ' +
        'write_app_icon(Path(sys.argv[1]))') $icon
}

New-DesktopShortcut -Path $layout.DesktopLink -Layout $layout
New-DesktopShortcut -Path $layout.StartMenuLink -Layout $layout
Write-Host '  Created the Desktop and Start Menu shortcuts.'
if ($Autostart) {
    New-DesktopShortcut -Path $layout.StartupLink -Layout $layout
    Write-Host '  Enabled autostart at sign-in.'
} elseif (Test-Path -LiteralPath $layout.StartupLink) {
    Remove-Item -LiteralPath $layout.StartupLink -Force
    Write-Host '  Disabled autostart (re-run with -Autostart to restore it).'
}

# A warning, not an error: the window needs WebView2 but the install itself is
# complete and correct without it, and Windows 11 ships the runtime already.
$webview2 = Get-WebView2Version
if ($null -eq $webview2) {
    Write-Warning ('The WebView2 runtime was not found. Install the Evergreen ' +
        'Runtime from https://developer.microsoft.com/microsoft-edge/webview2/ ' +
        'before starting the board.')
}

Write-Host ''
Write-Host "$($layout.AppName) $version installed." -ForegroundColor Green
Write-Host "  Runtime   : $($layout.Runtime)"
Write-Host "  Shortcut  : $($layout.DesktopLink)"
Write-Host "  Autostart : $(if ($Autostart) { 'enabled' } else { 'disabled' })"
Write-Host "  WebView2  : $(if ($null -eq $webview2) { 'missing' } else { $webview2 })"
Write-Host 'Verify it with desktop\Verify-PersonalMcpDesktop.cmd.'

if ($Launch) {
    Start-Process -FilePath $layout.Launcher -ArgumentList $layout.Arguments `
        -WorkingDirectory $layout.Root -WindowStyle Hidden
    Write-Host 'Started the board; look for the tray icon.'
}
