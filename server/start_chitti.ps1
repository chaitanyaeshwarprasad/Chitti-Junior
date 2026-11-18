# Stop any orphaned Chitti server process running on ports 8765, 8766, or 8768
try {
    $stalePids = Get-NetTCPConnection -LocalPort 8765, 8768 -ErrorAction SilentlyContinue | Select-Object -ExpandProperty OwningProcess -Unique
    foreach ($p in $stalePids) {
        if ($p -and $p -ne $PID) {
            Stop-Process -Id $p -Force -ErrorAction SilentlyContinue
        }
    }
} catch {}

# Starts Chitti's server with the API key saved by set_api_key.ps1 or dashboard (works even in an older terminal window).
$env:ANTHROPIC_API_KEY = [Environment]::GetEnvironmentVariable("ANTHROPIC_API_KEY", "User")
$env:SARVAM_API_KEY = [Environment]::GetEnvironmentVariable("SARVAM_API_KEY", "User")
if (Test-Path "$PSScriptRoot\.env") {
    Get-Content "$PSScriptRoot\.env" | ForEach-Object {
        if ($_ -match "^\s*([A-Za-z0-9_]+)\s*=\s*(.*)\s*$") {
            [Environment]::SetEnvironmentVariable($matches[1], $matches[2], "Process")
        }
    }
}
$env:PYTHONIOENCODING = "utf-8"
# ffmpeg: winget installs land here; the installer may also keep a private copy in serverfmpegin
$env:PATH = "$env:PATH;$env:LOCALAPPDATA\Microsoft\WinGet\Links;$PSScriptRoot\ffmpeg\bin"
$env:HF_HUB_DISABLE_SYMLINKS_WARNING = "1"
Set-Location $PSScriptRoot

# The server opens the dashboard in your browser itself, as soon as it is ready.

& "$PSScriptRoot\.venv\Scripts\python.exe" chitti_server.py
