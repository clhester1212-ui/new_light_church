# Reports whether secrets\slack.json has real Slack keys, without printing them.
$path = Join-Path $PSScriptRoot "..\secrets\slack.json"
$cfg = Get-Content $path -Raw | ConvertFrom-Json
"file: " + (Resolve-Path $path) + "  saved: " + (Get-Item $path).LastWriteTime
"bot_token ok: " + ($cfg.bot_token -match '^xoxb-[\w-]{20,}$')
"app_token ok: " + ($cfg.app_token -match '^xapp-[\w-]{20,}$')
"allowed_user_ids: " + ($cfg.allowed_user_ids -join ", ")
if ($cfg.bot_token -match '^xoxb-[\w-]{20,}$') {
    $r = Invoke-RestMethod -Method Post -Uri https://slack.com/api/auth.test -Headers @{ Authorization = "Bearer $($cfg.bot_token)" }
    "slack accepts bot key: $($r.ok)  workspace: $($r.team)  bot: $($r.user)"
}
