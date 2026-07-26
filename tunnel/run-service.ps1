$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
Add-Type -AssemblyName System.Security

function Write-TunnelEvent([string]$Message, [Diagnostics.EventLogEntryType]$Type) {
    try {
        Write-EventLog -LogName Application -Source 'OpenAISecureMcpTunnel' `
            -EventId 1003 -EntryType $Type -Message $Message
    } catch {
        # Event logging must never hide the original service result.
    }
}

function Get-RedactedText([string]$Text) {
    return $Text `
        -replace 'sk-[A-Za-z0-9_-]+', 'sk-[REDACTED]' `
        -replace 'tunnel_[A-Za-z0-9_-]+', 'tunnel_[REDACTED]' `
        -replace '(?i)(api[_-]?key["'' :=]+)[^,"'' ]+', '$1[REDACTED]' `
        -replace '\b(?:\d{1,3}\.){3}\d{1,3}\b', '[REDACTED_IP]'
}

$root = Split-Path -Parent $PSScriptRoot
try {
    Write-TunnelEvent 'Tunnel service script started.' Information
    $dataDir = $env:PERSONAL_MCP_DATA_DIR
    if ([string]::IsNullOrWhiteSpace($dataDir)) {
        $dataDir = "$env:ProgramData\Poyi\PersonalMcpGateway"
    }
    $keyPath = Join-Path $dataDir 'runtime-key.dpapi'
    $tunnelIdPath = Join-Path $dataDir 'tunnel-id'
    if (-not (Test-Path -LiteralPath $keyPath)) { throw 'Tunnel runtime key is not installed.' }
    if (-not (Test-Path -LiteralPath $tunnelIdPath)) { throw 'Tunnel ID is not installed.' }
    Write-TunnelEvent 'Tunnel credential files are accessible.' Information

    $deadline = (Get-Date).AddMinutes(2)
    do {
        Start-Sleep -Seconds 1
        try { $ready = Invoke-RestMethod 'http://127.0.0.1:8761/readyz' -TimeoutSec 2 }
        catch { $ready = $null }
    } until ($null -ne $ready -or (Get-Date) -ge $deadline)
    if ($null -eq $ready) { throw 'Gateway is not ready.' }
    Write-TunnelEvent 'Gateway readiness check passed.' Information

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
        Write-TunnelEvent 'Tunnel client process is starting.' Information
        $outputTail = [Collections.Generic.Queue[string]]::new()
        $previousErrorAction = $ErrorActionPreference
        try {
            $ErrorActionPreference = 'Continue'
            & $client.FullName run --control-plane.tunnel-id $tunnelId `
                --mcp.server-url 'url=http://127.0.0.1:8760/mcp,channel=main' `
                --health.listen-addr '127.0.0.1:8877' --log.format json 2>&1 |
                ForEach-Object {
                    $outputTail.Enqueue((Get-RedactedText ([string]$_)))
                    if ($outputTail.Count -gt 12) { [void]$outputTail.Dequeue() }
                }
            $exitCode = $LASTEXITCODE
        } finally {
            $ErrorActionPreference = $previousErrorAction
        }
        if ($exitCode -ne 0) {
            $detail = Get-RedactedText ($outputTail.ToArray() -join [Environment]::NewLine)
            Write-TunnelEvent "Tunnel client exited with code $exitCode.`n$detail" Error
        }
        exit $exitCode
    } finally {
        $env:CONTROL_PLANE_API_KEY = $null
        [Array]::Clear($plainBytes, 0, $plainBytes.Length)
        [Array]::Clear($encrypted, 0, $encrypted.Length)
    }
} catch {
    $message = Get-RedactedText $_.Exception.Message
    Write-TunnelEvent ("Tunnel service script failed: " + $message) Error
    throw
}
