param(
    [string]$BaseUrl = "http://127.0.0.1:8788",
    [switch]$SkipExternalCamera,
    [switch]$SkipConversation
)

$ErrorActionPreference = "Stop"

function Show-Json($Title, $Value) {
    Write-Host "`n$Title"
    $Value | ConvertTo-Json -Depth 30
}

function Invoke-JsonPost($Url, $Body) {
    Invoke-RestMethod $Url -Method Post -ContentType "application/json" -Body ($Body | ConvertTo-Json -Depth 30)
}

Show-Json "Health" (Invoke-RestMethod "$BaseUrl/health")
Show-Json "Robot state" (Invoke-RestMethod "$BaseUrl/robot/state")
Show-Json "Conversation status" (Invoke-RestMethod "$BaseUrl/conversation/status")

if (-not $SkipExternalCamera) {
    Show-Json "External camera status" (Invoke-RestMethod "$BaseUrl/external-camera/status")
    Show-Json "Pip area validation" (Invoke-RestMethod "$BaseUrl/validation/pip-area" -Method Post)
}

$plan = Invoke-JsonPost "$BaseUrl/plan" @{
    user_text = "say a short hello and stay still"
    robot_state = @{
        connected = $true
        on_charger = $false
        picked_up = $false
        cliff_detected = $false
        obstacle_close = $false
        low_battery = $false
    }
}
Show-Json "Plan smoke test" $plan

Show-Json "Execute dry-run smoke test" (Invoke-JsonPost "$BaseUrl/execute" @{
    actions = @(@{ type = "stop" })
    robot_state = @{ connected = $true; on_charger = $false }
    dry_run = $true
})

if (-not $SkipConversation) {
    Show-Json "Conversation dry-run queue" (Invoke-JsonPost "$BaseUrl/wirepod/transcript" @{
        text = "Hey Pip, what is my name?"
        source = "system-validation"
        execute = $false
        dry_run = $true
    })
    Start-Sleep -Seconds 3
    Show-Json "Recent events" (Invoke-RestMethod "$BaseUrl/events?limit=20")
}
