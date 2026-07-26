[CmdletBinding()]
param(
    [string]$DataDir = "$env:ProgramData\Poyi\PersonalMcpGateway",
    [string]$OutputPath
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
Add-Type -AssemblyName System.Security

$root = Split-Path -Parent $PSScriptRoot
$resolvedOutputPath = if ([string]::IsNullOrWhiteSpace($OutputPath)) {
    Join-Path $DataDir 'tunnel-doctor-redacted.txt'
} else {
    [IO.Path]::GetFullPath($OutputPath)
}
$keyPath = Join-Path $DataDir 'runtime-key.dpapi'
$tunnelIdPath = Join-Path $DataDir 'tunnel-id'
if (-not (Test-Path -LiteralPath $keyPath)) { throw 'Tunnel runtime key is not installed.' }
if (-not (Test-Path -LiteralPath $tunnelIdPath)) { throw 'Tunnel ID is not installed.' }

$encrypted = [Convert]::FromBase64String((Get-Content -Raw -LiteralPath $keyPath).Trim())
$entropy = [Text.Encoding]::UTF8.GetBytes('Poyi.PersonalMcpGateway.v1')
$plainBytes = [Security.Cryptography.ProtectedData]::Unprotect(
    $encrypted, $entropy, [Security.Cryptography.DataProtectionScope]::LocalMachine)
try {
    $env:CONTROL_PLANE_API_KEY = [Text.Encoding]::UTF8.GetString($plainBytes)
    $tunnelId = (Get-Content -Raw -LiteralPath $tunnelIdPath).Trim()
    $client = Get-ChildItem -LiteralPath (Join-Path $root 'tunnel-client') `
        -Filter 'tunnel-client.exe' -Recurse | Select-Object -First 1
    if ($null -eq $client) { throw 'tunnel-client.exe is missing.' }
    $previousErrorAction = $ErrorActionPreference
    try {
        $ErrorActionPreference = 'Continue'
        $output = & $client.FullName doctor --control-plane.tunnel-id $tunnelId `
            --mcp.server-url 'url=http://127.0.0.1:8760/mcp,channel=main' `
            --health.listen-addr '127.0.0.1:0' --explain --json 2>&1
        $exitCode = $LASTEXITCODE
    } finally {
        $ErrorActionPreference = $previousErrorAction
    }
    $output | ForEach-Object {
        $_ -replace 'tunnel_[A-Za-z0-9_-]+', 'tunnel_[REDACTED]' `
            -replace '(?i)(api[_-]?key["'' :=]+)[^,"'' ]+', '$1[REDACTED]' `
            -replace '(?i)(https?://)([^/:\s]+)', '$1[REDACTED]'
    } | Set-Content -LiteralPath $resolvedOutputPath -Encoding UTF8
    exit $exitCode
} finally {
    $env:CONTROL_PLANE_API_KEY = $null
    [Array]::Clear($plainBytes, 0, $plainBytes.Length)
    [Array]::Clear($encrypted, 0, $encrypted.Length)
}
