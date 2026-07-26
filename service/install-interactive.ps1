[CmdletBinding()]
param([switch]$Elevated)

$ErrorActionPreference = 'Stop'
$identity = [Security.Principal.WindowsIdentity]::GetCurrent()
$principal = [Security.Principal.WindowsPrincipal]::new($identity)
$isAdministrator = $principal.IsInRole(
    [Security.Principal.WindowsBuiltInRole]::Administrator)

if (-not $isAdministrator) {
    if ($Elevated) { throw 'Administrator elevation was not granted.' }
    $arguments = @(
        '-NoProfile',
        '-ExecutionPolicy', 'Bypass',
        '-File', $PSCommandPath,
        '-Elevated'
    )
    $process = Start-Process -FilePath 'powershell.exe' -ArgumentList $arguments `
        -Verb RunAs -Wait -PassThru
    exit $process.ExitCode
}

& (Join-Path $PSScriptRoot 'install.ps1')
