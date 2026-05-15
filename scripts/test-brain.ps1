param(
    [string]$BaseUrl = "http://127.0.0.1:8787",
    [string]$Prompt = "say hello and do one tiny happy expression"
)

$ErrorActionPreference = "Stop"

Write-Host "Health:"
Invoke-RestMethod "$BaseUrl/health" | ConvertTo-Json -Depth 10

$planBody = @{
    user_text = $Prompt
    robot_state = @{
        on_charger = $true
        picked_up = $false
        cliff_detected = $false
        obstacle_close = $false
        low_battery = $false
    }
} | ConvertTo-Json -Depth 10

Write-Host "`nPlan:"
$plan = Invoke-RestMethod "$BaseUrl/plan" -Method Post -ContentType "application/json" -Body $planBody
$plan | ConvertTo-Json -Depth 10

$execBody = @{
    actions = $plan.actions
    robot_state = @{
        on_charger = $true
    }
    dry_run = $true
} | ConvertTo-Json -Depth 10

Write-Host "`nExecute dry-run:"
Invoke-RestMethod "$BaseUrl/execute" -Method Post -ContentType "application/json" -Body $execBody | ConvertTo-Json -Depth 10
