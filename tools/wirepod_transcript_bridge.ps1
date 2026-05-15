param(
    [string]$BotSerial = "",
    [string]$SpeechText = "",
    [string]$Locale = ""
)

$cleanText = ($SpeechText.Trim()).Trim('"')

if ($cleanText.Length -gt 0) {
    $body = @{
        serial = $BotSerial
        text = $cleanText
        locale = $Locale
        source = "wirepod-custom-intent"
        execute = $true
        dry_run = $false
    } | ConvertTo-Json -Depth 4

    try {
        Invoke-RestMethod `
            -Uri "http://127.0.0.1:8788/wirepod/transcript" `
            -Method Post `
            -ContentType "application/json" `
            -Body $body `
            -TimeoutSec 3 | Out-Null
    } catch {
        Write-Error "Vector brain transcript bridge failed: $($_.Exception.Message)"
    }
}

Write-Output '{"status":"ok","returnIntent":"intent_imperative_affirmative"}'
