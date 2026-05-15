param(
    [string]$BaseUrl = "http://127.0.0.1:8788",
    [switch]$AllowMotion,
    [switch]$DryRun,
    [switch]$UseBehaviorControl,
    [switch]$ListenAfterSpeech,
    [switch]$NoGemma
)

$ErrorActionPreference = "Stop"

$body = @{
    enabled = $true
    dry_run = [bool]$DryRun
    allow_motion = [bool]$AllowMotion
    use_behavior_control = [bool]$UseBehaviorControl
    route_intents_to_gemma = -not [bool]$NoGemma
    listen_after_speech = [bool]$ListenAfterSpeech
} | ConvertTo-Json -Depth 10

Invoke-RestMethod "$BaseUrl/voice/start" -Method Post -ContentType "application/json" -Body $body | ConvertTo-Json -Depth 20
