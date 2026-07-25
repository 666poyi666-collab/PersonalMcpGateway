$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
Add-Type -AssemblyName System.Security
$root = Split-Path -Parent $PSScriptRoot
$dataDir = $env:PERSONAL_MCP_DATA_DIR
if ([string]::IsNullOrWhiteSpace($dataDir)) {
    $dataDir = "$env:ProgramData\Poyi\PersonalMcpGateway"
}
$keyPath = Join-Path $dataDir 'watch-token.dpapi'
if (-not (Test-Path -LiteralPath $keyPath)) { throw 'Watch pairing token is not installed.' }
$encrypted = [Convert]::FromBase64String((Get-Content -Raw -LiteralPath $keyPath).Trim())
$entropy = [Text.Encoding]::UTF8.GetBytes('Poyi.PersonalMcpGateway.v1')
$plainBytes = [Security.Cryptography.ProtectedData]::Unprotect(
    $encrypted, $entropy, [Security.Cryptography.DataProtectionScope]::LocalMachine)
try {
    $env:PERSONAL_MCP_SECRET_WATCH_PHONE_TOKEN = [Text.Encoding]::UTF8.GetString($plainBytes)
    & (Join-Path $root '.venv\Scripts\personal-mcp-gateway.exe') serve
    exit $LASTEXITCODE
} finally {
    $env:PERSONAL_MCP_SECRET_WATCH_PHONE_TOKEN = $null
    [Array]::Clear($plainBytes, 0, $plainBytes.Length)
    [Array]::Clear($encrypted, 0, $encrypted.Length)
}
