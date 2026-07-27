[CmdletBinding(SupportsShouldProcess)]
param(
    [Parameter(Mandatory)]
    [string]$ProjectRoot,

    [Parameter(Mandatory)]
    [ValidatePattern('^[a-z0-9][a-z0-9-]{1,62}$')]
    [string]$Id,

    [Parameter(Mandatory)]
    [string]$Name,

    [ValidateSet(
        'cloud_allowed',
        'sensitive_cloud_allowed',
        'cloud_allowed_with_media_exclusions',
        'metadata_cloud_allowed_binaries_opt_in',
        'operational_metadata',
        'local_only'
    )]
    [string]$DataPolicy = 'cloud_allowed',

    [switch]$Force
)

$ErrorActionPreference = 'Stop'
$resolvedRoot = (Resolve-Path -LiteralPath $ProjectRoot).Path
$templatePath = Join-Path $PSScriptRoot '..\templates\project-platform.json'
$targetDirectory = Join-Path $resolvedRoot '.poyi'
$targetPath = Join-Path $targetDirectory 'project-platform.json'

if ((Test-Path -LiteralPath $targetPath) -and -not $Force) {
    throw "Manifest already exists: $targetPath (use -Force to replace it)"
}

$manifest = Get-Content -LiteralPath $templatePath -Raw -Encoding UTF8 | ConvertFrom-Json
$manifest.id = $Id
$manifest.name = $Name
$manifest.dataPolicy = $DataPolicy

if ($DataPolicy -eq 'local_only') {
    $manifest.mcp.status = 'exempt'
    $manifest.sync.status = 'exempt'
    $manifest.sync.authority = 'none'
    $manifest.dataInventory[0].mcpExposure = 'none'
    $manifest.dataInventory[0].coverage = 'exempt'
    $manifest.dataInventory[0].reason = 'Local-only product policy; replace with the approved product reason.'
}

if ($PSCmdlet.ShouldProcess($targetPath, 'Create project platform manifest')) {
    New-Item -ItemType Directory -Force -Path $targetDirectory | Out-Null
    $json = $manifest | ConvertTo-Json -Depth 20
    Set-Content -LiteralPath $targetPath -Value $json -Encoding UTF8
    Write-Output "created: $targetPath"
    Write-Output 'next: fill dataInventory and knownGaps, then add the project to projects.json'
} else {
    Write-Output "planned: $targetPath"
}
