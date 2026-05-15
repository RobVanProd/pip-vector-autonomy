param(
    [string]$BaseUrl = "http://127.0.0.1:8788",
    [double]$PollIntervalSeconds = 2.0,
    [switch]$AllowMotion,
    [switch]$ListenAfterSpeech,
    [switch]$Real
)

$ErrorActionPreference = "Stop"

$body = @{
    enabled = $true
    dry_run = -not [bool]$Real
    allow_motion = [bool]$AllowMotion
    poll_interval_seconds = $PollIntervalSeconds
    listen_after_speech = [bool]$ListenAfterSpeech
} | ConvertTo-Json -Depth 10

Invoke-RestMethod "$BaseUrl/sentinel/start" -Method Post -ContentType "application/json" -Body $body | ConvertTo-Json -Depth 20
