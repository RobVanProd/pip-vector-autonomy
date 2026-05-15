param(
    [ValidateSet("installer", "zip")]
    [string]$Asset = "installer"
)

$ErrorActionPreference = "Stop"

$root = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
$downloads = Join-Path $root "downloads"
$metadataPath = Join-Path $downloads "wirepod-latest.json"

if (-not (Test-Path $metadataPath)) {
    throw "Missing metadata file: $metadataPath"
}

$metadata = Get-Content -Raw $metadataPath | ConvertFrom-Json

if ($Asset -eq "installer") {
    $url = $metadata.installer
    $expected = $metadata.installer_sha256
} else {
    $url = $metadata.zip
    $expected = $metadata.zip_sha256
}

if (-not $url -or -not $expected) {
    throw "Metadata does not include a URL and SHA-256 for asset '$Asset'."
}

$fileName = Split-Path -Leaf ([Uri]$url).AbsolutePath
$target = Join-Path $downloads $fileName

if (-not (Test-Path $target)) {
    Write-Host "Downloading $fileName..."
    Invoke-WebRequest -Uri $url -OutFile $target
} else {
    Write-Host "Already downloaded: $fileName"
}

$actual = "sha256:" + (Get-FileHash -Algorithm SHA256 -Path $target).Hash.ToLowerInvariant()
if ($actual -ne $expected.ToLowerInvariant()) {
    throw "SHA-256 mismatch for $fileName. Expected $expected, got $actual"
}

Write-Host "Verified $fileName"
Write-Host $target
