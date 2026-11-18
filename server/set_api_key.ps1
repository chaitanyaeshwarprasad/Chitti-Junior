# Saves Chitti's API keys on THIS PC only (server\.env + your Windows user environment).
# Keys are pasted here, never shown on screen, and never leave this PC except to their own service.
$envFile = Join-Path $PSScriptRoot ".env"

function Read-Secret($prompt) {
    $secure = Read-Host -AsSecureString $prompt
    return [Runtime.InteropServices.Marshal]::PtrToStringAuto([Runtime.InteropServices.Marshal]::SecureStringToBSTR($secure)).Trim().Trim('"')
}

function Save-Key($name, $value) {
    # update just this line of .env, keep every other key
    $lines = @()
    if (Test-Path $envFile) { $lines = @(Get-Content $envFile | Where-Object { $_ -notmatch "^\s*$name\s*=" }) }
    $lines += "$name=$value"
    Set-Content -Path $envFile -Value $lines -Encoding utf8
    [Environment]::SetEnvironmentVariable($name, $value, "User")
}

Write-Host ""
Write-Host "1) Anthropic API key (Chitti's brain) - from console.anthropic.com" -ForegroundColor Cyan
Write-Host "   Paste with Ctrl+V or right-click, then Enter. Nothing appears while you paste. Enter alone = skip." -ForegroundColor DarkGray
$key = Read-Secret "Anthropic key"
if ($key) {
    if ($key.Length -lt 40 -or -not $key.StartsWith("sk-ant-")) { Write-Host "That isn't an Anthropic key (starts with sk-ant-). Skipped." -ForegroundColor Red }
    else { Save-Key "ANTHROPIC_API_KEY" $key; Write-Host ("Saved (" + $key.Substring(0, 10) + "...).") -ForegroundColor Green }
}

Write-Host ""
Write-Host "2) Sarvam AI key (natural Telugu voice + hearing) - from dashboard.sarvam.ai. Optional." -ForegroundColor Cyan
Write-Host "   Without it Chitti uses a free voice and hears with Whisper on this PC." -ForegroundColor DarkGray
$skey = Read-Secret "Sarvam key"
if ($skey) {
    if ($skey.Length -lt 8 -or $skey -notmatch '^[A-Za-z0-9_-]+$') { Write-Host "That doesn't look like a Sarvam key. Skipped." -ForegroundColor Red }
    else { Save-Key "SARVAM_API_KEY" $skey; Write-Host "Saved." -ForegroundColor Green }
}
Write-Host ""
Write-Host "Done. Start Chitti Junior with 'Start Chitti Junior.bat'."
