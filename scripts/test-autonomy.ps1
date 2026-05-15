param(
    [string]$BaseUrl = "http://127.0.0.1:8787",
    [switch]$AllowMotion,
    [switch]$Real
)

$ErrorActionPreference = "Stop"

$body = @{
    enabled = $false
    dry_run = -not $Real
    interval_seconds = 45
    allow_motion = [bool]$AllowMotion
    listen_after_speech = $false
    respect_sleep = $true
    include_vision = $true
    vision_interval_ticks = 1
    speak_probability = 0.5
    vibe = "Ralph Wiggum loop, curious and gently alive"
    robot_state = @{
        on_charger = $true
        picked_up = $false
        cliff_detected = $false
        obstacle_close = $false
        low_battery = $false
    }
} | ConvertTo-Json -Depth 10

Invoke-RestMethod "$BaseUrl/autonomy/tick" -Method Post -ContentType "application/json" -Body $body | ConvertTo-Json -Depth 20
