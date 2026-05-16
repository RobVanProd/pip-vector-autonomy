param(
    [Parameter(Mandatory=$true)]
    [string]$LogDir
)

$ErrorActionPreference = "Stop"
$jsonl = Join-Path $LogDir "events.jsonl"
if (-not (Test-Path $jsonl)) {
    throw "Missing events.jsonl in $LogDir"
}

$rows = @(Get-Content $jsonl | Where-Object { $_.Trim() } | ForEach-Object { $_ | ConvertFrom-Json })

function Stats($Values) {
    $items = @($Values | Where-Object { $_ -ne $null } | ForEach-Object { [double]$_ } | Sort-Object)
    if (-not $items.Count) { return $null }
    $p95Index = [Math]::Min($items.Count - 1, [Math]::Floor(($items.Count - 1) * 0.95))
    @{
        count = $items.Count
        min = [Math]::Round($items[0], 1)
        avg = [Math]::Round((($items | Measure-Object -Average).Average), 1)
        p95 = [Math]::Round($items[$p95Index], 1)
        max = [Math]::Round($items[-1], 1)
    }
}

$latency = @($rows | Where-Object { $_.kind -eq "latency.sample" })
$chat = @($rows | Where-Object { $_.kind -eq "chat.sample" })
$warmups = @($rows | Where-Object { $_.kind -like "llm_warmup*" })
$rewarmEvents = @()
$errors = @($rows | Where-Object { $_.kind -like "*.error" })
$mapRows = @($rows | Where-Object { $_.kind -eq "map.observe" })
$control = @($rows | Where-Object { $_.kind -eq "control_suite" })
$eventDeltas = @($rows | Where-Object { $_.kind -eq "events.delta" })

$conversationWindows = 0
$conversationEnds = 0
$audioStaticTrue = 0
foreach ($delta in $eventDeltas) {
    foreach ($event in @($delta.payload.events)) {
        if ($event.kind -eq "conversation_reply_window_open") { $conversationWindows++ }
        if ($event.kind -eq "conversation_end") { $conversationEnds++ }
        if ($event.kind -in @("llm_warmup", "llm_rewarm", "llm_rewarm_skipped")) { $rewarmEvents += $event }
    }
}
foreach ($row in @($rows | Where-Object { $_.kind -eq "status.audio" })) {
    if ($row.payload.static_signal_detected) { $audioStaticTrue++ }
}

$mapOk = @($mapRows | Where-Object { $_.payload.summary -and $_.payload.summary.visible_counts.pip -gt 0 }).Count
$controlOk = @($control | Where-Object { $_.payload.ok -eq $true }).Count

$summary = [ordered]@{
    log_dir = $LogDir
    samples = $rows.Count
    started = if ($rows.Count) { $rows[0].ts } else { $null }
    ended = if ($rows.Count) { $rows[-1].ts } else { $null }
    latency_sample_elapsed_ms = Stats ($latency | ForEach-Object { $_.elapsed_ms })
    latency_ollama_total_ms = Stats ($latency | ForEach-Object { $_.payload.metrics.total_duration_ms })
    latency_ollama_load_ms = Stats ($latency | ForEach-Object { $_.payload.metrics.load_duration_ms })
    latency_cold_loads_over_5s = @($latency | Where-Object { $_.payload.metrics.load_duration_ms -ge 5000 }).Count
    latency_cold_loads_over_15s = @($latency | Where-Object { $_.payload.metrics.load_duration_ms -ge 15000 }).Count
    chat_sample_elapsed_ms = Stats ($chat | ForEach-Object { $_.elapsed_ms })
    chat_cold_loads_over_5s = @($chat | Where-Object { $_.payload.plan.metrics.load_duration_ms -ge 5000 -or $_.payload.metrics.load_duration_ms -ge 5000 }).Count
    warmup_elapsed_ms = Stats ($warmups | ForEach-Object { $_.elapsed_ms })
    rewarm_events_seen = $rewarmEvents.Count
    rewarm_skipped_seen = @($rewarmEvents | Where-Object { $_.kind -eq "llm_rewarm_skipped" }).Count
    errors = $errors.Count
    map_observations = $mapRows.Count
    map_rows_with_pip_seen = $mapOk
    control_suite_runs = $control.Count
    control_suite_ok = $controlOk
    conversation_reply_windows = $conversationWindows
    conversation_ends = $conversationEnds
    audio_static_status_count = $audioStaticTrue
}

$out = Join-Path $LogDir "analysis.json"
$summary | ConvertTo-Json -Depth 20 | Set-Content -Path $out -Encoding UTF8
$summary | ConvertTo-Json -Depth 20
