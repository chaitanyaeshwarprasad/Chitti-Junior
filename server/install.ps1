# Chitti first-time installer. Created by Chaitanya Eshwar Prasad (https://chaitanyaeshwarprasad.com). MIT licence.
# Checks this PC for everything Chitti needs and installs what's missing. Safe to run again.
# Run by "Start Chitti Junior.bat"; exit code 0 = ready.
$ErrorActionPreference = "Stop"
$ProgressPreference = "SilentlyContinue"   # makes downloads much faster in Windows PowerShell
$Server = $PSScriptRoot
$Root = Split-Path $Server -Parent
$Venv = Join-Path $Server ".venv"
$VenvPy = Join-Path $Venv "Scripts\python.exe"
$Marker = Join-Path $Venv "chitti_installed.txt"
$LocalFfmpeg = Join-Path $Server "ffmpeg\bin"
$env:PATH = "$env:PATH;$env:LOCALAPPDATA\Microsoft\WinGet\Links;$LocalFfmpeg"

function Line { Write-Host ("  " + ("-" * 62)) -ForegroundColor DarkCyan }
function Step($n, $text) { Write-Host ""; Write-Host ("  [$n/8] " + $text) -ForegroundColor Cyan }
function Ok($text) { Write-Host ("        OK   " + $text) -ForegroundColor Green }
function Warn($text) { Write-Host ("        !    " + $text) -ForegroundColor Yellow }
function Info($text) { Write-Host ("             " + $text) -ForegroundColor Gray }
function Fail($text, $fix) {
    Write-Host ""
    Write-Host ("  X  " + $text) -ForegroundColor Red
    if ($fix) { Write-Host ("     " + $fix) -ForegroundColor Yellow }
    Write-Host ""
    exit 1
}
function Ask($question) {
    $a = Read-Host ("        ?    " + $question + " [Y/n]")
    return -not ($a -match '^\s*n')
}
function Have($cmd) { return [bool](Get-Command $cmd -ErrorAction SilentlyContinue) }
function PyVersionOk($exe, $pre) {
    try {
        $v = & $exe @pre -c "import sys; print('%d.%d' % sys.version_info[:2])" 2>$null
        return ($LASTEXITCODE -eq 0 -and $v -in @("3.10", "3.11", "3.12"))
    } catch { return $false }
}

Clear-Host
Write-Host ""
Line
Write-Host "    CHITTI JUNIOR  -  your Telugu AI friend" -ForegroundColor White
Write-Host "    First-time install. Takes about 5-10 minutes." -ForegroundColor Gray
Write-Host "    Made by Chaitanya Eshwar Prasad  -  youtube.com/@chaitanya.eshwar.prasad" -ForegroundColor DarkCyan
Line

# ---------------------------------------------------------------- 1. this PC
Step 1 "Checking this PC"
if (-not [Environment]::Is64BitOperatingSystem) { Fail "Chitti needs 64-bit Windows 10 or 11." "This PC runs 32-bit Windows, which isn't supported." }
$arch = $env:PROCESSOR_ARCHITECTURE
if ($env:PROCESSOR_ARCHITEW6432) { $arch = $env:PROCESSOR_ARCHITEW6432 }
if ($arch -eq "ARM64") { Warn "This is an ARM PC. Chitti is made for normal (x64) PCs and may not work fully." }
else { Ok "64-bit Windows" }
$build = [Environment]::OSVersion.Version.Build
if ($build -lt 17763) { Warn "Windows is quite old (build $build). Please run Windows Update if anything fails." }
if ($Root -match "OneDrive") { Warn "This folder is inside OneDrive. If install fails, move the Chitti folder to C:\Chitti." }
try {
    $drive = (Get-Item $Root).PSDrive
    $freeGB = [math]::Round($drive.Free / 1GB, 1)
    if ($freeGB -lt 4) { Fail "Not enough disk space: $freeGB GB free on drive $($drive.Name):." "Chitti needs about 4 GB. Free some space, then start Chitti Junior again." }
    Ok "$freeGB GB free on drive $($drive.Name):"
} catch { Warn "Couldn't check free disk space. Chitti needs about 4 GB." }
try {
    Invoke-WebRequest -Uri "https://pypi.org/simple/pip/" -Method Head -UseBasicParsing -TimeoutSec 15 | Out-Null
    Ok "Internet connection"
} catch { Fail "No internet connection (or it's blocked)." "Connect to the internet, then start Chitti Junior again." }

