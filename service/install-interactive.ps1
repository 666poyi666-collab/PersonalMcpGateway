[CmdletBinding()]
param([switch]$Elevated)

$ErrorActionPreference = 'Stop'
$identity = [Security.Principal.WindowsIdentity]::GetCurrent()
$principal = [Security.Principal.WindowsPrincipal]::new($identity)
$isAdministrator = $principal.IsInRole(
    [Security.Principal.WindowsBuiltInRole]::Administrator)

if (-not $isAdministrator) {
    if ($Elevated) { throw 'Administrator elevation was not granted.' }
    $arguments = @(
        '-NoProfile',
        '-ExecutionPolicy', 'Bypass',
        '-File', $PSCommandPath,
        '-Elevated'
    )
    $process = Start-Process -FilePath 'powershell.exe' -ArgumentList $arguments `
        -Verb RunAs -Wait -PassThru
    exit $process.ExitCode
}

$repositoryRoot = Split-Path -Parent $PSScriptRoot
$evidenceDir = Join-Path $repositoryRoot 'evidence'
$transcriptPath = Join-Path $evidenceDir 'windows-install.log'
$resultPath = Join-Path $evidenceDir 'windows-install-result.json'
New-Item -ItemType Directory -Path $evidenceDir -Force | Out-Null
$transcriptStarted = $false
try {
    Start-Transcript -LiteralPath $transcriptPath -Force | Out-Null
    $transcriptStarted = $true
    & (Join-Path $PSScriptRoot 'install.ps1')
    [ordered]@{
        status = 'passed'
        finishedAt = [DateTimeOffset]::UtcNow.ToString('o')
    } | ConvertTo-Json | Set-Content -LiteralPath $resultPath -Encoding UTF8
} catch {
    $message = $_.Exception.Message `
        -replace 'sk-[A-Za-z0-9_-]+', 'sk-[REDACTED]' `
        -replace 'tunnel_[A-Za-z0-9_-]+', 'tunnel_[REDACTED]' `
        -replace '\b(?:\d{1,3}\.){3}\d{1,3}\b', '[REDACTED_IP]'
    [ordered]@{
        status = 'failed'
        finishedAt = [DateTimeOffset]::UtcNow.ToString('o')
        error = $message
    } | ConvertTo-Json | Set-Content -LiteralPath $resultPath -Encoding UTF8
    throw
} finally {
    if ($transcriptStarted) { Stop-Transcript | Out-Null }
}
