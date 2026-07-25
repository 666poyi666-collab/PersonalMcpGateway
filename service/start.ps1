$ErrorActionPreference = 'Stop'
Start-Service PoyiPersonalMcpGateway
$deadline = (Get-Date).AddSeconds(45)
do {
    Start-Sleep -Milliseconds 500
    try { $ready = Invoke-RestMethod 'http://127.0.0.1:8761/readyz' -TimeoutSec 2 }
    catch { $ready = $null }
} until ($null -ne $ready -or (Get-Date) -ge $deadline)
if ($null -eq $ready) { throw 'Gateway did not become ready.' }
Start-Service OpenAISecureMcpTunnel