# ---------------------------------------------------------------- 2. Python 3.10-3.12
Step 2 "Python"
$py = $null; $pyPre = @()
foreach ($v in @("3.12", "3.11", "3.10")) {
    if (-not $py -and (Have "py") -and (PyVersionOk "py" @("-$v"))) { $py = "py"; $pyPre = @("-$v") }
}
if (-not $py -and (Have "python") -and (PyVersionOk "python" @())) { $py = "python" }
if (-not $py) {
    foreach ($d in @("$env:LOCALAPPDATA\Programs\Python\Python312", "$env:ProgramFiles\Python312",
                      "$env:LOCALAPPDATA\Programs\Python\Python311", "$env:LOCALAPPDATA\Programs\Python\Python310")) {
        if (-not $py -and (Test-Path "$d\python.exe") -and (PyVersionOk "$d\python.exe" @())) { $py = "$d\python.exe" }
    }
}
if (-not $py) {
    Warn "Python 3.12 isn't installed (Chitti needs 3.10, 3.11 or 3.12)."
    if ((Have "winget") -and (Ask "Install Python 3.12 now?")) {
        winget install -e --id Python.Python.3.12 --scope user
        $p = "$env:LOCALAPPDATA\Programs\Python\Python312\python.exe"
        if ((Test-Path $p) -and (PyVersionOk $p @())) { $py = $p }
    }
    if (-not $py) {
        Start-Process "https://www.python.org/downloads/release/python-3129/"
        Fail "Please install Python 3.12 from the page that just opened." "Tick 'Add python.exe to PATH' in the installer, then start Chitti Junior again."
    }
}
$pyShown = (& $py @pyPre -c "import sys; print(sys.version.split()[0])")
Ok "Python $pyShown"

# ---------------------------------------------------------------- 3. Microsoft Visual C++ runtime (speech recognition needs it)
Step 3 "Microsoft Visual C++ runtime"
$sys32 = Join-Path $env:WINDIR "System32"
if ((Test-Path "$sys32\msvcp140.dll") -and (Test-Path "$sys32\vcruntime140_1.dll")) { Ok "Installed" }
else {
    Warn "Missing. Speech recognition needs it."
    if ((Have "winget") -and (Ask "Install it now? (Windows may ask for permission)")) {
        winget install -e --id Microsoft.VCRedist.2015+.x64
    }
    if ((Test-Path "$sys32\msvcp140.dll") -and (Test-Path "$sys32\vcruntime140_1.dll")) { Ok "Installed" }
    else {
        Start-Process "https://aka.ms/vs/17/release/vc_redist.x64.exe"
        Fail "Please run the Microsoft installer that just downloaded (vc_redist.x64.exe)." "Then start Chitti Junior again."
    }
}

# ---------------------------------------------------------------- 4. Chitti's own Python environment
Step 4 "Chitti's private Python environment"
if ((Test-Path $VenvPy) -and -not (PyVersionOk $VenvPy @())) {
    Warn "Old environment found; rebuilding it."
    Remove-Item $Venv -Recurse -Force
}
if (-not (Test-Path $VenvPy)) {
    & $py @pyPre -m venv $Venv
    if ($LASTEXITCODE -ne 0 -or -not (Test-Path $VenvPy)) { Fail "Couldn't create Chitti's Python environment." "Reinstall Python 3.12, then start Chitti Junior again." }
}
Ok "server\.venv"

# ---------------------------------------------------------------- 5. packages
Step 5 "Installing Chitti's packages (the slow part, please wait)"
& $VenvPy -m pip install --upgrade pip --disable-pip-version-check -q 2>$null
$ok = $false
foreach ($try in 1..2) {
    & $VenvPy -m pip install -r (Join-Path $Server "requirements.txt") --disable-pip-version-check --progress-bar on
    if ($LASTEXITCODE -eq 0) { $ok = $true; break }
    if ($try -eq 1) { Warn "Download hiccup; trying once more..." }
}
if (-not $ok) { Fail "Package install failed." "Check your internet, then start Chitti Junior again. Antivirus or a work network can also block it." }
Ok "Packages installed"
if (Have "nvidia-smi") {
    Info "NVIDIA graphics card found: adding faster speech recognition (about 1.5 GB)..."
    & $VenvPy -m pip install -r (Join-Path $Server "requirements-gpu.txt") --disable-pip-version-check --progress-bar on
    if ($LASTEXITCODE -eq 0) { Ok "Graphics card speed-up installed" }
    else { Warn "Graphics card speed-up failed; Chitti will use the processor instead (fine, a bit slower)." }
}

