param(
    [string]$BaseUrl = "http://127.0.0.1:8788",
    [switch]$AutoRoute,
    [switch]$Execute,
    [switch]$Real,
    [string]$Model = "tiny.en",
    [int]$VadMode = 2,
    [int]$MinRms = 120
)

$ErrorActionPreference = "Stop"

$body = @{
    enabled = $true
    auto_route = [bool]$AutoRoute
    execute = [bool]$Execute
    dry_run = -not [bool]$Real
    sample_rate = 16000
    vad_mode = $VadMode
    frame_ms = 20
    min_speech_ms = 300
    silence_ms = 700
    max_utterance_ms = 8000
    pre_roll_ms = 300
    min_rms = $MinRms
    stt_model = $Model
    language = "en"
    compute_type = "int8"
    mute_after_route_seconds = 5.0
} | ConvertTo-Json -Depth 10

Invoke-RestMethod "$BaseUrl/listener/start" -Method Post -ContentType "application/json" -Body $body | ConvertTo-Json -Depth 20
