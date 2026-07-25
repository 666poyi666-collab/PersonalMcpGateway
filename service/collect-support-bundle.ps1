param([string]$DataDir = "$env:ProgramData\Poyi\PersonalMcpGateway")
$ErrorActionPreference = 'Stop'
$token = Get-Content -Raw -LiteralPath (Join-Path $DataDir 'admin-token')
$output = Join-Path $PWD ('support-bundle-' + (Get-Date -Format 'yyyyMMdd-HHmmss') + '.zip')
Invoke-WebRequest 'http://127.0.0.1:8761/admin/support-bundle' `
    -Headers @{ 'X-Admin-Token' = $token.Trim() } -OutFile $output
Write-Output $output
