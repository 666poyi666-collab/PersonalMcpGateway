$ErrorActionPreference = 'Stop'
$gateway = Invoke-RestMethod 'http://127.0.0.1:8761/readyz' -TimeoutSec 3
$tunnel = Invoke-RestMethod 'http://127.0.0.1:8877/readyz' -TimeoutSec 3
if ($gateway.gateway -ne 'ready') { throw 'Gateway is not ready.' }
if ($null -eq $tunnel) { throw 'Tunnel is not ready.' }
Write-Host 'Gateway and Secure MCP Tunnel are ready.' -ForegroundColor Green
