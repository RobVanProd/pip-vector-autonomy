param(
    [string]$BaseUrl = "http://127.0.0.1:8788",
    [int]$AutonomyIntervalSeconds = 35,
    [int]$VisionIntervalSeconds = 20,
    [int]$VisionIntervalTicks = 2,
    [double]$SpeakProbability = 0.55,
    [switch]$AllowMotion,
    [switch]$Real,
    [switch]$NoListener,
    [switch]$NoListenAfterSpeech
)

$ErrorActionPreference = "Stop"

function Invoke-JsonPost {
    param(
        [string]$Path,
        [hashtable]$Body
    )

    $json = $Body | ConvertTo-Json -Depth 12
    Invoke-RestMethod "$BaseUrl$Path" -Method Post -ContentType "application/json" -Body $json
}

$listenAfterSpeech = -not [bool]$NoListenAfterSpeech

$summary = [ordered]@{}

$summary.health = Invoke-RestMethod "$BaseUrl/health"

if (-not [bool]$NoListener) {
    $summary.listener = Invoke-JsonPost "/listener/start" @{
        enabled = $true
        auto_route = $false
        execute = $false
        dry_run = -not [bool]$Real
        sample_rate = 16000
        vad_mode = 2
        frame_ms = 20
        min_speech_ms = 300
        silence_ms = 700
        max_utterance_ms = 8000
        pre_roll_ms = 300
        min_rms = 120
        stt_model = "tiny.en"
        language = "en"
        compute_type = "int8"
        mute_after_route_seconds = 5.0
    }
}

$summary.voice = Invoke-JsonPost "/voice/start" @{
    enabled = $true
    dry_run = -not [bool]$Real
    allow_motion = [bool]$AllowMotion
    use_behavior_control = $false
    route_intents_to_gemma = $true
    listen_after_speech = $listenAfterSpeech
}

$summary.vision = Invoke-JsonPost "/vision/start" @{
    enabled = $true
    interval_seconds = $VisionIntervalSeconds
    respect_sleep = $true
}

$summary.sentinel = Invoke-JsonPost "/sentinel/start" @{
    enabled = $true
    dry_run = -not [bool]$Real
    allow_motion = [bool]$AllowMotion
    poll_interval_seconds = 2.0
    listen_after_speech = $listenAfterSpeech
}

$summary.autonomy = Invoke-JsonPost "/autonomy/start" @{
    enabled = $true
    dry_run = -not [bool]$Real
    interval_seconds = $AutonomyIntervalSeconds
    allow_motion = [bool]$AllowMotion
    listen_after_speech = $listenAfterSpeech
    respect_sleep = $true
    include_vision = $true
    vision_interval_ticks = $VisionIntervalTicks
    speak_probability = $SpeakProbability
    vibe = "curious desk companion, locally embodied and gently alive"
    robot_state = @{
        on_charger = $true
        picked_up = $false
        cliff_detected = $false
        obstacle_close = $false
        low_battery = $false
    }
}

$summary | ConvertTo-Json -Depth 30
