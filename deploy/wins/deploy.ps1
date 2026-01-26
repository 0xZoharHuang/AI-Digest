# AI-Digest Windows Deployment Script
# Usage: .\deploy.ps1 -Action <install|uninstall|status|run> [-TriggerTime "08:00"] [-PythonPath "python"]

param(
    [Parameter(Mandatory=$true)]
    [ValidateSet("install", "uninstall", "status", "run")]
    [string]$Action,

    [string]$TriggerTime = "08:00",
    [string]$PythonPath = "python"
)

$TaskName = "AI-Digest-DailyCrawler"
$ScriptPath = Join-Path $PSScriptRoot "..\..\scripts\run_daily.py"
$WorkingDir = Join-Path $PSScriptRoot "..\.."

# Resolve to absolute paths
$ScriptPath = [System.IO.Path]::GetFullPath($ScriptPath)
$WorkingDir = [System.IO.Path]::GetFullPath($WorkingDir)

Write-Host "Task Name: $TaskName"
Write-Host "Script Path: $ScriptPath"
Write-Host "Working Directory: $WorkingDir"
Write-Host ""

switch ($Action) {
    "install" {
        Write-Host "Installing scheduled task..."

        # Create the task using wrapper batch file
        $RunScript = Join-Path $PSScriptRoot "run_crawler.bat"
        $RunScript = [System.IO.Path]::GetFullPath($RunScript)

        schtasks /create /tn $TaskName `
            /tr "`"$RunScript`"" `
            /sc daily /st $TriggerTime `
            /rl HIGHEST /f

        if ($LASTEXITCODE -eq 0) {
            Write-Host ""
            Write-Host "Task created successfully!" -ForegroundColor Green
            Write-Host "Schedule: Daily at $TriggerTime"
            Write-Host ""
            Write-Host "To run immediately: .\deploy.ps1 -Action run"
            Write-Host "To check status: .\deploy.ps1 -Action status"
        } else {
            Write-Host "Failed to create task" -ForegroundColor Red
        }
    }

    "uninstall" {
        Write-Host "Removing scheduled task..."

        schtasks /delete /tn $TaskName /f

        if ($LASTEXITCODE -eq 0) {
            Write-Host "Task removed successfully!" -ForegroundColor Green
        } else {
            Write-Host "Failed to remove task (may not exist)" -ForegroundColor Yellow
        }
    }

    "status" {
        Write-Host "Checking task status..."
        Write-Host ""

        schtasks /query /tn $TaskName /v /fo list

        if ($LASTEXITCODE -ne 0) {
            Write-Host "Task not found" -ForegroundColor Yellow
        }
    }

    "run" {
        Write-Host "Running task immediately..."

        schtasks /run /tn $TaskName

        if ($LASTEXITCODE -eq 0) {
            Write-Host "Task triggered successfully!" -ForegroundColor Green
        } else {
            Write-Host "Failed to run task" -ForegroundColor Red
        }
    }
}
