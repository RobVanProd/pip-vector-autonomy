$ErrorActionPreference = "Stop"

$toolRoot = $PSScriptRoot
$vectorRoot = Split-Path -Parent $toolRoot
$relay = Join-Path $toolRoot "tls_relay.py"
$cert = Join-Path $vectorRoot "runtime\chipper\epod\ep.crt"
$key = Join-Path $vectorRoot "runtime\chipper\epod\ep.key"
$logs = Join-Path $vectorRoot "logs"
$outLog = Join-Path $logs "wirepod-tls-relay.out.log"
$errLog = Join-Path $logs "wirepod-tls-relay.err.log"

New-Item -ItemType Directory -Force -Path $logs | Out-Null

& python -u $relay `
  --listen-host 0.0.0.0 `
  --listen-port 443 `
  --backend-host 127.0.0.1 `
  --backend-port 8443 `
  --backend-sni escapepod.local `
  --cert $cert `
  --key $key `
  1>> $outLog 2>> $errLog
