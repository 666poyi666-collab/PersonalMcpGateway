$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
Add-Type -AssemblyName System.Security
$root = Split-Path -Parent $PSScriptRoot
$dataDir = $env:PERSONAL_MCP_DATA_DIR
if ([string]::IsNullOrWhiteSpace($dataDir)) {
    $dataDir = "$env:ProgramData\Poyi\PersonalMcpGateway"
}
$keyPath = Join-Path $dataDir 'runtime-key.dpapi'
$tunnelIdPath = Join-Path $dataDir 'tunnel-id'
if (-not (Test-Path -LiteralPath $keyPath)) { throw 'Tunnel runtime key is not installed.' }
if (-not (Test-Path -LiteralPath $tunnelIdPath)) { throw 'Tunnel ID is not installed.' }

$deadline = (Get-Date).AddMinutes(2)
do {
    Start-Sleep -Seconds 1
    try { $ready = Invoke-RestMethod 'http://127.0.0.1:8761/readyz' -TimeoutSec 2 }
    catch { $ready = $null }
} until ($null -ne $ready -or (Get-Date) -ge $deadline)
if ($null -eq $ready) { throw 'Gateway is not ready.' }

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
    & $client.FullName run --control-plane.tunnel-id $tunnelId `
        --mcp.server-url 'url=http://127.0.0.1:8760/mcp,channel=main' `
        --health.listen-addr '127.0.0.1:8877' --log.format json `
        --log.file (Join-Path $dataDir 'tunnel-logs\tunnel.jsonl')
    exit $LASTEXITCODE
} finally {
    $env:CONTROL_PLANE_API_KEY = $null
    [Array]::Clear($plainBytes, 0, $plainBytes.Length)
    [Array]::Clear($encrypted, 0, $encrypted.Length)
}
