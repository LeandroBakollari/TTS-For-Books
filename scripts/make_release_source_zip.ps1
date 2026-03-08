param(
    [string]$Version = "0.1.0"
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
Set-Location $Root

$releaseDir = Join-Path $Root "dist\release"
New-Item -ItemType Directory -Force -Path $releaseDir | Out-Null

$zipPath = Join-Path $releaseDir ("AudiobookTTS-Source-With-Downloader-" + $Version + ".zip")
if (Test-Path $zipPath) {
    Remove-Item $zipPath -Force
}

$excludeRegex = @(
    '^[\\/]*\.git([\\/]|$)',
    '^[\\/]*\.venv([\\/]|$)',
    '^[\\/]*venv([\\/]|$)',
    '^[\\/]*dist([\\/]|$)',
    '^[\\/]*build([\\/]|$)',
    '^[\\/]*assets[\\/]kokoro([\\/]|$)',
    '^[\\/]*assets[\\/]ffmpeg([\\/]|$)',
    '^[\\/]*__pycache__([\\/]|$)'
)

$allFiles = Get-ChildItem -Path $Root -Recurse -File
$toZip = @()
foreach ($f in $allFiles) {
    $rel = $f.FullName.Substring($Root.Length).TrimStart('\')
    $relNorm = $rel -replace '\\', '/'
    $skip = $false
    foreach ($rx in $excludeRegex) {
        if ($relNorm -match $rx) {
            $skip = $true
            break
        }
    }
    if (-not $skip) {
        $toZip += $f.FullName
    }
}

if ($toZip.Count -eq 0) {
    throw "No files selected for zip."
}

Compress-Archive -Path $toZip -DestinationPath $zipPath -CompressionLevel Optimal
Write-Host "Created: $zipPath"
