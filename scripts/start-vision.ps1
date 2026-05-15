param(
    [string]$BaseUrl = "http://127.0.0.1:8788",
    [int]$IntervalSeconds = 20,
    [switch]$IgnoreSleep
)

$ErrorActionPreference = "Stop"

$body = @{
    enabled = $true
    interval_seconds = $IntervalSeconds
    respect_sleep = -not [bool]$IgnoreSleep
} | ConvertTo-Json -Depth 5

Invoke-RestMethod "$BaseUrl/vision/start" -Method Post -ContentType "application/json" -Body $body | ConvertTo-Json -Depth 20
