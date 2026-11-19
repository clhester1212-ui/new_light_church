# Sets up the "New Light Bridge Watch" task: starts the helper (no window) every time Windows logs in.
# Run once:  powershell -ExecutionPolicy Bypass -File install.ps1
$here = Split-Path -Parent $MyInvocation.MyCommand.Path
$action = New-ScheduledTaskAction -Execute "wscript.exe" -Argument "`"$here\start-hidden.vbs`"" -WorkingDirectory $here
$trigger = New-ScheduledTaskTrigger -AtLogOn -User "$env:USERDOMAIN\$env:USERNAME"
$settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -ExecutionTimeLimit ([TimeSpan]::Zero)
Register-ScheduledTask -TaskName "New Light Bridge Watch" -Action $action -Trigger $trigger -Settings $settings `
  -Description "Keeps the ATEM Mini Extreme streaming to the ATEM Streaming Bridge, even when the Bridge comes back at a new address. See new_light_church\bridge-watch\README.md" -Force | Out-Null
Start-ScheduledTask -TaskName "New Light Bridge Watch"
"Installed and started."
