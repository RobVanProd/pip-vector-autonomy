param(
    [ValidateSet("mock", "vector-sdk-dry-run", "vector-sdk")]
    [string]$Mode = "vector-sdk-dry-run",
    [string]$Serial = "0dd1fb2d",
    [int]$Port = 8788
)

$ErrorActionPreference = "Stop"

$root = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
$brain = Join-Path $root "brain"
$venv = Join-Path $brain ".venv"
$python = Join-Path $venv "Scripts\python.exe"

if (-not (Test-Path $python)) {
    Write-Host "Creating host venv..."
    python -m venv $venv
}

Write-Host "Installing host dependencies..."
& $python -m pip install --upgrade pip
& $python -m pip install -r (Join-Path $brain "requirements-host.txt")

$env:OLLAMA_BASE_URL = "http://127.0.0.1:11434"
$env:VECTOR_BRAIN_MODEL = "gemma4:e4b"
$env:VECTOR_EXECUTION_MODE = $Mode
$env:VECTOR_SERIAL = $Serial
$env:VECTOR_VISION_MODEL = "moondream:latest,llava:7b"

Write-Host "Starting vector-brain on http://127.0.0.1:$Port in mode $Mode"
& $python -m uvicorn app.main:app --app-dir $brain --host 127.0.0.1 --port $Port
