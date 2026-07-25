$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
Add-Type -AssemblyName System.Security

function Write-ServiceEvent([string]$Message, [Diagnostics.EventLogEntryType]$Type) {
    try {
        Write-EventLog -LogName Application -Source 'PoyiPersonalMcpGateway' `
            -EventId 1001 -EntryType $Type -Message $Message
    } catch {
        # Event logging must never hide the original service result.
    }
}

$root = Split-Path -Parent $PSScriptRoot
try {
    Write-ServiceEvent 'Gateway service script started.' Information
    $dataDir = $env:PERSONAL_MCP_DATA_DIR
    if ([string]::IsNullOrWhiteSpace($dataDir)) {
        $dataDir = "$env:ProgramData\Poyi\PersonalMcpGateway"
    }
    Write-ServiceEvent 'Gateway data directory resolved.' Information
    $keyPath = Join-Path $dataDir 'watch-token.dpapi'
    if (-not (Test-Path -LiteralPath $keyPath)) {
        Write-ServiceEvent 'Watch credential absent; starting gateway-only mode.' Information
        & (Join-Path $root '.venv\Scripts\personal-mcp-gateway.exe') serve
        $exitCode = $LASTEXITCODE
        if ($exitCode -ne 0) {
            Write-ServiceEvent "Gateway process exited with code $exitCode." Error
        }
        exit $exitCode
    }

    Write-ServiceEvent 'Watch credential present; decrypting for gateway process.' Information
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
} catch {
    $message = $_.Exception.Message `
        -replace 'sk-[A-Za-z0-9_-]+', 'sk-[REDACTED]' `
        -replace 'tunnel_[A-Za-z0-9_-]+', 'tunnel_[REDACTED]' `
        -replace '\b(?:\d{1,3}\.){3}\d{1,3}\b', '[REDACTED_IP]'
    Write-ServiceEvent ("Gateway service script failed: " + $message) Error
    throw
}
