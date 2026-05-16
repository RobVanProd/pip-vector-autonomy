param(
    [string]$BaseUrl = "http://127.0.0.1:8788",
    [int]$DurationMinutes = 180,
    [int]$StatusEverySeconds = 60,
    [int]$LatencyEverySeconds = 120,
    [int]$WarmupEverySeconds = 600,
    [int]$MapEverySeconds = 900,
    [int]$ControlSuiteEverySeconds = 1200,
    [switch]$SkipMap,
    [switch]$SkipControlSuite,
    [switch]$RealControlSuite,
    [string]$OutputDir = ""
)

$ErrorActionPreference = "Stop"

if (-not $OutputDir) {
    $stamp = Get-Date -Format "yyyyMMdd-HHmmss"
    $OutputDir = Join-Path (Resolve-Path ".").Path "vector\logs\extended-harness-$stamp"
}
New-Item -ItemType Directory -Force -Path $OutputDir | Out-Null
$jsonl = Join-Path $OutputDir "events.jsonl"
$summaryPath = Join-Path $OutputDir "summary.json"

function Write-Jsonl {
    param(
        [string]$Kind,
        [object]$Payload,
        [double]$ElapsedMs = $null
    )
    $entry = [ordered]@{
        ts = (Get-Date).ToUniversalTime().ToString("o")
        kind = $Kind
        elapsed_ms = $ElapsedMs
        payload = $Payload
    }
    ($entry | ConvertTo-Json -Depth 40 -Compress) | Add-Content -Path $jsonl -Encoding UTF8
}

function Invoke-Tracked {
    param(
        [string]$Kind,
        [string]$Method = "GET",
        [string]$Path,
        [object]$Body = $null
    )
    $sw = [Diagnostics.Stopwatch]::StartNew()
    try {
        if ($Body -ne $null) {
            $result = Invoke-RestMethod "$BaseUrl$Path" -Method $Method -ContentType "application/json" -Body ($Body | ConvertTo-Json -Depth 40) -TimeoutSec 45
        } else {
            $result = Invoke-RestMethod "$BaseUrl$Path" -Method $Method -TimeoutSec 45
        }
        $sw.Stop()
        Write-Jsonl $Kind $result $sw.Elapsed.TotalMilliseconds
        return $result
    } catch {
        $sw.Stop()
        Write-Jsonl "$Kind.error" @{ error = $_.Exception.Message; path = $Path; method = $Method } $sw.Elapsed.TotalMilliseconds
        return $null
    }
}

$deadline = (Get-Date).AddMinutes($DurationMinutes)
$nextStatus = Get-Date
$nextLatency = Get-Date
$nextWarmup = Get-Date
$nextMap = Get-Date
$nextControl = Get-Date
$lastEventId = 0

Write-Jsonl "harness.start" @{
    base_url = $BaseUrl
    duration_minutes = $DurationMinutes
    output_dir = $OutputDir
    real_control_suite = [bool]$RealControlSuite
}

Invoke-Tracked "health" "GET" "/health" | Out-Null
Invoke-Tracked "llm_warmup" "POST" "/llm/warmup" | Out-Null

while ((Get-Date) -lt $deadline) {
    $now = Get-Date

    if ($now -ge $nextStatus) {
        Invoke-Tracked "status.health" "GET" "/health" | Out-Null
        Invoke-Tracked "status.robot" "GET" "/robot/state" | Out-Null
        Invoke-Tracked "status.audio" "GET" "/audio/status" | Out-Null
        Invoke-Tracked "status.listener" "GET" "/listener/status" | Out-Null
        Invoke-Tracked "status.conversation" "GET" "/conversation/status" | Out-Null
        Invoke-Tracked "status.voice" "GET" "/voice/status" | Out-Null
        Invoke-Tracked "status.autonomy" "GET" "/autonomy/status" | Out-Null
        Invoke-Tracked "status.sentinel" "GET" "/sentinel/status" | Out-Null
        Invoke-Tracked "status.vision" "GET" "/vision/status" | Out-Null
        $events = Invoke-Tracked "events.raw" "GET" "/events?limit=200"
        if ($events -and $events.events) {
            $newEvents = @($events.events | Where-Object { $_.id -gt $lastEventId })
            if ($newEvents.Count -gt 0) {
                $lastEventId = ($newEvents | Measure-Object -Property id -Maximum).Maximum
                Write-Jsonl "events.delta" @{ count = $newEvents.Count; events = $newEvents }
            }
        }
        $nextStatus = $now.AddSeconds($StatusEverySeconds)
    }

    if ($now -ge $nextLatency) {
        Invoke-Tracked "latency.sample" "POST" "/diagnostics/latency-sample?prompt=say%20one%20short%20friendly%20sentence%20and%20stop" | Out-Null
        Invoke-Tracked "chat.sample" "POST" "/chat" @{
            user_text = "Quick latency check: answer in one short friendly sentence."
            execute = $false
            dry_run = $true
            robot_state = @{}
        } | Out-Null
        $nextLatency = $now.AddSeconds($LatencyEverySeconds)
    }

    if ($now -ge $nextWarmup) {
        Invoke-Tracked "llm_warmup" "POST" "/llm/warmup" | Out-Null
        $nextWarmup = $now.AddSeconds($WarmupEverySeconds)
    }

    if (-not $SkipMap -and $now -ge $nextMap) {
        Invoke-Tracked "map.observe" "POST" "/map/observe?note=extended-harness" | Out-Null
        Invoke-Tracked "llm_warmup.after_map" "POST" "/llm/warmup" | Out-Null
        $nextMap = $now.AddSeconds($MapEverySeconds)
    }

    if (-not $SkipControlSuite -and $now -ge $nextControl) {
        $dryRun = (-not [bool]$RealControlSuite).ToString().ToLowerInvariant()
        Invoke-Tracked "control_suite" "POST" "/validation/control-suite?dry_run=$dryRun" | Out-Null
        Invoke-Tracked "llm_warmup.after_control_suite" "POST" "/llm/warmup" | Out-Null
        $nextControl = $now.AddSeconds($ControlSuiteEverySeconds)
    }

    Start-Sleep -Seconds 5
}

Write-Jsonl "harness.end" @{ output_dir = $OutputDir }

$lines = Get-Content $jsonl | ForEach-Object { $_ | ConvertFrom-Json }
$latencies = @($lines | Where-Object { $_.kind -eq "latency.sample" -and $_.payload.metrics })
$summary = [ordered]@{
    output_dir = $OutputDir
    jsonl = $jsonl
    samples = $lines.Count
    latency_samples = $latencies.Count
    latency_elapsed_ms = if ($latencies.Count) {
        @{
            min = [math]::Round((($latencies | Measure-Object -Property elapsed_ms -Minimum).Minimum), 1)
            max = [math]::Round((($latencies | Measure-Object -Property elapsed_ms -Maximum).Maximum), 1)
            avg = [math]::Round((($latencies | Measure-Object -Property elapsed_ms -Average).Average), 1)
        }
    } else { $null }
    errors = @($lines | Where-Object { $_.kind -like "*.error" }).Count
}
$summary | ConvertTo-Json -Depth 20 | Set-Content -Path $summaryPath -Encoding UTF8
$summary | ConvertTo-Json -Depth 20
