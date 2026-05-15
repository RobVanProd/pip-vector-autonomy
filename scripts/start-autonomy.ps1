param(
    [string]$BaseUrl = "http://127.0.0.1:8788",
    [int]$IntervalSeconds = 35,
    [int]$VisionIntervalTicks = 2,
    [double]$SpeakProbability = 0.55,
    [switch]$AllowMotion,
    [switch]$ListenAfterSpeech,
    [switch]$NoListenAfterSpeech,
    [switch]$IgnoreSleep,
    [switch]$NoVision,
    [switch]$Real
)

$ErrorActionPreference = "Stop"

$body = @{
    enabled = $true
    dry_run = -not $Real
    interval_seconds = $IntervalSeconds
    allow_motion = [bool]$AllowMotion
    listen_after_speech = ([bool]$ListenAfterSpeech) -and -not [bool]$NoListenAfterSpeech
    respect_sleep = -not [bool]$IgnoreSleep
    include_vision = -not [bool]$NoVision
    vision_interval_ticks = $VisionIntervalTicks
    speak_probability = $SpeakProbability
    vibe = "Ralph Wiggum loop, curious and gently alive"
    robot_state = @{
        on_charger = $true
        picked_up = $false
        cliff_detected = $false
        obstacle_close = $false
        low_battery = $false
    }
} | ConvertTo-Json -Depth 10

Invoke-RestMethod "$BaseUrl/autonomy/start" -Method Post -ContentType "application/json" -Body $body | ConvertTo-Json -Depth 20
