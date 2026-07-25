$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
$releaseDir = Join-Path $root 'dist\release'
$stageDir = Join-Path $root 'build\package\PersonalMcpGateway'
if (Test-Path -LiteralPath $stageDir) { Remove-Item -LiteralPath $stageDir -Recurse -Force }
New-Item -ItemType Directory -Path $stageDir, $releaseDir -Force | Out-Null
Get-ChildItem -LiteralPath $root -Force | Where-Object {
    $_.Name -notin @('.git', '.venv', '.pytest_cache', 'build', 'dist')
} | Copy-Item -Destination $stageDir -Recurse -Force
$zip = Join-Path $releaseDir 'PersonalMcpGateway.zip'
Compress-Archive -Path $stageDir -DestinationPath $zip -Force
$hash = Get-FileHash -Algorithm SHA256 -LiteralPath $zip
"$($hash.Hash.ToLowerInvariant())  $([IO.Path]::GetFileName($zip))" |
    Set-Content -LiteralPath (Join-Path $releaseDir 'SHA-256.txt') -Encoding ASCII
uv run cyclonedx-py environment --output-format JSON `
    --output-file (Join-Path $releaseDir 'SBOM.json')
if ($LASTEXITCODE -ne 0) { throw 'SBOM generation failed.' }
