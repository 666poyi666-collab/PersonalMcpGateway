[CmdletBinding(SupportsShouldProcess)]
param(
    [string]$InstallDir = "$env:ProgramFiles\Poyi\PersonalMcpGateway",
    [string]$DataDir = "$env:ProgramData\Poyi\PersonalMcpGateway",
    [switch]$PurgeData
)
$ErrorActionPreference = 'Stop'

foreach ($name in @('OpenAISecureMcpTunnel', 'PoyiPersonalMcpGateway')) {
    $wrapper = Join-Path $InstallDir "$name.exe"
    if (Test-Path -LiteralPath $wrapper) {
        & $wrapper stopwait 20sec 2>$null
        & $wrapper uninstall
    }
}
if (Test-Path -LiteralPath $InstallDir) {
    Remove-Item -LiteralPath $InstallDir -Recurse -Force
}
if ($PurgeData -and (Test-Path -LiteralPath $DataDir)) {
    if ($PSCmdlet.ShouldProcess($DataDir, 'Permanently delete gateway data and encrypted keys')) {
        Remove-Item -LiteralPath $DataDir -Recurse -Force
    }
}
Write-Host 'Services removed. Data was retained unless -PurgeData was confirmed.'
