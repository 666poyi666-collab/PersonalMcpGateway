# Shared paths and helpers for the user-scoped desktop shell.
#
# Everything the control center touches lives under %LOCALAPPDATA%, so install,
# verify and uninstall all run as the signed-in user with no elevation. The
# dashboard is a viewer of the gateway, not a component of it: it holds no
# secrets, opens no listener and needs no service account, so asking for UAC
# would buy nothing and would install into the wrong profile if the operator
# ever answered it as a different user.
#
# Kept ASCII-only on purpose: Windows PowerShell 5.1 reads a BOM-less .ps1 as
# ANSI, so non-ASCII text here would arrive mojibaked on a default system.

Set-StrictMode -Version Latest

$script:AppName = 'Poyi Control Center'
$script:ShortcutFile = 'Poyi Control Center.lnk'
$script:StartMenuFolder = 'Poyi'
$script:LaunchArguments = '-m personal_mcp_gateway.desktop.app'
# The evergreen WebView2 runtime registers itself under this fixed product GUID.
$script:WebView2Guid = '{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}'

function Get-DesktopLayout {
    [CmdletBinding()]
    param([string]$InstallDir)

    if ([string]::IsNullOrWhiteSpace($InstallDir)) {
        $InstallDir = Join-Path $env:LOCALAPPDATA 'Poyi\PersonalMcpDesktop'
    }
    $root = [IO.Path]::GetFullPath($InstallDir)
    $runtime = Join-Path $root 'runtime'
    $programs = [Environment]::GetFolderPath('Programs')
    $startMenuDir = Join-Path $programs $script:StartMenuFolder
    [ordered]@{
        AppName       = $script:AppName
        Arguments     = $script:LaunchArguments
        Root          = $root
        Runtime       = $runtime
        Python        = Join-Path $runtime 'Scripts\python.exe'
        PythonW       = Join-Path $runtime 'Scripts\pythonw.exe'
        Launcher      = Join-Path $runtime 'Scripts\PoyiControlCenter.exe'
        Build         = Join-Path $root 'build'
        Icon          = Join-Path $root 'poyi-control-center.ico'
        State         = Join-Path $root 'window-state.json'
        DesktopLink   = Join-Path ([Environment]::GetFolderPath('Desktop')) $script:ShortcutFile
        StartMenuDir  = $startMenuDir
        StartMenuLink = Join-Path $startMenuDir $script:ShortcutFile
        StartupLink   = Join-Path ([Environment]::GetFolderPath('Startup')) $script:ShortcutFile
    }
}

function Test-RunningElevated {
    $identity = [Security.Principal.WindowsIdentity]::GetCurrent()
    $principal = [Security.Principal.WindowsPrincipal]::new($identity)
    $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
}

