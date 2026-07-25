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

function Invoke-GatewayProcess([string]$Executable, [string]$DataDirectory) {
    $logDirectory = Join-Path $DataDirectory 'logs'
    New-Item -ItemType Directory -Path $logDirectory -Force | Out-Null
    $stdoutPath = Join-Path $logDirectory 'gateway-process.out.log'
    $stderrPath = Join-Path $logDirectory 'gateway-process.err.log'
    $process = Start-Process -FilePath $Executable `
        -ArgumentList @('-m', 'personal_mcp_gateway.main', 'serve') -NoNewWindow `
        -RedirectStandardOutput $stdoutPath -RedirectStandardError $stderrPath -Wait -PassThru
    if ($process.ExitCode -ne 0) {
        $tail = @($stdoutPath, $stderrPath) | ForEach-Object {
            if (Test-Path -LiteralPath $_) {
                Get-Content -LiteralPath $_ -Tail 40 -ErrorAction SilentlyContinue
            }
        }
        $message = (($tail -join [Environment]::NewLine) `
            -replace 'sk-[A-Za-z0-9_-]+', 'sk-[REDACTED]' `
            -replace 'tunnel_[A-Za-z0-9_-]+', 'tunnel_[REDACTED]' `
            -replace '\b(?:\d{1,3}\.){3}\d{1,3}\b', '[REDACTED_IP]')
        if ($message.Length -gt 12000) { $message = $message.Substring($message.Length - 12000) }
        Write-ServiceEvent ("Gateway process output before failure:`n" + $message) Error
    }
    return $process.ExitCode
}

$root = Split-Path -Parent $PSScriptRoot
try {
    Write-ServiceEvent 'Gateway service script started.' Information
    $dataDir = $env:PERSONAL_MCP_DATA_DIR
    if ([string]::IsNullOrWhiteSpace($dataDir)) {
        $dataDir = "$env:ProgramData\Poyi\PersonalMcpGateway"
    }
    Write-ServiceEvent 'Gateway data directory resolved.' Information
    $pythonExecutable = Get-ChildItem -LiteralPath (Join-Path $root 'python') `
        -Filter 'python.exe' -Recurse | Select-Object -First 1 -ExpandProperty FullName
    if ([string]::IsNullOrWhiteSpace($pythonExecutable)) {
        throw 'The private Python runtime is missing.'
    }
    $sitePackages = Join-Path $root '.venv\Lib\site-packages'
    $env:PYTHONPATH = @(
        $sitePackages,
        (Join-Path $sitePackages 'win32'),
        (Join-Path $sitePackages 'win32\lib'),
        (Join-Path $sitePackages 'pythonwin')
    ) -join ';'
    $env:PATH = (Join-Path $sitePackages 'pywin32_system32') + ';' + $env:PATH
    $keyPath = Join-Path $dataDir 'watch-token.dpapi'
    if (-not (Test-Path -LiteralPath $keyPath)) {
        Write-ServiceEvent 'Watch credential absent; starting gateway-only mode.' Information
        $exitCode = Invoke-GatewayProcess $pythonExecutable $dataDir
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
        exit (Invoke-GatewayProcess $pythonExecutable $dataDir)
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
