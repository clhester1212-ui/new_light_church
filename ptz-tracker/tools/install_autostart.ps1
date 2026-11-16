# Sets up the PTZ Tracker to start by itself at Windows login, and puts a "PTZ Tracker" button on the desktop.
# Run once on the Stream Deck PC:  powershell -ExecutionPolicy Bypass -File ptz-tracker\tools\install_autostart.ps1
# To undo: Unregister-ScheduledTask -TaskName "New Light PTZ Tracker" -Confirm:$false, then delete the desktop icon.

$ErrorActionPreference = "Stop"
$tools = $PSScriptRoot
$app = Split-Path $tools -Parent
$pythonw = (Get-Command pythonw.exe -ErrorAction SilentlyContinue).Source
if (-not $pythonw -or $pythonw -like "*WindowsApps*") {
    $pythonw = Join-Path $env:LOCALAPPDATA "Programs\Python\Python312\pythonw.exe"
}
if (-not (Test-Path $pythonw)) { throw "pythonw.exe not found. Install Python 3.12 first." }

# 1. Start at login, hidden, kept running by keep_running.py
$action = New-ScheduledTaskAction -Execute $pythonw -Argument "`"$tools\keep_running.py`"" -WorkingDirectory $app
$trigger = New-ScheduledTaskTrigger -AtLogOn -User "$env:USERDOMAIN\$env:USERNAME"
$settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
    -ExecutionTimeLimit ([TimeSpan]::Zero) -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 1) -StartWhenAvailable
$principal = New-ScheduledTaskPrincipal -UserId "$env:USERDOMAIN\$env:USERNAME" -LogonType Interactive -RunLevel Limited
Register-ScheduledTask -TaskName "New Light PTZ Tracker" -Action $action -Trigger $trigger -Settings $settings `
    -Principal $principal -Description "Starts the New Light PTZ Tracker (http://localhost:8890) in the background at login." -Force | Out-Null
Write-Output "Login task 'New Light PTZ Tracker' is set up."

# 2. Desktop button
$desktop = [Environment]::GetFolderPath("Desktop")
$lnk = Join-Path $desktop "PTZ Tracker.lnk"
$sh = New-Object -ComObject WScript.Shell
$s = $sh.CreateShortcut($lnk)
$s.TargetPath = $pythonw
$s.Arguments = "`"$tools\open_dashboard.py`""
$s.WorkingDirectory = $app
$s.IconLocation = "$tools\ptz-tracker.ico,0"
$s.Description = "Open the New Light PTZ Tracker dashboard (starts it if needed)"
$s.Save()
Write-Output "Desktop button: $lnk"
