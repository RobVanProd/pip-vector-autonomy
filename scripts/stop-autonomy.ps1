param(
    [string]$BaseUrl = "http://127.0.0.1:8788"
)

$ErrorActionPreference = "Stop"

Invoke-RestMethod "$BaseUrl/autonomy/stop" -Method Post | ConvertTo-Json -Depth 20
