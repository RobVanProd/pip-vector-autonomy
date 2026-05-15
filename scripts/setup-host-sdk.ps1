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

Write-Host "Checking SDK import..."
& $python -c "import anki_vector; print('anki_vector import OK')"