function Get-DesktopProcess {
    <#
        .SYNOPSIS
        Interpreters running out of this install, and nothing else.

        .DESCRIPTION
        Matched on the executable path, not on the image name: the operator has
        other Python processes and none of them are the installer's business.
    #>
    [CmdletBinding()]
    param([Parameter(Mandatory)][string]$Runtime)

    $prefix = [IO.Path]::GetFullPath($Runtime).TrimEnd('\') + '\'
    @(Get-Process -Name 'python', 'pythonw', 'PoyiControlCenter' -ErrorAction SilentlyContinue | Where-Object {
        $path = $null
        try { $path = $_.Path } catch { $path = $null }
        $path -and $path.StartsWith($prefix, [StringComparison]::OrdinalIgnoreCase)
    })
}

function Expand-ProcessTree {
    <#
        .SYNOPSIS
        Seed process ids plus their Python descendants.

        .DESCRIPTION
        A uv virtual environment installs launcher trampolines, so running
        Scripts\pythonw.exe starts a parent that spawns the real interpreter --
        and the window, the tray icon and the open file handles all belong to
        that child. Expansion is confined to python images so a recycled process
        id can never drag an unrelated program into the set.
    #>
    [CmdletBinding()]
    param([Parameter(Mandatory)][int[]]$ProcessId)

    $candidates = @(Get-CimInstance Win32_Process `
            -Filter "Name='python.exe' OR Name='pythonw.exe'" -ErrorAction SilentlyContinue)
    $ids = [Collections.Generic.HashSet[int]]::new()
    foreach ($seed in $ProcessId) { [void]$ids.Add([int]$seed) }
    # Depth is one in practice; the bound just keeps a surprising tree finite.
    for ($pass = 0; $pass -lt 8; $pass++) {
        $grew = $false
        foreach ($process in $candidates) {
            if ($ids.Contains([int]$process.ParentProcessId) -and
                $ids.Add([int]$process.ProcessId)) {
                $grew = $true
            }
        }
        if (-not $grew) { break }
    }
    @($ids)
}

function Stop-DesktopProcess {
    <# Free the runtime files before rewriting them; returns the count stopped. #>
    [CmdletBinding()]
    param([Parameter(Mandatory)][string]$Runtime)

    $seeds = @(Get-DesktopProcess -Runtime $Runtime | ForEach-Object { $_.Id })
    if ($seeds.Count -eq 0) { return 0 }
    $ids = @(Expand-ProcessTree -ProcessId $seeds)
    foreach ($id in $ids) { Stop-Process -Id $id -Force -ErrorAction SilentlyContinue }
    Start-Sleep -Milliseconds 700
    $ids.Count
}

function Get-WebView2Version {
    <# The installed evergreen runtime version, or $null when it is absent. #>
    $keys = @(
        "HKLM:\SOFTWARE\WOW6432Node\Microsoft\EdgeUpdate\Clients\$script:WebView2Guid",
        "HKLM:\SOFTWARE\Microsoft\EdgeUpdate\Clients\$script:WebView2Guid",
        "HKCU:\SOFTWARE\Microsoft\EdgeUpdate\Clients\$script:WebView2Guid"
    )
    foreach ($key in $keys) {
        $entry = Get-ItemProperty -LiteralPath $key -ErrorAction SilentlyContinue
        if ($null -ne $entry -and -not [string]::IsNullOrWhiteSpace($entry.pv)) { return $entry.pv }
    }
    $null
}

function New-DesktopShortcut {
    <# Point a .lnk at the native GUI-subsystem launcher so no console can exist. #>
    [CmdletBinding()]
    param(
        [Parameter(Mandatory)][string]$Path,
        [Parameter(Mandatory)]$Layout,
        [string]$Description = 'Poyi Control Center - Personal MCP Gateway desktop board'
    )

    New-Item -ItemType Directory -Path (Split-Path -Parent $Path) -Force | Out-Null
    $shell = New-Object -ComObject WScript.Shell
    try {
        $link = $shell.CreateShortcut($Path)
        $link.TargetPath = $Layout.Launcher
        $link.Arguments = $Layout.Arguments
        $link.WorkingDirectory = $Layout.Root
        $link.Description = $Description
        $link.WindowStyle = 7
        if (Test-Path -LiteralPath $Layout.Icon) { $link.IconLocation = "$($Layout.Icon),0" }
        $link.Save()
    } finally {
        [void][Runtime.InteropServices.Marshal]::ReleaseComObject($shell)
    }
}

function Read-DesktopShortcut {
    <# Target and arguments of an existing .lnk, or $null when it is missing. #>
    [CmdletBinding()]
    param([Parameter(Mandatory)][string]$Path)

    if (-not (Test-Path -LiteralPath $Path)) { return $null }
    $shell = New-Object -ComObject WScript.Shell
    try {
        $link = $shell.CreateShortcut($Path)
        [ordered]@{ target = $link.TargetPath; arguments = $link.Arguments }
    } finally {
        [void][Runtime.InteropServices.Marshal]::ReleaseComObject($shell)
    }
}
