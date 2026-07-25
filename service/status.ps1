$ErrorActionPreference = 'Continue'
$services = Get-Service PoyiPersonalMcpGateway, OpenAISecureMcpTunnel -ErrorAction SilentlyContinue |
    Select-Object Name, Status, StartType
$gateway = try { Invoke-RestMethod 'http://127.0.0.1:8761/readyz' -TimeoutSec 3 } catch { $null }
$tunnel = try { Invoke-RestMethod 'http://127.0.0.1:8877/readyz' -TimeoutSec 3 } catch { $null }
[pscustomobject]@{
    Services = $services
    GatewayReady = $null -ne $gateway
    Gateway = $gateway
    TunnelReady = $null -ne $tunnel
    Tunnel = $tunnel
} | ConvertTo-Json -Depth 8
