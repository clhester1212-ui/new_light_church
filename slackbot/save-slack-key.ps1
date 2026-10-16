# Saves a Slack key from the clipboard into secrets\slack.json.
# Copy the key in Slack (xoxb-... or xapp-...), then double-click save-slack-key.cmd.
$path = Join-Path $PSScriptRoot "..\secrets\slack.json"
$key = (Get-Clipboard -Raw).Trim()
$cfg = Get-Content $path -Raw | ConvertFrom-Json
if ($key -match '^xoxb-[\w-]{20,}$') { $cfg.bot_token = $key; $what = "Bot key (xoxb-)" }
elseif ($key -match '^xapp-[\w-]{20,}$') { $cfg.app_token = $key; $what = "Connection key (xapp-)" }
else {
    Write-Host "The clipboard doesn't hold a Slack key (should start with xoxb- or xapp-)." -ForegroundColor Red
    Write-Host "Click Copy next to the key in Slack, then run this again."
    Read-Host "Press Enter to close"; exit 1
}
[IO.File]::WriteAllText((Resolve-Path $path), ($cfg | ConvertTo-Json), (New-Object Text.UTF8Encoding $false))
Set-Clipboard -Value " "
Write-Host "$what saved." -ForegroundColor Green
Read-Host "Press Enter to close"
