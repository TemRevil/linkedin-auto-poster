# LinkedIn Auto-Poster - Schedule Daily Tasks
# 3:00 PM - News Discovery (swipe cards)
# 5:00 PM - Post Generation Pipeline

Write-Host "`n============================================================" -ForegroundColor Cyan
Write-Host "  Scheduling LinkedIn Auto-Poster (Daily Tasks)" -ForegroundColor Cyan
Write-Host "============================================================`n" -ForegroundColor Cyan

$pythonPath = (Get-Command python -ErrorAction SilentlyContinue).Source
if (-not $pythonPath) {
    Write-Host "  ERROR: Python not found in PATH." -ForegroundColor Red
    exit 1
}

$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$schedulerPath = Join-Path $scriptDir "src\scheduler.py"

# Task 1: Discovery at 3 PM
$action1 = New-ScheduledTaskAction -Execute $pythonPath -Argument "`"$schedulerPath`" --discover"
$trigger1 = New-ScheduledTaskTrigger -Daily -At "3:00 PM"
$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -DontStopOnIdleEnd

try {
    Register-ScheduledTask -TaskName "LinkedInAutoPoster_Discovery" -Action $action1 -Trigger $trigger1 -Settings $settings -Force
    Write-Host "  [1/2] Discovery task created!" -ForegroundColor Green
    Write-Host "        Schedule: Daily at 3:00 PM"
    Write-Host "        Purpose:  Scrape news for swipe cards"
} catch {
    Write-Host "  ERROR (Discovery): $($_.Exception.Message)" -ForegroundColor Red
}

# Task 2: Post Pipeline at 5 PM
$action2 = New-ScheduledTaskAction -Execute $pythonPath -Argument "`"$schedulerPath`""
$trigger2 = New-ScheduledTaskTrigger -Daily -At "5:00 PM"

try {
    Register-ScheduledTask -TaskName "LinkedInAutoPoster_Pipeline" -Action $action2 -Trigger $trigger2 -Settings $settings -Force
    Write-Host "  [2/2] Pipeline task created!" -ForegroundColor Green
    Write-Host "        Schedule: Daily at 5:00 PM"
    Write-Host "        Purpose:  Generate post + open approval"
} catch {
    Write-Host "  ERROR (Pipeline): $($_.Exception.Message)" -ForegroundColor Red
}

# Task 3: Claude Code maintenance at 4 PM
$maintenanceScript = Join-Path $scriptDir "run_maintenance.ps1"
$action3 = New-ScheduledTaskAction `
    -Execute "powershell.exe" `
    -Argument "-NonInteractive -ExecutionPolicy Bypass -File `"$maintenanceScript`""
$trigger3 = New-ScheduledTaskTrigger -Daily -At "4:00 PM"

try {
    Register-ScheduledTask -TaskName "LinkedInAutoPoster_Maintenance" -Action $action3 -Trigger $trigger3 -Settings $settings -Force
    Write-Host "  [3/3] Maintenance task created!" -ForegroundColor Green
    Write-Host "        Schedule: Daily at 4:00 PM"
    Write-Host "        Purpose:  Claude Code fixes next audit bug, commits as TemRevil, pushes to main"
} catch {
    Write-Host "  ERROR (Maintenance): $($_.Exception.Message)" -ForegroundColor Red
}

Write-Host "`n  Python:   $pythonPath" -ForegroundColor DarkGray
Write-Host "  Script:   $schedulerPath" -ForegroundColor DarkGray
Write-Host ""
Write-Host "  To test:  python src\run.py discover" -ForegroundColor Yellow
Write-Host "            python src\run.py test" -ForegroundColor Yellow
Write-Host "  To test maintenance manually:" -ForegroundColor Yellow
Write-Host "            powershell -ExecutionPolicy Bypass -File run_maintenance.ps1" -ForegroundColor Yellow
Write-Host ""
Write-Host "  To remove:" -ForegroundColor DarkGray
Write-Host "    Unregister-ScheduledTask -TaskName 'LinkedInAutoPoster_Discovery' -Confirm:`$false"
Write-Host "    Unregister-ScheduledTask -TaskName 'LinkedInAutoPoster_Pipeline' -Confirm:`$false"
Write-Host "    Unregister-ScheduledTask -TaskName 'LinkedInAutoPoster_Maintenance' -Confirm:`$false"