# ---------------------------------------------------------------- 6. ffmpeg
Step 6 "ffmpeg (Chitti's voice)"
if (Have "ffmpeg") { Ok "Found" }
else {
    if ((Have "winget") -and (Ask "Install ffmpeg now?")) { winget install -e --id Gyan.FFmpeg }
    if (-not (Have "ffmpeg")) {
        Info "Downloading a private copy of ffmpeg into Chitti's folder (about 100 MB)..."
        try {
            $zip = Join-Path $env:TEMP "chitti_ffmpeg.zip"
            $tmp = Join-Path $env:TEMP "chitti_ffmpeg"
            Invoke-WebRequest -Uri "https://www.gyan.dev/ffmpeg/builds/ffmpeg-release-essentials.zip" -OutFile $zip -UseBasicParsing
            if (Test-Path $tmp) { Remove-Item $tmp -Recurse -Force }
            Expand-Archive $zip -DestinationPath $tmp -Force
            $bin = Get-ChildItem $tmp -Recurse -Filter ffmpeg.exe | Select-Object -First 1
            New-Item -ItemType Directory -Force $LocalFfmpeg | Out-Null
            Copy-Item (Join-Path $bin.DirectoryName "*.exe") $LocalFfmpeg -Force
            Remove-Item $zip, $tmp -Recurse -Force -ErrorAction SilentlyContinue
        } catch { Warn "Download failed: $($_.Exception.Message)" }
    }
    if (Have "ffmpeg") { Ok "ffmpeg ready" }
    else { Warn "ffmpeg is still missing: Chitti can't speak until it's installed. Run: winget install -e --id Gyan.FFmpeg" }
}

# ---------------------------------------------------------------- 7. self-test
Step 7 "Testing the install"
& $VenvPy -c "import anthropic, aiohttp, websockets, edge_tts, numpy, serial, esptool, faster_whisper, ctranslate2" 2>$null
if ($LASTEXITCODE -ne 0) {
    & $VenvPy -c "import anthropic, aiohttp, websockets, edge_tts, numpy, serial, esptool, faster_whisper, ctranslate2"
    Fail "Something didn't install correctly (see the message above)." "Start Chitti Junior again. If it repeats, delete the server\.venv folder and try once more."
}
Ok "All parts load correctly"

# ---------------------------------------------------------------- 8. firewall (so Chitti can reach this PC)
Step 8 "Windows Firewall"
$ruleOk = [bool]$env:CHITTI_SKIP_FIREWALL   # set only by automated tests
if (-not $ruleOk) { try { $ruleOk = [bool](Get-NetFirewallRule -DisplayName "Chitti server" -ErrorAction SilentlyContinue) } catch {} }
if ($ruleOk) { Ok "Chitti is allowed" }
else {
    Info "Chitti's board talks to this PC over your Wi-Fi, so Windows Firewall must allow it."
    if (Ask "Allow Chitti through the firewall now? (Windows will ask for permission)") {
        $cmd = "New-NetFirewallRule -DisplayName 'Chitti server' -Direction Inbound -Action Allow -Profile Private,Domain -Program '$VenvPy' | Out-Null"
        try {
            Start-Process powershell -Verb RunAs -Wait -WindowStyle Hidden -ArgumentList "-NoProfile", "-Command", $cmd
            Ok "Allowed (private networks)"
        } catch { Warn "Skipped. When Windows asks about Python later, tick 'Private networks' and click Allow." }
    } else { Info "OK. When Windows asks about Python later, tick 'Private networks' and click Allow." }
}

Set-Content -Path $Marker -Value "installed" -Encoding ascii
Write-Host ""
Line
Write-Host "    All set! Chitti is starting; the setup page opens in your browser." -ForegroundColor Green
Line
exit 0
