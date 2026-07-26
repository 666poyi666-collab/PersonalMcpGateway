[CmdletBinding()]
param(
    [ValidateSet('status-1000', 'recovery-100', 'gateway-restart-20',
        'tunnel-restart-20', 'active-24h', 'idle-72h')]
    [string]$Gate,
    [string]$EvidenceDir = "$PSScriptRoot\..\..\evidence",
    [string]$AdminBaseUrl = 'http://127.0.0.1:8761'
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
New-Item -ItemType Directory -Path $EvidenceDir -Force | Out-Null
$path = Join-Path $EvidenceDir "$Gate.json"
$started = [DateTimeOffset]::UtcNow
$checks = 0

function Wait-Endpoint([string]$Uri, [string]$Name, [int]$TimeoutSeconds = 45) {
    $deadline = (Get-Date).AddSeconds($TimeoutSeconds)
    do {
        try { $response = Invoke-RestMethod $Uri -TimeoutSec 3 }
        catch { $response = $null }
        if ($null -ne $response) { return $response }
        Start-Sleep -Milliseconds 500
    } until ((Get-Date) -ge $deadline)
    throw "$Name did not become ready within $TimeoutSeconds seconds."
}

function Test-GatewayReady {
    $response = Wait-Endpoint "$AdminBaseUrl/readyz" 'Gateway'
    if ($response.gateway -ne 'ready') { throw 'Gateway returned a non-ready response.' }
    $script:checks++
}

function Test-TunnelReady {
    [void](Wait-Endpoint 'http://127.0.0.1:8877/readyz' 'Tunnel')
    $service = Get-Service OpenAISecureMcpTunnel
    if ($service.Status -ne 'Running') { throw 'Tunnel service is not running.' }
    $script:checks++
}

function Write-Evidence([string]$Status, [string]$Message) {
    [ordered]@{
        gate = $Gate
        status = $Status
        startedAt = $started.ToString('o')
        finishedAt = [DateTimeOffset]::UtcNow.ToString('o')
        checks = $script:checks
        message = $Message
    } | ConvertTo-Json | Set-Content -LiteralPath $path -Encoding UTF8
}

try {
    switch ($Gate) {
        'status-1000' { 1..1000 | ForEach-Object { Test-GatewayReady } }
        'recovery-100' { 1..100 | ForEach-Object { Test-GatewayReady; Start-Sleep -Milliseconds 100 } }
        'gateway-restart-20' {
            1..20 | ForEach-Object {
                Stop-Service OpenAISecureMcpTunnel
                Restart-Service PoyiPersonalMcpGateway
                Start-Service OpenAISecureMcpTunnel
                Test-GatewayReady
                Test-TunnelReady
            }
        }
        'tunnel-restart-20' {
            1..20 | ForEach-Object {
                Restart-Service OpenAISecureMcpTunnel
                Test-TunnelReady
                Test-GatewayReady
            }
        }
        'active-24h' {
            $deadline = $started.AddHours(24)
            while ([DateTimeOffset]::UtcNow -lt $deadline) {
                Test-GatewayReady
                Write-Evidence 'running' 'Active gate has not reached 24 hours.'
                Start-Sleep -Seconds 60
            }
        }
        'idle-72h' {
            $deadline = $started.AddHours(72)
            while ([DateTimeOffset]::UtcNow -lt $deadline) {
                Test-GatewayReady
                Write-Evidence 'running' 'Idle gate has not reached 72 hours.'
                Start-Sleep -Seconds 900
            }
        }
    }
    Write-Evidence 'passed' 'Gate completed its full configured count or duration.'
} catch {
    Write-Evidence 'failed' $_.Exception.Message
    throw
}
