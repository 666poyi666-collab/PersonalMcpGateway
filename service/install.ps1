[CmdletBinding()]
param(
    [string]$InstallDir = "$env:ProgramFiles\Poyi\PersonalMcpGateway",
    [string]$DataDir = "$env:ProgramData\Poyi\PersonalMcpGateway",
    [string]$TunnelId,
    [Security.SecureString]$RuntimeApiKey,
    [Security.SecureString]$WatchPairingToken
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

function Assert-Administrator {
    $identity = [Security.Principal.WindowsIdentity]::GetCurrent()
    $principal = [Security.Principal.WindowsPrincipal]::new($identity)
    if (-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
        throw 'Run install.ps1 from an elevated PowerShell session.'
    }
}

function Get-VerifiedDownload([string]$Url, [string]$Sha256, [string]$Destination) {
    Invoke-WebRequest -UseBasicParsing -Uri $Url -OutFile $Destination
    $actual = (Get-FileHash -Algorithm SHA256 -LiteralPath $Destination).Hash.ToLowerInvariant()
    if ($actual -ne $Sha256.ToLowerInvariant()) {
        Remove-Item -LiteralPath $Destination -Force
        throw "SHA-256 mismatch for $Url"
    }
}

function Protect-Secret([Security.SecureString]$Secret, [string]$Destination) {
    $pointer = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($Secret)
    try {
        $plain = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($pointer)
        $bytes = [Text.Encoding]::UTF8.GetBytes($plain)
        $entropy = [Text.Encoding]::UTF8.GetBytes('Poyi.PersonalMcpGateway.v1')
        $encrypted = [Security.Cryptography.ProtectedData]::Protect(
            $bytes, $entropy, [Security.Cryptography.DataProtectionScope]::LocalMachine)
        [IO.File]::WriteAllText($Destination, [Convert]::ToBase64String($encrypted))
        [Array]::Clear($bytes, 0, $bytes.Length)
    } finally {
        if ($pointer -ne [IntPtr]::Zero) {
            [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($pointer)
        }
    }
}

function Set-ServiceDataDir([string]$ConfigurationPath, [string]$ResolvedDataDir) {
    [xml]$configuration = Get-Content -Raw -LiteralPath $ConfigurationPath
    foreach ($node in @($configuration.SelectNodes('/service/env'))) {
        if ($node.GetAttribute('name') -eq 'PERSONAL_MCP_DATA_DIR') {
            $node.SetAttribute('value', $ResolvedDataDir)
        }
    }
    $logPathNode = $configuration.SelectSingleNode('/service/logpath')
    if ($null -eq $logPathNode) { throw "Missing logpath in $ConfigurationPath" }
    $logPathNode.InnerText = Join-Path $ResolvedDataDir 'service-logs'
    $configuration.Save($ConfigurationPath)

    [xml]$saved = Get-Content -Raw -LiteralPath $ConfigurationPath
    $dataNode = @($saved.SelectNodes('/service/env')) | Where-Object {
        $_.GetAttribute('name') -eq 'PERSONAL_MCP_DATA_DIR'
    } | Select-Object -First 1
    if ($null -eq $dataNode -or $dataNode.GetAttribute('value') -ne $ResolvedDataDir) {
        throw "Failed to set PERSONAL_MCP_DATA_DIR in $ConfigurationPath"
    }
}

function Set-GatewayRuntime([string]$ConfigurationPath, [string]$PythonExecutable) {
    [xml]$configuration = Get-Content -Raw -LiteralPath $ConfigurationPath
    $executableNode = $configuration.SelectSingleNode('/service/executable')
    $argumentsNode = $configuration.SelectSingleNode('/service/arguments')
    if ($null -eq $executableNode -or $null -eq $argumentsNode) {
        throw "Missing executable or arguments in $ConfigurationPath"
    }
    $executableNode.InnerText = $PythonExecutable
    $argumentsNode.InnerText = '-s -m personal_mcp_gateway.main serve'
    $configuration.Save($ConfigurationPath)

    [xml]$saved = Get-Content -Raw -LiteralPath $ConfigurationPath
    if ($saved.service.executable -ne $PythonExecutable -or
        $saved.service.arguments -ne '-s -m personal_mcp_gateway.main serve') {
        throw "Failed to set the private Python runtime in $ConfigurationPath"
    }
}

Assert-Administrator
Add-Type -AssemblyName System.Security
$sourceRoot = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
$resolvedSource = (Resolve-Path -LiteralPath $sourceRoot).Path
$resolvedInstallParent = [IO.Path]::GetFullPath((Split-Path -Parent $InstallDir))
$resolvedDataParent = [IO.Path]::GetFullPath((Split-Path -Parent $DataDir))
if ($resolvedInstallParent -notlike "$env:ProgramFiles*") { throw 'InstallDir must be under Program Files.' }
if ($resolvedDataParent -notlike "$env:ProgramData*") { throw 'DataDir must be under ProgramData.' }

New-Item -ItemType Directory -Path $InstallDir, $DataDir -Force | Out-Null
Get-ChildItem -LiteralPath $resolvedSource -Force | Where-Object {
    $_.Name -notin @('.git', '.venv', '.pytest_cache', 'dist', 'build')
} | Copy-Item -Destination $InstallDir -Recurse -Force

$gatewayExe = Join-Path $InstallDir 'PoyiPersonalMcpGateway.exe'
$tunnelExe = Join-Path $InstallDir 'OpenAISecureMcpTunnel.exe'
foreach ($service in @(
    @{ Name = 'OpenAISecureMcpTunnel'; Executable = $tunnelExe },
    @{ Name = 'PoyiPersonalMcpGateway'; Executable = $gatewayExe }
)) {
    $installed = Get-Service -Name $service.Name -ErrorAction SilentlyContinue
    if ($null -ne $installed) {
        if ($installed.Status -ne 'Stopped') {
            Stop-Service -Name $service.Name -Force
            (Get-Service -Name $service.Name).WaitForStatus('Stopped', [TimeSpan]::FromSeconds(30))
        }
        & $service.Executable uninstall
        if ($LASTEXITCODE -ne 0) { throw "Failed to uninstall service $($service.Name)." }
    }
}

$dependencies = Get-Content -Raw -LiteralPath (Join-Path $InstallDir 'service\dependencies.json') |
    ConvertFrom-Json
$downloadDir = Join-Path $env:TEMP ('personal-mcp-install-' + [Guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory -Path $downloadDir | Out-Null
try {
    $winsw = Join-Path $downloadDir 'WinSW-x64.exe'
    Get-VerifiedDownload $dependencies.winsw.url $dependencies.winsw.sha256 $winsw
    Copy-Item $winsw (Join-Path $InstallDir 'PoyiPersonalMcpGateway.exe') -Force
    Copy-Item $winsw (Join-Path $InstallDir 'OpenAISecureMcpTunnel.exe') -Force
    Copy-Item (Join-Path $InstallDir 'service\gateway-service.xml') `
        (Join-Path $InstallDir 'PoyiPersonalMcpGateway.xml') -Force
    Copy-Item (Join-Path $InstallDir 'service\tunnel-service.xml') `
        (Join-Path $InstallDir 'OpenAISecureMcpTunnel.xml') -Force
    Set-ServiceDataDir (Join-Path $InstallDir 'PoyiPersonalMcpGateway.xml') `
        ([IO.Path]::GetFullPath($DataDir))
    Set-ServiceDataDir (Join-Path $InstallDir 'OpenAISecureMcpTunnel.xml') `
        ([IO.Path]::GetFullPath($DataDir))

    $tunnelZip = Join-Path $downloadDir 'tunnel-client.zip'
    Get-VerifiedDownload $dependencies.tunnelClient.url $dependencies.tunnelClient.sha256 $tunnelZip
    Expand-Archive -LiteralPath $tunnelZip -DestinationPath (Join-Path $InstallDir 'tunnel-client') -Force
} finally {
    Remove-Item -LiteralPath $downloadDir -Recurse -Force -ErrorAction SilentlyContinue
}

Push-Location $InstallDir
try {
    $pythonInstallDir = Join-Path $InstallDir 'python'
    $previousPythonInstallDir = $env:UV_PYTHON_INSTALL_DIR
    $previousNoUserSite = $env:PYTHONNOUSERSITE
    $env:UV_PYTHON_INSTALL_DIR = $pythonInstallDir
    $env:PYTHONNOUSERSITE = '1'
    & uv python install 3.12 --no-bin --no-registry
    if ($LASTEXITCODE -ne 0) { throw 'uv python install failed.' }
    $pythonExe = (& uv python find 3.12 --managed-python | Select-Object -Last 1).Trim()
    if ($LASTEXITCODE -ne 0 -or -not (Test-Path -LiteralPath $pythonExe)) {
        throw 'Could not resolve the installed Python 3.12 runtime.'
    }
    $runtimeBuildDir = Join-Path $env:TEMP `
        ('personal-mcp-runtime-' + [Guid]::NewGuid().ToString('N'))
    New-Item -ItemType Directory -Path $runtimeBuildDir | Out-Null
    try {
        $requirementsPath = Join-Path $runtimeBuildDir 'requirements.txt'
        & uv export --locked --no-dev --no-emit-project --format requirements.txt `
            --output-file $requirementsPath
        if ($LASTEXITCODE -ne 0) { throw 'uv export failed.' }
        & uv build --wheel --out-dir $runtimeBuildDir
        if ($LASTEXITCODE -ne 0) { throw 'uv build failed.' }
        $wheels = @(Get-ChildItem -LiteralPath $runtimeBuildDir -Filter '*.whl' -File)
        if ($wheels.Count -ne 1) { throw 'Expected exactly one gateway wheel.' }
        & uv pip install --python $pythonExe --break-system-packages `
            --requirement $requirementsPath
        if ($LASTEXITCODE -ne 0) { throw 'Locked runtime dependency installation failed.' }
        & uv pip install --python $pythonExe --break-system-packages --no-deps `
            --reinstall $wheels[0].FullName
        if ($LASTEXITCODE -ne 0) { throw 'Gateway wheel installation failed.' }
    } finally {
        Remove-Item -LiteralPath $runtimeBuildDir -Recurse -Force -ErrorAction SilentlyContinue
    }
    & $pythonExe -c 'import mcp, personal_mcp_gateway, pywintypes, uvicorn, win32crypt'
    if ($LASTEXITCODE -ne 0) { throw 'Private Python runtime import verification failed.' }
    Set-GatewayRuntime (Join-Path $InstallDir 'PoyiPersonalMcpGateway.xml') $pythonExe
} finally {
    $env:UV_PYTHON_INSTALL_DIR = $previousPythonInstallDir
    $env:PYTHONNOUSERSITE = $previousNoUserSite
    Pop-Location
}

if ($null -ne $WatchPairingToken) {
    Protect-Secret $WatchPairingToken (Join-Path $DataDir 'watch-token.dpapi')
}

if (-not [string]::IsNullOrWhiteSpace($TunnelId)) {
    if ($TunnelId -notmatch '^tunnel_[A-Za-z0-9_-]+$') { throw 'Invalid TunnelId.' }
    if ($null -eq $RuntimeApiKey) {
        $RuntimeApiKey = Read-Host 'OpenAI Tunnel Runtime API Key' -AsSecureString
    }
    Protect-Secret $RuntimeApiKey (Join-Path $DataDir 'runtime-key.dpapi')
    Set-Content -LiteralPath (Join-Path $DataDir 'tunnel-id') -Value $TunnelId -Encoding ASCII
}

& $gatewayExe install
if ($LASTEXITCODE -ne 0) { throw 'Gateway service installation failed.' }
& $tunnelExe install
if ($LASTEXITCODE -ne 0) { throw 'Tunnel service installation failed.' }

$gatewaySid = 'NT SERVICE\PoyiPersonalMcpGateway'
$tunnelSid = 'NT SERVICE\OpenAISecureMcpTunnel'
$gatewayLogDir = Join-Path $DataDir 'logs'
$serviceLogDir = Join-Path $DataDir 'service-logs'
$tunnelLogDir = Join-Path $DataDir 'tunnel-logs'
New-Item -ItemType Directory -Path $gatewayLogDir, $serviceLogDir, $tunnelLogDir -Force |
    Out-Null
& icacls $DataDir /inheritance:r /grant:r 'BUILTIN\Administrators:(OI)(CI)F' `
    "$gatewaySid`:(OI)(CI)M" "$tunnelSid`:(RX)" | Out-Null
& icacls $gatewayLogDir /inheritance:r /grant:r 'BUILTIN\Administrators:(OI)(CI)F' `
    "$gatewaySid`:(OI)(CI)M" /T | Out-Null
& icacls $serviceLogDir /inheritance:r /grant:r 'BUILTIN\Administrators:(OI)(CI)F' `
    "$gatewaySid`:(OI)(CI)M" "$tunnelSid`:(OI)(CI)M" /T | Out-Null
& icacls $tunnelLogDir /inheritance:r /grant:r 'BUILTIN\Administrators:(OI)(CI)F' `
    "$tunnelSid`:(OI)(CI)M" /T | Out-Null
foreach ($name in @('gateway.db', 'gateway.db-wal', 'gateway.db-shm',
        'admin-token', 'admin-csrf-token')) {
    $path = Join-Path $DataDir $name
    if (Test-Path -LiteralPath $path) {
        & icacls $path /inheritance:r /grant:r 'BUILTIN\Administrators:F' `
            "$gatewaySid`:M" | Out-Null
    }
}
& icacls (Join-Path $DataDir 'runtime-key.dpapi') /inheritance:r `
    /grant:r 'BUILTIN\Administrators:F' "$tunnelSid`:R" 2>$null | Out-Null
& icacls (Join-Path $DataDir 'tunnel-id') /inheritance:r `
    /grant:r 'BUILTIN\Administrators:F' "$tunnelSid`:R" 2>$null | Out-Null

& $gatewayExe start
$deadline = (Get-Date).AddSeconds(45)
do {
    Start-Sleep -Milliseconds 500
    try { $ready = Invoke-RestMethod 'http://127.0.0.1:8761/readyz' -TimeoutSec 2 }
    catch { $ready = $null }
} until ($null -ne $ready -or (Get-Date) -ge $deadline)
if ($null -eq $ready) { throw 'Gateway did not become ready within 45 seconds.' }
if (Test-Path -LiteralPath (Join-Path $DataDir 'runtime-key.dpapi')) { & $tunnelExe start }

Write-Host "Installed Personal MCP Gateway in $InstallDir" -ForegroundColor Green
