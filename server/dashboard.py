# Part of Chitti by Chaitanya Eshwar Prasad (https://chaitanyaeshwarprasad.com). MIT licence.
"""Chitti Web Dashboard: Real-time control console, Wi-Fi configuration,
API key management, Voice & Slang customization, Conversation feed,
API credits & usage tracking, and audio diagnostics.
Runs as a lightweight HTTP server on port 8768 alongside the WebSocket server.
"""
import json
import logging
import os
import subprocess
import sys
import threading
import time
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

log = logging.getLogger("chitti.dashboard")
HERE = Path(__file__).resolve().parent
ROOT = HERE.parent

if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

try:
    from slang_presets import PERSONALITIES, SLANG_PRESETS
except ImportError:
    SLANG_PRESETS, PERSONALITIES = {}, {}

SETTINGS_FILE = HERE / "chitti_settings.json"


def load_settings() -> dict:
    defaults = {
        "voice": "sarvam:kavitha_te_narration",
        "slang": "telgish",
        "personality": "roaster",
        "sarvam_starting_credits": 1000.0,
        "volume_boost": "loud",
        "model": "claude-haiku-5-5",
        "city": "Hyderabad",
        "use_words": "రా, రేయ్, ఒరేయ్, అరేయ్",
        "avoid_words": "",
    }
    try:
        if SETTINGS_FILE.exists():
            data = json.loads(SETTINGS_FILE.read_text(encoding="utf-8"))
            defaults.update(data)
    except Exception:
        pass
    return defaults


def save_settings(data: dict) -> bool:
    try:
        cur = load_settings()
        cur.update(data)
        SETTINGS_FILE.write_text(json.dumps(cur, indent=2, ensure_ascii=False), encoding="utf-8")
        return True
    except Exception as e:
        log.warning("Failed to save settings: %s", e)
        return False


_init_s = load_settings()

# Thread-safe shared state for live dashboard telemetry
STATE = {
    "board_connected": False,
    "board_ip": "Not connected",
    "board_battery": -1,
    "board_charging": False,
    "board_volume": -1,
    "board_underruns": 0,
    "board_temp": None,
    "board_activity": "idle",
    "board_mood": "neutral",
    "api_key_set": False,
    "api_key_masked": "",
    "sarvam_key_set": False,
    "sarvam_key_masked": "",
    "tts_engine": "sarvam",
    "stt_engine": "sarvam",
    "selected_voice": _init_s.get("voice", "sarvam:kavitha_te_narration"),
    "selected_slang": _init_s.get("slang", "telgish"),
    "selected_personality": _init_s.get("personality", "roaster"),
    "claude_model": _init_s.get("model", "claude-haiku-5-5"),
    "selected_model": _init_s.get("model", "claude-haiku-5-5"),
    "city": _init_s.get("city", "Hyderabad"),
    "use_words": _init_s.get("use_words", "రా, రేయ్, ఒరేయ్, అరేయ్"),
    "avoid_words": _init_s.get("avoid_words", ""),
    "facts": [],
    "reminders": [],
    "avg_stt": 0.0,
    "avg_brain": 0.0,
    "avg_voice": 0.0,
    "brain_mode": "claude",
    "total_turns": 0,
    "total_tokens": 0,
    "claude_cost_usd": 0.0,
    "claude_cost_inr": 0.0,
    "sarvam_tts_chars": 0,
    "sarvam_tts_cost_inr": 0.0,
    "sarvam_stt_sec": 0.0,
    "sarvam_stt_cost_inr": 0.0,
    "sarvam_total_cost_inr": 0.0,
    "sarvam_starting_credits": float(_init_s.get("sarvam_starting_credits", 1000.0)),
    "sarvam_remaining_credits": float(_init_s.get("sarvam_starting_credits", 1000.0)),
    "total_cost_usd": 0.0,
    "total_cost_inr": 0.0,
    "last_turn": None,
    "setup_done": bool(_init_s.get("setup_done", False)),
    "ffmpeg_ok": __import__("shutil").which("ffmpeg") is not None,
    "feed": [],
    "wallet": {},
}
STATE_LOCK = threading.Lock()

# Callbacks into chitti_server.py
CALLBACKS = {
    "update_key": None,
    "update_sarvam_key": None,
    "remove_key": None,
    "update_voice": None,
    "update_slang": None,
    "test_voice": None,
    "test_chat": None,
    "clear_memory": None,
    "update_model": None,
    "lists": None,
    "reset_chitti": None,
    "set_volume": None,
}

MODELS = {"claude-haiku-5-5", "claude-sonnet-5-5"}


def mask_key(key: str) -> str:
    if not key or len(key) < 14:
        return ""
    return f"{key[:8]}...{key[-4:]}"


def update_state(**kwargs):
    with STATE_LOCK:
        for k, v in kwargs.items():
            if k in STATE:
                STATE[k] = v


def record_claude_usage(tokens_in: int, tokens_out: int, tokens_cached: int, cost_usd: float, cost_inr: float):
    with STATE_LOCK:
        STATE["total_tokens"] += (tokens_in + tokens_out)
        STATE["claude_cost_usd"] += cost_usd
        STATE["claude_cost_inr"] += cost_inr
        STATE["total_cost_usd"] += cost_usd
        STATE["total_cost_inr"] += cost_inr
    wallet_spend("claude", cost_usd, reply=True)


def record_sarvam_tts(chars: int, cost_inr: float):
    with STATE_LOCK:
        STATE["sarvam_tts_chars"] += chars
        STATE["sarvam_tts_cost_inr"] += cost_inr
        STATE["sarvam_total_cost_inr"] += cost_inr
        STATE["total_cost_inr"] += cost_inr
        STATE["sarvam_remaining_credits"] = max(0.0, STATE["sarvam_starting_credits"] - STATE["sarvam_total_cost_inr"])
    wallet_spend("sarvam", cost_inr)


def record_sarvam_stt(sec: float, cost_inr: float):
    with STATE_LOCK:
        STATE["sarvam_stt_sec"] += round(sec, 2)
        STATE["sarvam_stt_cost_inr"] += cost_inr
        STATE["sarvam_total_cost_inr"] += cost_inr
        STATE["total_cost_inr"] += cost_inr
        STATE["sarvam_remaining_credits"] = max(0.0, STATE["sarvam_starting_credits"] - STATE["sarvam_total_cost_inr"])
    wallet_spend("sarvam", cost_inr, reply=True)  # one heard turn = one reply's worth of Sarvam


def record_timing(stt: float, brain: float, voice: float):
    """Running averages of each step (speech->text, Claude's first words, voice), for the speed card."""
    with STATE_LOCK:
        for key, val in (("avg_stt", stt), ("avg_brain", brain), ("avg_voice", voice)):
            if val > 0:
                STATE[key] = val if STATE[key] == 0 else round(STATE[key] * 0.8 + val * 0.2, 2)


# ---------------------------------------------------------------- budget wallet
# Neither Anthropic nor Sarvam has an API that returns your remaining balance, so Chitti keeps a wallet:
# you type the balance shown on each billing page once, and every request's exact cost is subtracted
# (Claude: from the token counts the API returns; Sarvam: from its published prices). Saved to disk.
WALLET_FILE = HERE / "wallet.json"
LOW = {"claude": 1.00, "sarvam": 100.0}  # warn below $1 / Rs 100
_wallet_lock = threading.Lock()


def _wallet_load() -> dict:
    try:
        w = json.loads(WALLET_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        w = {}
    for k in ("claude", "sarvam"):
        w.setdefault(k, {"balance": None, "set_at": None, "spent": 0.0, "replies": 0, "empty": False})
    w.setdefault("days", {})
    return w


def _wallet_save(w: dict) -> None:
    days = dict(sorted(w["days"].items())[-30:])
    w["days"] = days
    WALLET_FILE.write_text(json.dumps(w, indent=1), encoding="utf-8")


def _wallet_publish(w: dict) -> None:
    today = w["days"].get(time.strftime("%Y-%m-%d"), {})
    out = {}
    for k in ("claude", "sarvam"):
        b = w[k]
        left = None if b["balance"] is None else max(0.0, b["balance"] - b["spent"])
        per = b["spent"] / b["replies"] if b["replies"] else 0.0
        out[k] = {
            "balance": b["balance"], "set_at": b["set_at"], "spent": round(b["spent"], 6), "left": left,
            "per_reply": per, "replies_left": int(left / per) if left is not None and per > 0 else None,
            "low": left is not None and left < LOW[k], "empty": b["empty"] or (left is not None and left <= 0),
            "today": round(today.get(k, 0.0), 6),
        }
    with STATE_LOCK:
        STATE["wallet"] = out


def wallet_spend(service: str, amount: float, reply: bool = False) -> None:
    """Subtract one request's cost (claude: USD, sarvam: INR)."""
    with _wallet_lock:
        w = _wallet_load()
        w[service]["spent"] += amount
        if reply:
            w[service]["replies"] += 1
        day = w["days"].setdefault(time.strftime("%Y-%m-%d"), {})
        day[service] = day.get(service, 0.0) + amount
        _wallet_save(w)
        _wallet_publish(w)


def wallet_set_balance(service: str, balance: float) -> None:
    """New balance from the billing page: start counting from here."""
    with _wallet_lock:
        w = _wallet_load()
        w[service].update(balance=balance, set_at=time.strftime("%d %b %Y, %I:%M %p"), spent=0.0, replies=0, empty=False)
        _wallet_save(w)
        _wallet_publish(w)


def wallet_mark_empty(service: str) -> None:
    """The service itself said the credits are used up (Anthropic credit error / Sarvam HTTP 402)."""
    with _wallet_lock:
        w = _wallet_load()
        if not w[service]["empty"]:
            w[service]["empty"] = True
            _wallet_save(w)
        _wallet_publish(w)


def add_feed_entry(entry: dict):
    with STATE_LOCK:
        STATE["feed"].append(entry)
        if len(STATE["feed"]) > 50:
            STATE["feed"].pop(0)


def update_last_feed_or_add(entry: dict):
    with STATE_LOCK:
        if STATE["feed"] and STATE["feed"][-1].get("reply") == "Thinking...":
            STATE["feed"][-1] = entry
        else:
            STATE["feed"].append(entry)
            if len(STATE["feed"]) > 50:
                STATE["feed"].pop(0)


def scan_wifi_networks() -> list[str]:
    try:
        out = subprocess.run(
            ["netsh", "wlan", "show", "networks"],
            capture_output=True,
            text=True,
            timeout=8,
        ).stdout
        if "location permission" in out.lower():
            return ["__LOCATION_OFF__"]
        names = [
            line.split(":", 1)[1].strip()
            for line in out.splitlines()
            if line.strip().startswith("SSID")
        ]
        unique = []
        for n in names:
            if n and n not in unique:
                unique.append(n)
        return unique
    except Exception as e:
        log.warning("WiFi scan failed: %s", e)
        return []


def list_com_ports() -> list[str]:
    try:
        from serial.tools import list_ports

        # Chitti's own USB port (Espressif chip) first, then any others
        found = sorted(list_ports.comports(), key=lambda p: p.vid != 0x303A)
        return [p.device for p in found]
    except Exception:
        return []


def send_wifi_to_board(ssid: str, password: str, port: str = "") -> tuple[bool, str]:
    """Send Wi-Fi credentials over USB serial to Chitti's ESP32-S3 board."""
    import serial

    if not port or port == "auto":
        port = find_board_port() or ""
    if not port:
        return False, ("Chitti Junior wasn't found on her USB port. Plug the port marked USB (not COM) into this PC "
                       "(some cables only charge), wait 3 seconds, then try again.")

    try:
        s = serial.Serial()
        s.port = port
        s.baudrate = 115200
        s.timeout = 0.5
        s.dtr = False
        s.rts = False
        s.open()
    except Exception as e:
        return False, f"Could not open {port}: {e}"

    try:
        s.write(b"\nCHITTI-PING\n")
        s.flush()
        time.sleep(0.3)
        _ = s.read(2048)

        payload = json.dumps({"ssid": ssid, "pass": password, "server": ""})
        cmd = f"CHITTI-WIFI {payload}\n".encode("utf-8")
        s.write(cmd)
        s.flush()

        deadline = time.time() + 6.0
        buf = b""
        while time.time() < deadline:
            chunk = s.read(1024)
            if chunk:
                buf += chunk
                text = buf.decode("utf-8", errors="ignore")
                if "OK" in text or "SAVED" in text or "RESTART" in text:
                    s.close()
                    return True, "Wi-Fi saved successfully! Chitti is restarting and connecting..."
                if "ERR" in text or "FAIL" in text:
                    s.close()
                    return False, f"Board reported error: {text.strip()}"
            time.sleep(0.1)

        s.close()
        return True, "Credentials sent. Check Chitti's screen for connection status."
    except Exception as e:
        try:
            s.close()
        except Exception:
            pass
        return False, f"Communication error: {e}"


# ---------------------------------------------------------------- firmware flashing (from the dashboard)
FIRMWARE_DIR = ROOT / "firmware" / "prebuilt"
FLASH_PARTS = [("0x0", "bootloader.bin"), ("0x8000", "partition-table.bin"), ("0x10000", "chitti_junior.bin")]
FLASH = {"running": False, "pct": 0, "stage": "", "ok": None, "message": "", "port": ""}
_FLASH_LOCK = threading.Lock()


def _flash_set(**kw):
    with _FLASH_LOCK:
        FLASH.update(kw)


def flash_status() -> dict:
    with _FLASH_LOCK:
        return dict(FLASH)


def find_flash_port() -> str | None:
    """Best port to install software: the board's COM port (USB-to-serial chip, switches into download mode
    by itself), otherwise its own USB port."""
    try:
        from serial.tools import list_ports
        ports = list(list_ports.comports())
        for p in ports:
            if p.vid in (0x1A86, 0x10C4, 0x0403):  # CH34x / CP210x / FTDI
                return p.device
        for p in ports:
            if p.vid == 0x303A:
                return p.device
    except Exception:
        pass
    return None


def find_board_port() -> str | None:
    """The board's USB port (Espressif's USB id), or None when it isn't plugged in."""
    try:
        from serial.tools import list_ports
        for p in list_ports.comports():
            if p.vid == 0x303A:
                return p.device
    except Exception:
        pass
    return None


def _check_firmware_files() -> str | None:
    """Return an error message if a firmware file is missing or damaged (bad download)."""
    import hashlib
    sums = {}
    sums_file = FIRMWARE_DIR / "SHA256SUMS.txt"
    if sums_file.exists():
        for line in sums_file.read_text(encoding="utf-8").splitlines():
            parts = line.split()
            if len(parts) == 2:
                sums[parts[1].lstrip("*")] = parts[0].lower()
    for _, name in FLASH_PARTS:
        f = FIRMWARE_DIR / name
        if not f.exists():
            return f"firmware/prebuilt/{name} is missing. Download the project again."
        if name in sums and hashlib.sha256(f.read_bytes()).hexdigest() != sums[name]:
            return f"firmware/prebuilt/{name} is damaged. Download the project again."
    return None


def _friendly_flash_error(text: str) -> str:
    t = text.lower()
    if "could not open" in t or "access is denied" in t or "permissionerror" in t or "port is busy" in t:
        return "The USB port is busy. Close Arduino IDE, serial monitors or other flashing tools, then try again."
    if "failed to connect" in t or "no serial data" in t or "timed out waiting" in t:
        return ("Couldn't talk to the board. Hold the BOOT button, tap RESET, let go of BOOT, then try again. "
                "If it still fails, try another USB-C data cable.")
    if "wrong chip" in t or "unexpected chip" in t or "this chip is" in t:
        return "This board isn't an ESP32-S3. Chitti Junior needs an ESP32-S3 N16R8 development board."
    if "exceeds" in t and "flash" in t:
        return "This board's flash is too small. Chitti Junior needs the N16R8 version (16 MB flash)."
    return "Flashing failed. Unplug the board, plug it back in and try again."


def _run_esptool(args: list[str], stage: str, base: float, span: float, sizes: list[int] | None = None) -> tuple[bool, str]:
    """Run esptool, turning its output into one overall percentage (base .. base+span)."""
    import re
    cmd = [sys.executable, "-u", "-m", "esptool", *args]
    env = dict(os.environ, PYTHONUNBUFFERED="1", PYTHONIOENCODING="utf-8")
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, env=env,
                            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    total = sum(sizes) if sizes else 0
    done_bytes, file_i, tail, line = 0, 0, [], b""
    pct_re = re.compile(rb"(\d{1,3}(?:\.\d+)?)\s?%")
    while True:
        ch = proc.stdout.read(1)
        if not ch:
            break
        if ch not in (b"\r", b"\n"):
            line += ch
            continue
        if not line.strip():
            line = b""
            continue
        text = line.decode("utf-8", "ignore").strip()
        tail = (tail + [text])[-25:]
        low = text.lower()
        if "connecting" in low:
            _flash_set(stage="Connecting to the board…")
        elif sizes and low.startswith("wrote ") and file_i < len(sizes):
            done_bytes += sizes[file_i]
            file_i += 1
            _flash_set(pct=round(base + span * done_bytes / total, 1))
        else:
            m = pct_re.findall(line)
            if m:
                p = min(100.0, float(m[-1])) / 100
                if sizes and file_i < len(sizes):
                    p = (done_bytes + sizes[file_i] * p) / total
                _flash_set(stage=stage, pct=round(base + span * p, 1))
        line = b""
    proc.wait()
    return proc.returncode == 0, "\n".join(tail)


def _flash_worker(port: str, erase: bool, backup: bool):
    try:
        common = ["--chip", "esp32s3", "--port", port, "--baud", "921600"]
        scale = 1.0
        base = 0.0
        if backup:
            (ROOT / "backup").mkdir(exist_ok=True)
            out = ROOT / "backup" / "factory_backup.bin"
            _flash_set(stage="Saving a backup of the board's original software (about 3 min)…")
            ok, txt = _run_esptool(common + ["read_flash", "0", "0x1000000", str(out)],
                                   "Saving a backup of the original software…", 0, 40)
            if not ok:
                return _flash_set(running=False, ok=False, message=_friendly_flash_error(txt))
            base, scale = 40.0, 0.6
        if erase:
            _flash_set(stage="Erasing the board…")
            ok, txt = _run_esptool(common + ["erase_flash"], "Erasing the board…", base, 10 * scale)
            if not ok:
                return _flash_set(running=False, ok=False, message=_friendly_flash_error(txt))
            base += 10 * scale
        sizes = [(FIRMWARE_DIR / n).stat().st_size for _, n in FLASH_PARTS]
        files = []
        for addr, name in FLASH_PARTS:
            files += [addr, str(FIRMWARE_DIR / name)]
        _flash_set(stage="Installing Chitti…")
        ok, txt = _run_esptool(common + ["--before", "default_reset", "--after", "hard_reset", "write_flash",
                                         "--flash_mode", "dio", "--flash_freq", "80m", "--flash_size", "16MB", *files],
                               "Installing Chitti…", base, 100 - base, sizes)
        if not ok:
            return _flash_set(running=False, ok=False, message=_friendly_flash_error(txt))
        save_settings({"firmware_flashed": True})
        _flash_set(running=False, ok=True, pct=100, stage="Done",
                   message="Chitti Junior is installed! The board restarts by itself; her eyes appear in a few seconds.")
    except Exception as e:
        log.exception("flashing failed")
        _flash_set(running=False, ok=False, message=f"Flashing failed: {e}")


def start_flash(port: str = "", erase: bool = True, backup: bool = False) -> tuple[bool, str]:
    with _FLASH_LOCK:
        if FLASH["running"]:
            return False, "Already installing. Please wait."
    err = _check_firmware_files()
    if err:
        return False, err
    port = port or find_flash_port() or ""
    if not port:
        return False, ("Chitti Junior's board wasn't found. Plug it into this PC with a USB-C data cable "
                       "(some cables only charge). If it's plugged in, hold BOOT, tap RESET, then try again.")
    _flash_set(running=True, pct=0, ok=None, message="", stage="Starting…", port=port)
    threading.Thread(target=_flash_worker, args=(port, erase, backup), daemon=True).start()
    return True, "started"


FREE_VOICE = "edge:te-IN-ShrutiNeural"            # works with no Sarvam key
SARVAM_VOICE = "sarvam:kavitha_te_narration"      # used once a Sarvam key is added


def sync_voice_with_keys() -> None:
    """No Sarvam key -> use the free voice. Sarvam key added -> switch to a Sarvam voice,
    unless the person picked a free voice themselves (voice_auto False)."""
    s = load_settings()
    voice, auto = s.get("voice", ""), s.get("voice_auto", True)
    has_sarvam = bool(os.environ.get("SARVAM_API_KEY"))
    new = None
    if not has_sarvam and voice.startswith("sarvam"):
        new = FREE_VOICE
    elif has_sarvam and voice.startswith("edge:") and auto:
        new = SARVAM_VOICE
    if new:
        save_settings({"voice": new, "voice_auto": True})
        update_state(selected_voice=new)
        if CALLBACKS.get("update_voice"):
            CALLBACKS["update_voice"](new)
        log.info("voice set to %s (%s Sarvam key)", new, "with" if has_sarvam else "no")


def _set_user_env(name: str, value: str | None) -> None:
    """Save (or with None, delete) an environment variable for this Windows user, without a shell command."""
    import ctypes
    import winreg

    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, "Environment", 0, winreg.KEY_SET_VALUE) as k:
        if value is None:
            try:
                winreg.DeleteValue(k, name)
            except FileNotFoundError:
                pass
        else:
            winreg.SetValueEx(k, name, 0, winreg.REG_SZ, value)
    # tell Windows (and new terminals) that the environment changed
    ctypes.windll.user32.SendMessageTimeoutW(0xFFFF, 0x001A, 0, "Environment", 0x0002, 2000, None)


def _valid_key_text(key: str) -> bool:
    # API keys are letters, digits, '-' and '_' only; anything else is rejected outright
    return 8 <= len(key) <= 300 and all(c.isalnum() or c in "-_" for c in key)


def _write_env_key(name: str, value: str | None) -> None:
    """Remove EVERY old line for this key from the server .env file, then add the new one (None = just remove)."""
    import re
    env_path = HERE / ".env"
    old = re.compile(r"^\s*(export\s+)?" + re.escape(name) + r"\s*=")
    lines = []
    if env_path.exists():
        lines = [ln for ln in env_path.read_text(encoding="utf-8-sig").splitlines() if not old.match(ln)]
    if value:
        lines.append(f"{name}={value}")
    env_path.write_text("\n".join(lines) + ("\n" if lines else ""), encoding="utf-8")


def _store_key(name: str, value: str | None) -> bool:
    """Replace (or remove) a key in all three places Chitti keeps it: the .env file, Windows, this process."""
    ok = True
    try:
        _write_env_key(name, value)
    except Exception as e:
        ok = False
        log.warning("Failed to update .env for %s: %s", name, e)
    try:
        _set_user_env(name, value)
    except Exception as e:
        log.warning("Failed to update the Windows setting for %s: %s", name, e)
    if value:
        os.environ[name] = value
    else:
        os.environ.pop(name, None)
    return ok


def check_claude_key(key: str) -> tuple[bool, str]:
    """Ask Anthropic whether this key works, before it replaces the old one."""
    try:
        import anthropic
        anthropic.Anthropic(api_key=key, timeout=15, max_retries=0).models.list(limit=1)
        return True, ""
    except Exception as e:
        name = type(e).__name__
        if name in ("AuthenticationError", "PermissionDeniedError"):
            return False, "This key doesn't work (wrong, copied incompletely, or deleted). Your previous key was kept."
        log.info("Couldn't check the Claude key (%s); saving it anyway", name)
        return True, "(couldn't check it online right now)"


def save_api_key_disk(key: str) -> bool:
    key = key.strip()
    return bool(key) and _store_key("ANTHROPIC_API_KEY", key)


def save_sarvam_key_disk(key: str) -> bool:
    key = key.strip()
    return bool(key) and _store_key("SARVAM_API_KEY", key)


DASHBOARD_HTML = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Chitti Junior</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link href="https://fonts.googleapis.com/css2?family=Plus+Jakarta+Sans:wght@400;500;600;700;800&display=swap" rel="stylesheet">
<style>
  :root {
    --canvas: #f3f5f9; --card: #ffffff; --card2: #f5f7fb; --line: #e4e9f0; --ink: #0e131b; --mut: #667387;
    --mint: #3fe0c5; --mint-deep: #0e9f8a; --mint-ink: #04241f; --mint-soft: #e2faf5;
    --red: #ef4444; --amber: #f59e0b; --green: #22c55e; --cream: #fff6df; --cream-line: #f5e2ae;
    --shadow: 0 1px 2px rgba(15,23,42,.05), 0 6px 16px rgba(15,23,42,.06), 0 18px 40px rgba(15,23,42,.06);
    --glass: rgba(255,255,255,.75);
  }
  :root[data-theme="dark"] {
    --canvas: #0b0e13; --card: #141920; --card2: #1a2029; --line: #242c37; --ink: #ecf1f7; --mut: #8b96a6;
    --mint-soft: #10302a; --cream: #2a2416; --cream-line: #4a3d1c; --glass: rgba(16,20,26,.75);
    --shadow: 0 1px 2px rgba(0,0,0,.4), 0 12px 30px rgba(0,0,0,.35);
  }
  * { box-sizing: border-box; margin: 0; padding: 0; }
  html { scroll-behavior: smooth; }
  body { background: var(--canvas); color: var(--ink); font: 14px/1.55 "Plus Jakarta Sans", "Segoe UI", system-ui, sans-serif; }
  body::before { content: ""; position: fixed; inset: 0; z-index: -1; pointer-events: none;
    background: radial-gradient(700px 400px at 8% -5%, rgba(63,224,197,.16), transparent 60%); }
  button, input, select { font: inherit; color: inherit; }
  a { color: var(--mint-deep); }
  .wrap { max-width: 1180px; margin: 0 auto; padding: 0 22px 30px; }
  .topbar { position: sticky; top: 0; z-index: 20; backdrop-filter: blur(16px); -webkit-backdrop-filter: blur(16px); background: var(--glass); border-bottom: 1px solid var(--line); }
  .topbar-in { max-width: 1180px; margin: 0 auto; padding: 12px 22px; display: flex; align-items: center; gap: 16px; }
  .brand { display: flex; align-items: center; gap: 10px; font-weight: 800; letter-spacing: 3px; font-size: 17px; }
  .tabs { display: flex; gap: 2px; flex: 1; justify-content: center; overflow-x: auto; scrollbar-width: none; }
  .tabs a { text-decoration: none; padding: 7px 14px; border-radius: 10px; color: var(--mut); font-weight: 600; font-size: 13px; white-space: nowrap; }
  .tabs a:hover { color: var(--ink); background: var(--card2); }
  .tabs a.on { color: var(--mint-ink); background: var(--mint); }
  .icon-btn { width: 36px; height: 36px; border-radius: 10px; border: 1px solid var(--line); background: var(--card); display: grid; place-items: center; cursor: pointer; }
  .pill { display: inline-flex; align-items: center; gap: 7px; padding: 8px 15px; border-radius: 999px; border: 1px solid var(--line); background: var(--card); cursor: pointer; font-weight: 600; font-size: 13px; white-space: nowrap; }
  .pill:hover { background: var(--card2); }
  .pill.mint { background: linear-gradient(135deg, #5cf0d6, #2fd0b6); border-color: transparent; color: var(--mint-ink); }
  .pill.dark { background: var(--ink); border-color: var(--ink); color: var(--card); }
  .pill.danger-solid { background: var(--red); border-color: var(--red); color: #fff; }
  .pill.small { padding: 5px 11px; font-size: 12px; }
  .dot { width: 8px; height: 8px; border-radius: 50%; background: var(--amber); display: inline-block; flex-shrink: 0; }
  .dot.ok { background: var(--green); }
  .banner { margin-top: 18px; background: var(--cream); border: 1px solid var(--cream-line); border-radius: 14px; padding: 11px 16px; color: #6b4e0b; font-weight: 500; display: flex; gap: 12px; align-items: center; }
  :root[data-theme="dark"] .banner { color: #f3d98b; }
  .banner.ok { background: var(--mint-soft); border-color: transparent; color: #0b5c50; }
  :root[data-theme="dark"] .banner.ok { color: var(--mint); }
  .banner .pill { margin-left: auto; }
  .hero { margin-top: 18px; border-radius: 26px; color: #eef3f8; overflow: hidden; display: grid; grid-template-columns: minmax(0, 1.2fr) minmax(260px, .8fr);
          background: radial-gradient(800px 420px at 78% 40%, rgba(63,224,197,.28), transparent 60%), linear-gradient(135deg, #10161d, #0b0f14); box-shadow: 0 30px 60px rgba(8,12,18,.25); }
  .hero-text { padding: 32px 36px; display: flex; flex-direction: column; gap: 14px; }
  .hero-text h1 { font-size: 50px; line-height: 1; font-weight: 800; letter-spacing: 2px; background: linear-gradient(90deg, #fff, #b9fff2 55%, #3fe0c5); -webkit-background-clip: text; background-clip: text; color: transparent; }
  .hero-sub { color: #9fb0c2; }
  .hchip { display: inline-flex; align-items: center; gap: 7px; padding: 6px 12px; border-radius: 999px; background: rgba(255,255,255,.07); border: 1px solid rgba(255,255,255,.09); font-size: 12.5px; font-weight: 600; margin-right: 6px; }
  .bubble { background: rgba(255,255,255,.07); border: 1px solid rgba(255,255,255,.1); border-radius: 18px 18px 18px 6px; padding: 14px 16px; }
  .bubble .said { font-size: 18px; font-weight: 700; line-height: 1.4; word-break: break-word; }
  .bubble .sub { color: #a9b6c4; font-size: 13.5px; margin-top: 4px; }
  .stage { display: grid; place-items: center; min-height: 300px; position: relative; }
  .halo { position: absolute; width: 280px; height: 280px; border-radius: 50%; background: radial-gradient(circle, rgba(63,224,197,.3), transparent 65%); }
  .robot { width: 240px; height: 280px; position: relative; overflow: visible; }
  .robot .float { animation: float 3.4s ease-in-out infinite; }
  .robot .eye { transform-box: fill-box; transform-origin: center; animation: blink 5s infinite; }
  .robot .eyes { transition: transform .25s ease-out; }
  .robot .mouth { transform-box: fill-box; transform-origin: center; }
  .robot .jet { transform-box: fill-box; transform-origin: top center; animation: jet .18s ease-in-out infinite alternate; }
  .robot .ripple, .robot .dots { opacity: 0; transition: opacity .3s; }
  .robot .ripple circle { transform-box: fill-box; transform-origin: center; }
  [data-act="speaking"] .robot .mouth { animation: talk .22s ease-in-out infinite alternate; }
  [data-act="listening"] .robot .ripple { opacity: 1; }
  [data-act="listening"] .robot .ripple circle { animation: ripple 1.6s ease-out infinite; }
  [data-act="thinking"] .robot .dots { opacity: 1; }
  [data-act="thinking"] .robot .dots circle { animation: dot 1.2s ease-in-out infinite; }
  @keyframes float { 0%,100% { transform: translateY(0) } 50% { transform: translateY(-12px) } }
  @keyframes blink { 0%,46%,50%,100% { transform: scaleY(1) } 48% { transform: scaleY(.1) } }
  @keyframes jet { from { transform: scaleY(.75); opacity: .7 } to { transform: scaleY(1.1); opacity: 1 } }
  @keyframes talk { from { transform: scaleY(.55) } to { transform: scaleY(1.35) } }
  @keyframes ripple { from { transform: scale(.7); opacity: .9 } to { transform: scale(1.5); opacity: 0 } }
  @keyframes dot { 0%,100% { transform: translateY(0); opacity: .4 } 50% { transform: translateY(-6px); opacity: 1 } }
  @media (prefers-reduced-motion: reduce) { .robot * { animation: none !important; } }
  .row { display: grid; gap: 18px; margin-top: 18px; }
  .r-two { grid-template-columns: minmax(0, 1.4fr) minmax(300px, 1fr); }
  .r-even { grid-template-columns: 1fr 1fr; }
  .r-three { grid-template-columns: repeat(3, minmax(0, 1fr)); }
  .card { background: var(--card); border: 1px solid var(--line); border-radius: 20px; padding: 20px 22px; box-shadow: var(--shadow); min-width: 0; display: flex; flex-direction: column; }
  .card h3 { font-size: 15px; font-weight: 700; margin-bottom: 14px; display: flex; align-items: center; gap: 8px; }
  .card h3 .right { margin-left: auto; font-weight: 500; font-size: 12.5px; color: var(--mut); }
  section { scroll-margin-top: 80px; }
  h2.sec { font-size: 13px; letter-spacing: 1.5px; text-transform: uppercase; color: var(--mut); margin: 30px 4px 0; }
  .mut { color: var(--mut); } .small { font-size: 12.5px; }
  .grow-y { flex: 1; }
  .feed { flex: 1; min-height: 220px; max-height: 380px; overflow-y: auto; display: flex; flex-direction: column; gap: 10px; }
  .msg { max-width: 85%; padding: 10px 14px; border-radius: 16px; word-break: break-word; }
  .msg.me { align-self: flex-end; background: var(--card2); border: 1px solid var(--line); }
  .msg.bot { align-self: flex-start; background: var(--mint-soft); }
  .msg .s { color: var(--mut); font-size: 12.5px; margin-top: 3px; }
  .empty { text-align: center; color: var(--mut); padding: 26px 10px; }
  .ask { display: flex; gap: 8px; margin-top: 14px; background: var(--card2); border: 1px solid var(--line); border-radius: 14px; padding: 6px 6px 6px 14px; }
  .ask input { flex: 1; border: 0; background: transparent; outline: none; min-width: 0; }
  .chips { display: flex; gap: 8px; flex-wrap: wrap; margin-top: 10px; }
  .chip-btn { border: 1px solid var(--line); background: var(--card); border-radius: 10px; padding: 6px 11px; font-size: 12.5px; cursor: pointer; }
  .chip-btn.on { background: var(--mint); border-color: var(--mint); color: var(--mint-ink); font-weight: 700; }
  .steps { display: flex; flex-direction: column; gap: 10px; }
  .step { display: flex; gap: 12px; align-items: flex-start; background: var(--card2); border: 1px solid var(--line); border-radius: 14px; padding: 12px 14px; }
  .step .ico { width: 26px; height: 26px; border-radius: 50%; display: grid; place-items: center; font-weight: 800; flex-shrink: 0; font-size: 13px; }
  .ico.ok { background: #dcfce7; color: #15803d; } .ico.no { background: #fee2e2; color: #b91c1c; } .ico.opt { background: #fef3c7; color: #a16207; }
  :root[data-theme="dark"] .ico.ok { background: #14532d; color: #bbf7d0; } :root[data-theme="dark"] .ico.no { background: #450a0a; color: #fecaca; } :root[data-theme="dark"] .ico.opt { background: #422006; color: #fde68a; }
  .step b { display: block; } .step span { color: var(--mut); font-size: 12.5px; }
  label { display: block; font-size: 12px; font-weight: 700; color: var(--mut); margin: 14px 0 6px; }
  label:first-of-type { margin-top: 0; }
  .in { width: 100%; border: 1px solid var(--line); background: var(--card2); border-radius: 11px; padding: 9px 12px; outline: none; }
  .in:focus { border-color: var(--mint); }
  .inrow { display: flex; gap: 8px; }
  .alert { display: none; margin-top: 10px; padding: 9px 12px; border-radius: 10px; font-size: 13px; }
  .alert.good { display: block; background: var(--mint-soft); color: #0b5c50; }
  .alert.bad { display: block; background: #fdecec; color: #a12020; }
  :root[data-theme="dark"] .alert.good { color: var(--mint); } :root[data-theme="dark"] .alert.bad { background: #3a1717; color: #ffb3b3; }
  .help p, .help li { color: var(--ink); } .help ol, .help ul { margin: 6px 0 0 20px; } .help li { margin: 4px 0; }
  .flow { display: flex; align-items: center; gap: 10px; flex-wrap: wrap; margin: 6px 0 12px; }
  .flow span.box { background: var(--card2); border: 1px solid var(--line); border-radius: 12px; padding: 8px 12px; font-weight: 600; font-size: 13px; }
  footer { margin-top: 28px; padding-top: 16px; border-top: 1px solid var(--line); display: flex; justify-content: space-between; flex-wrap: wrap; gap: 8px; color: var(--mut); font-size: 12.5px; }
  footer b { color: var(--mint-deep); }
  @media (max-width: 900px) { .hero, .r-two, .r-even, .r-three { grid-template-columns: 1fr; } .stage { order: -1; min-height: 240px; } .hero-text { padding: 10px 22px 26px; } }
  @media (max-width: 600px) { .wrap { padding: 0 14px 24px; } .hero-text h1 { font-size: 38px; } .brand span { display: none; } }


  /* ---- soft, shadowed look: depth from shadows, not outlines */
  :root { --line: #e9edf3; --sh-sm: 0 1px 2px rgba(15,23,42,.06), 0 2px 6px rgba(15,23,42,.06);
          --sh-md: 0 2px 4px rgba(15,23,42,.04), 0 10px 24px rgba(15,23,42,.07), 0 24px 48px rgba(15,23,42,.05);
          --sh-in: inset 0 1px 3px rgba(15,23,42,.08); }
  :root[data-theme="dark"] { --line: #1f2630; --sh-sm: 0 1px 2px rgba(0,0,0,.5), 0 3px 8px rgba(0,0,0,.35);
          --sh-md: 0 2px 6px rgba(0,0,0,.4), 0 14px 32px rgba(0,0,0,.45); --sh-in: inset 0 1px 3px rgba(0,0,0,.5); }
  .topbar { border-bottom: 0; box-shadow: 0 1px 0 rgba(15,23,42,.03), 0 8px 24px rgba(15,23,42,.06); }
  .card { border: 0; border-radius: 22px; box-shadow: var(--sh-md); transition: box-shadow .2s, transform .2s; }
  .card:hover { box-shadow: 0 4px 8px rgba(15,23,42,.05), 0 16px 36px rgba(15,23,42,.09), 0 30px 60px rgba(15,23,42,.06); }
  .pill, .icon-btn, .chip-btn { border: 0; box-shadow: var(--sh-sm); transition: transform .12s, box-shadow .2s, background .2s; }
  .pill:hover, .icon-btn:hover, .chip-btn:hover { transform: translateY(-1px); box-shadow: 0 2px 4px rgba(15,23,42,.08), 0 8px 18px rgba(15,23,42,.10); }
  .pill:active, .chip-btn:active { transform: translateY(0); }
  .pill.mint { box-shadow: 0 4px 14px rgba(47,208,182,.35); }
  .pill.danger-solid { box-shadow: 0 4px 14px rgba(239,68,68,.3); }
  .pill.dark { box-shadow: 0 4px 14px rgba(14,19,27,.25); }
  .chip-btn.on { box-shadow: 0 4px 12px rgba(47,208,182,.35); }
  .in { border: 0; box-shadow: var(--sh-in); transition: box-shadow .2s; }
  .in:focus { box-shadow: var(--sh-in), 0 0 0 3px rgba(63,224,197,.35); }
  .ask { border: 0; box-shadow: var(--sh-in); }
  .ask:focus-within { box-shadow: var(--sh-in), 0 0 0 3px rgba(63,224,197,.3); }
  .step, .usb, .flow span.box { border: 0; box-shadow: var(--sh-sm); background: var(--card); }
  .msg.me { border: 0; box-shadow: var(--sh-sm); background: var(--card); }
  .msg.bot { box-shadow: 0 2px 8px rgba(47,208,182,.15); }
  .banner { border: 0; box-shadow: var(--sh-sm); }
  .wiz { border: 0; }
  .bar { border: 0; box-shadow: var(--sh-in); }
  .tabs a.on { box-shadow: 0 4px 12px rgba(47,208,182,.35); }
  .pill.small { box-shadow: var(--sh-sm); }
  footer { border-top: 0; }
  .author { display: flex; gap: 18px; align-items: center; flex-wrap: wrap; }
  .author .av { width: 58px; height: 58px; border-radius: 50%; display: grid; place-items: center; font-weight: 800; font-size: 20px; color: var(--mint-ink);
                background: linear-gradient(135deg, #5cf0d6, #2fd0b6); box-shadow: 0 6px 18px rgba(47,208,182,.4); flex-shrink: 0; }
  .author .who { flex: 1; min-width: 200px; }
  .author .who b { font-size: 16px; }
  .socials { display: flex; flex-wrap: wrap; gap: 8px; margin-top: 12px; }
  .socials a { text-decoration: none; color: var(--ink); }
  .socials .sd { width: 9px; height: 9px; border-radius: 50%; display: inline-block; }

  /* ---- layout polish */
  h2.sec { margin: 24px 6px 0; }
  .row { margin-top: 14px; }
  .feed { min-height: 150px; }
  .empty { padding: 34px 10px; display: flex; flex-direction: column; align-items: center; gap: 6px; }
  .empty .big { font-size: 28px; }
  .step { padding: 10px 12px; }
  .steps { gap: 8px; }
  .flow { gap: 6px; font-size: 12px; color: var(--mut); }
  .flow span.box { padding: 6px 10px; font-size: 12px; color: var(--ink); }
  .kv { display: flex; flex-direction: column; gap: 8px; margin-top: 14px; }
  .kv div { display: flex; justify-content: space-between; align-items: center; background: var(--card2); border-radius: 12px; padding: 9px 12px; font-size: 13px; box-shadow: var(--sh-in); }
  .kv span { color: var(--mut); }
  .card > .pill:last-child, .card .actions { margin-top: auto; }
  #hardware .step { padding: 8px 12px; } #hardware .steps { gap: 6px; } #hardware .step span { font-size: 12px; }
  .risknote { margin-top: 12px; font-size: 12.5px; background: var(--cream); color: #6b4e0b; border-radius: 12px; padding: 10px 12px; box-shadow: var(--sh-sm); }
  :root[data-theme="dark"] .risknote { color: #f3d98b; }
  .help .chips .chip-btn { cursor: default; }
  .help .chips .chip-btn:hover { transform: none; }
  .riskbar { position: fixed; left: 0; right: 0; bottom: 0; z-index: 30; text-align: center; padding: 7px 14px; font-size: 12px; font-weight: 600;
             background: var(--glass); backdrop-filter: blur(14px); -webkit-backdrop-filter: blur(14px); color: var(--mut); box-shadow: 0 -6px 20px rgba(15,23,42,.06); }
  .riskbar b { color: var(--ink); } .riskbar a { font-weight: 700; }
  body { padding-bottom: 40px; }
  @media (max-width: 600px) { .riskbar { font-size: 11px; padding: 6px 10px; } .riskbar .long { display: none; } }
  .wiz-back { position: fixed; inset: 0; z-index: 50; background: rgba(8,12,18,.55); backdrop-filter: blur(6px); -webkit-backdrop-filter: blur(6px); display: none; align-items: center; justify-content: center; padding: 16px; }
  .wiz-back.open { display: flex; }
  .wiz { width: 100%; max-width: 560px; max-height: calc(100vh - 32px); overflow-y: auto; background: var(--card); border: 1px solid var(--line); border-radius: 24px; box-shadow: 0 30px 80px rgba(0,0,0,.35); padding: 26px 28px; }
  .wiz-top { display: flex; align-items: center; gap: 10px; margin-bottom: 18px; }
  .wiz-top .x { margin-left: auto; border: 0; background: none; cursor: pointer; color: var(--mut); font-size: 13px; }
  .wiz-dots { display: flex; gap: 6px; }
  .wiz-dots span { width: 28px; height: 6px; border-radius: 3px; background: var(--line); }
  .wiz-dots span.on { background: var(--mint); }
  .wiz h2 { font-size: 22px; font-weight: 800; margin-bottom: 6px; }
  .wiz p.lead { color: var(--mut); margin-bottom: 16px; }
  .wiz-step { display: none; } .wiz-step.on { display: block; }
  .wiz-nav { display: flex; gap: 10px; margin-top: 22px; align-items: center; }
  .wiz-nav .sp { flex: 1; }
  .linkbtn { border: 0; background: none; color: var(--mut); cursor: pointer; text-decoration: underline; font-size: 13px; }
  .usb { display: flex; align-items: center; gap: 10px; background: var(--card2); border: 1px solid var(--line); border-radius: 14px; padding: 12px 14px; font-weight: 600; }
  .bar { height: 12px; border-radius: 6px; background: var(--card2); border: 1px solid var(--line); overflow: hidden; margin-top: 14px; }
  .bar div { height: 100%; width: 0; background: linear-gradient(90deg, #5cf0d6, #2fd0b6); transition: width .4s; }
  .check { display: flex; gap: 8px; align-items: flex-start; margin-top: 10px; font-size: 13px; cursor: pointer; }
  .check input { margin-top: 3px; accent-color: var(--mint-deep); }
  .pill:disabled { opacity: .5; cursor: not-allowed; }
  .bigok { font-size: 46px; text-align: center; margin: 6px 0; }
</style>
</head>
<body>
<header class="topbar">
  <div class="topbar-in">
    <div class="brand"><svg width="30" height="30" viewBox="0 0 34 34" aria-hidden="true"><rect x="1" y="1" width="32" height="32" rx="11" fill="#2fd0b6"/><rect x="6" y="10" width="22" height="14" rx="7" fill="#0b0e12"/><rect x="11" y="14" width="3.5" height="6" rx="1.75" fill="#7ff8ff"/><rect x="19.5" y="14" width="3.5" height="6" rx="1.75" fill="#7ff8ff"/></svg><span>CHITTI JR</span></div>
    <nav class="tabs" id="nav">
      <a href="#home" class="on">Home</a><a href="#settings">Settings</a><a href="#device">Device</a><a href="#help">Help</a><a href="#hardware">Hardware</a><a href="#disclaimer">Disclaimer</a><a href="#author">Author</a>
    </nav>
    <span class="pill small"><span class="dot" id="boardDot"></span><span id="boardStatusText">Connecting…</span></span>
    <button class="icon-btn" onclick="toggleTheme()" aria-label="Light or dark mode"><svg width="17" height="17" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M21 12.8A9 9 0 1 1 11.2 3a7 7 0 0 0 9.8 9.8z"/></svg></button>
  </div>
</header>

<div class="wrap">
  <div class="banner" id="banner"><span id="bannerText">Checking…</span><button class="pill" id="bannerBtn" style="display:none"></button></div>

  <section class="hero" id="home">
    <div class="hero-text">
      <h1>CHITTI JR</h1>
      <div class="hero-sub">Your Telugu AI friend. Press the BOOT button and talk.<br><span style="font-size:12.5px">Created by <a href="https://chaitanyaeshwarprasad.com" target="_blank" rel="noopener" style="color:#7ff8e6;text-decoration:none;font-weight:700">Chaitanya Eshwar Prasad</a></span></div>
      <div><span class="hchip"><span class="dot" id="heroDot"></span><span id="statActivity">Offline</span></span><span class="hchip">Battery · <span id="heroBatt">–</span></span><span class="hchip">Brain · <span id="modelText">–</span></span></div>
      <div class="bubble"><div class="said" id="heroSaid">Hi! Set me up below, then press my BOOT button and say hi.</div><div class="sub" id="heroSub"></div></div>
    </div>
    <div class="stage" id="stage" data-act="idle">
      <div class="halo"></div>
      <svg class="robot" viewBox="0 0 240 300" aria-label="Chitti">
        <defs>
          <radialGradient id="rHead" cx="38%" cy="28%" r="80%"><stop offset="0" stop-color="#fff"/><stop offset=".65" stop-color="#e3eaef"/><stop offset="1" stop-color="#b8c4cd"/></radialGradient>
          <linearGradient id="rHair" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="#6ff3dd"/><stop offset="1" stop-color="#20b9a0"/></linearGradient>
          <radialGradient id="rJet" cx="50%" cy="0%" r="100%"><stop offset="0" stop-color="#e8fffb"/><stop offset=".4" stop-color="#5cf0d6"/><stop offset="1" stop-color="rgba(63,224,197,0)"/></radialGradient>
        </defs>
        <ellipse cx="120" cy="284" rx="50" ry="8" fill="#000" opacity=".35"/>
        <g class="float">
          <ellipse class="jet" cx="120" cy="246" rx="16" ry="26" fill="url(#rJet)"/>
          <path d="M74 182 q-22 8 -24 34" stroke="#d5dde3" stroke-width="12" stroke-linecap="round" fill="none"/>
          <path d="M166 182 q22 8 24 34" stroke="#d5dde3" stroke-width="12" stroke-linecap="round" fill="none"/>
          <rect x="74" y="164" width="92" height="76" rx="34" fill="url(#rHead)"/>
          <circle cx="120" cy="198" r="8" fill="#3fe0c5"/>
          <path d="M58 70 Q66 26 120 24 Q174 26 182 70 Q160 44 120 46 Q80 44 58 70Z" fill="url(#rHair)"/>
          <rect x="40" y="50" width="160" height="124" rx="58" fill="url(#rHead)"/>
          <circle cx="40" cy="112" r="15" fill="#d5dde3"/><circle cx="200" cy="112" r="15" fill="#d5dde3"/>
          <rect x="58" y="80" width="124" height="68" rx="34" fill="#0b0e12"/>
          <g class="eyes" id="robotEyes"><rect class="eye" x="86" y="98" width="16" height="28" rx="8" fill="#7ff8ff"/><rect class="eye" x="138" y="98" width="16" height="28" rx="8" fill="#7ff8ff"/></g>
          <path class="mouth" d="M108 133 Q120 145 132 133" stroke="#7ff8ff" stroke-width="4" stroke-linecap="round" fill="none"/>
          <g class="dots"><circle cx="196" cy="40" r="6" fill="#3fe0c5"/><circle cx="214" cy="30" r="6" fill="#3fe0c5"/><circle cx="232" cy="20" r="6" fill="#3fe0c5"/></g>
        </g>
        <g class="ripple" fill="none" stroke="#3fe0c5" stroke-width="2.5"><circle cx="120" cy="112" r="96"/></g>
      </svg>
    </div>
  </section>

  <div class="row r-two">
    <div class="card">
      <h3>Chat<span class="right">try her here (she replies in text)</span></h3>
      <div class="feed" id="feedContainer"><div class="empty"><span class="big">💬</span>No messages yet. Press Junior's BOOT button, or type below.</div></div>
      <div class="ask"><input id="askInput" placeholder="Type to Chitti in Telugu or English…" onkeydown="if(event.key==='Enter') sendTestChat()"><button class="pill dark" onclick="sendTestChat()">Ask</button></div>
      <div class="chips">
        <button class="chip-btn" onclick="quickAsk('హాయ్ చిట్టి, ఏం చేస్తున్నావ్?')">Say hi</button>
        <button class="chip-btn" onclick="quickAsk('నన్ను రోస్ట్ చెయ్')">Roast me</button>
        <button class="chip-btn" onclick="quickAsk('ఈరోజు వాతావరణం ఎలా ఉంది?')">Weather</button>
        <button class="chip-btn" onclick="clearMemory()">Clear chat</button>
      </div>
    </div>
    <div class="card">
      <h3>Setup checklist</h3>
      <div class="steps">
        <div class="step"><div class="ico ok">✓</div><div><b>Chitti's brain is running on this PC</b><span>Keep the black "Start Chitti Junior" window open.</span></div></div>
        <div class="step"><div class="ico" id="stepKey">?</div><div><b>Claude API key</b><span id="stepKeyText">Checking…</span></div></div>
        <div class="step"><div class="ico" id="stepSarvam">?</div><div><b>Sarvam API key (optional)</b><span id="stepSarvamText">Checking…</span></div></div>
        <div class="step"><div class="ico" id="stepBoard">?</div><div><b>Chitti board connected</b><span id="stepBoardText">Checking…</span></div></div>
      </div>
    </div>
  </div>

  <h2 class="sec">Settings</h2>
  <div class="row r-even" id="settings">
    <section class="card">
      <h3>API keys and brain<span class="right">stored only on this PC</span></h3>
      <label>Claude (Anthropic) key: required</label>
      <div class="inrow"><input class="in" type="password" id="apiKeyInput" placeholder="sk-ant-…" autocomplete="off"><button class="pill" onclick="saveApiKey()">Save</button></div>
      <div class="mut small" style="margin-top:6px;display:flex;justify-content:space-between;align-items:center;gap:8px"><span id="keyCurrentStatus">Checking…</span><button class="linkbtn" id="rmClaude" style="display:none" onclick="removeKey('claude')">Delete key</button></div>
      <div id="keyAlert" class="alert"></div>
      <label>Sarvam key: optional (natural Telugu voice)</label>
      <div class="inrow"><input class="in" type="password" id="sarvamKeyInput" placeholder="Sarvam API key" autocomplete="off"><button class="pill" onclick="saveSarvamKey()">Save</button></div>
      <div class="mut small" style="margin-top:6px;display:flex;justify-content:space-between;align-items:center;gap:8px"><span id="sarvamCurrentStatus">Checking…</span><button class="linkbtn" id="rmSarvam" style="display:none" onclick="removeKey('sarvam')">Delete key</button></div>
      <div id="sarvamAlert" class="alert"></div>
      <label>Brain</label>
      <select class="in" id="modelSelect" onchange="saveModel()">
        <option value="claude-haiku-5-5">Haiku 5.5: fast, very cheap (recommended)</option>
        <option value="claude-sonnet-5-5">Sonnet 5.5: smarter, costs several times more</option>
      </select>
      <div class="mut small" style="margin-top:8px">Get keys: <a href="https://console.anthropic.com" target="_blank">console.anthropic.com</a> · <a href="https://dashboard.sarvam.ai" target="_blank">dashboard.sarvam.ai</a>. Set a monthly spend limit there.</div>
      <label>Your city (for weather)</label>
      <div class="inrow"><input class="in" id="cityInput" placeholder="e.g. Hyderabad"><button class="pill" onclick="saveCity()">Save</button></div>
      <div id="cityAlert" class="alert"></div>
    </section>
    <section class="card">
      <h3>Personality</h3>
      <label>Voice</label>
      <div class="inrow">
        <select class="in" id="voiceSelect" onchange="onVoiceChange()">
          <optgroup label="Sarvam voices" id="sarvamVoices">
            <option value="sarvam3:kavitha">Kavitha (female, expressive)</option>
            <option value="sarvam3:neha">Neha (female, expressive)</option>
            <option value="sarvam3:shubh">Shubh (male, expressive)</option>
            <option value="sarvam:kavitha_te_narration">Kavitha (female, fast)</option>
            <option value="sarvam:pooja_te_conversation">Pooja (female, fast)</option>
            <option value="sarvam:tarun_te_narration">Tarun (male, fast)</option>
          </optgroup>
          <optgroup label="Free voices (no key needed)">
            <option value="edge:te-IN-ShrutiNeural">Shruti (female)</option>
            <option value="edge:te-IN-MohanNeural">Mohan (male)</option>
          </optgroup>
        </select>
        <button class="pill" onclick="testVoice()">Test</button>
      </div>
      <div class="mut small" id="engineInfo" style="margin-top:6px"></div>
      <label>Personality</label>
      <div class="chips" id="personaChips" style="margin-top:0"></div>
      <label>Telugu style</label>
      <div class="chips" id="slangChips" style="margin-top:0"></div>
      <label>Words she should use</label>
      <input class="in" id="useWords" placeholder="e.g. రా, రేయ్, ఒరేయ్, అరేయ్">
      <label>Words she must never use</label>
      <div class="inrow"><input class="in" id="avoidWords" placeholder="e.g. మచ్చా"><button class="pill" onclick="saveWords()">Save</button></div>
      <div id="voiceSlangAlert" class="alert"></div>
    </section>
  </div>

  <h2 class="sec">Device</h2>
  <div class="row r-three" id="device">
    <section class="card">
      <h3>Chitti</h3>
      <label>Volume <span id="volVal" style="float:right;color:var(--ink)">–</span></label>
      <input type="range" id="volSlider" min="0" max="100" step="5" value="80" style="width:100%;accent-color:var(--mint-deep)" oninput="$('volVal').textContent=this.value+'%'" onchange="setVolume(this.value)">
      <div class="mut small" style="margin-top:6px">Junior sounds cleanest at 60-80%.</div>
      <div id="volAlert" class="alert"></div>
      <div class="kv">
        <div><span>Status</span><b id="devState">Offline</b></div>
        <div><span>Battery</span><b id="devBatt">–</b></div>
        <div><span>Address</span><b id="statIp">–</b></div>
      </div>
      <button class="pill small" style="margin-top:14px;align-self:flex-start" onclick="testVoice()">Play a test sound</button>
    </section>
    <section class="card">
      <h3>Wi-Fi setup<span class="right">over USB</span></h3>
      <label>Wi-Fi name (2.4 GHz)</label>
      <div class="inrow"><input class="in" id="wifiSsidInput" placeholder="Wi-Fi name"><button class="pill" onclick="scanWifi()">Scan</button></div>
      <select class="in" id="wifiScanSelect" style="display:none;margin-top:6px" onchange="if(this.value) $('wifiSsidInput').value=this.value"></select>
      <label>Password</label>
      <input class="in" type="password" id="wifiPassInput" placeholder="leave empty if open">
      <label>USB port</label>
      <select class="in" id="comPortSelect"><option value="auto">Find it automatically</option></select>
      <div class="mut small" id="portHint" style="margin-top:6px"></div>
      <button class="pill mint" style="margin-top:14px;align-self:flex-start" onclick="sendWifiConfig()" id="btnSendWifi">Send to Chitti</button>
      <div id="wifiAlert" class="alert"></div>
    </section>
    <section class="card">
      <h3>Install or update Chitti Junior</h3>
      <div class="mut small">Writes Chitti Junior's software to the board over USB, or runs the whole setup again.</div>
      <div class="chips"><button class="pill mint small" onclick="openWizard(2)">Install firmware</button><button class="pill small" onclick="openWizard(1)">Run setup again</button></div>
      <h3 style="margin-top:18px">Reset Chitti</h3>
      <div class="mut small grow-y">Erases her memory of you: conversations, facts, reminders and recordings. Keys and settings stay.</div>
      <button class="pill danger-solid" style="margin-top:14px;align-self:flex-start" onclick="resetChitti()">Erase and reset</button>
      <div id="resetAlert" class="alert"></div>
    </section>
  </div>

  <h2 class="sec">Help</h2>
  <section class="row r-even help" id="help">
    <div class="card">
      <h3>How Chitti works</h3>
      <div class="flow"><span class="box">You talk</span>→<span class="box">Chitti (board)</span>→ Wi-Fi →<span class="box">This PC</span>→<span class="box">Claude + Sarvam</span></div>
      <ol>
        <li><b>Press BOOT and talk.</b> Junior records your voice and sends it over your Wi-Fi to this PC.</li>
        <li><b>This PC</b> turns your voice into text (Sarvam, or Whisper on this PC), asks <b>Claude</b> for a reply in Chitti's personality, and turns the reply into Telugu speech.</li>
        <li><b>She answers</b> through her speaker with subtitles and a matching face.</li>
      </ol>
      <p class="small mut" style="margin-top:10px">She finds this PC by herself when both are on the same Wi-Fi. Your keys and chats stay on this PC; only what you say is sent to Claude / Sarvam to make her reply.</p>
    </div>
    <div class="card">
      <h3>If Chitti doesn't connect</h3>
      <ul>
        <li>Keep the <b>"Start Chitti Junior"</b> window open on this PC.</li>
        <li>Chitti and this PC must be on the <b>same Wi-Fi / router</b> (Chitti needs 2.4 GHz, not "5G"). A LAN cable on the PC is fine. Send the Wi-Fi again in Device → Wi-Fi setup (board plugged in by USB).</li>
        <li>Don't use <b>guest Wi-Fi</b>: it stops devices from seeing each other.</li>
        <li>Keep this PC <b>awake</b>. If a laptop sleeps or its lid closes, Chitti goes offline.</li>
        <li>When Windows asks, <b>allow Python</b> through the firewall (private networks).</li>
        <li>Restart Junior (unplug and plug her USB cable) and wait 20 seconds.</li>
        <li>Still stuck? Open the start window: its last lines say what's wrong.</li>
      </ul>
    </div>
  </section>

  <section class="row">
    <div class="card help">
      <h3>Using her</h3>
      <div class="chips" style="margin-top:0">
        <span class="chip-btn">🔘 Press BOOT = talk / stop her</span><span class="chip-btn">✋ Hold BOOT 1.2 s = poke</span>
        <span class="chip-btn">🖥️ Hold BOOT 6 s = screen test</span><span class="chip-btn">😴 Idle 3 min = she sleeps; press BOOT to wake her</span>
      </div>
    </div>
  </section>

  <h2 class="sec">What you need</h2>
  <section class="row r-even help" id="hardware">
    <div class="card">
      <h3>Parts I used</h3>
      <div class="author" style="margin-bottom:12px">
        <div class="av" style="font-size:13px">JR</div>
        <div class="who"><b>Chitti Junior: breadboard build, no soldering</b>
          <div class="mut small">The software is made for these parts and pins. Follow the assembly guide exactly.</div></div>
      </div>
      <a class="pill mint small" style="align-self:flex-start" href="https://github.com/chaitanyaeshwarprasad/chitti-junior/blob/main/ASSEMBLY.md" target="_blank" rel="noopener">Open the assembly guide ↗</a>
      <ul class="small" style="margin-top:14px">
        <li><b>ESP32-S3 N16R8 development board</b>: 16 MB flash, 8 MB PSRAM, two USB-C ports</li>
        <li><b>INMP441 I2S MEMS microphone</b>: her ears</li>
        <li><b>MAX98357A I2S mono amplifier</b>: drives the speaker</li>
        <li><b>0.96-inch OLED I2C display (4-pin)</b>: her glowing-eyes face</li>
        <li><b>8 Ω, 0.5 W speaker</b>: her voice</li>
        <li><b>Breadboard</b> + jumper wires</li>
        <li><b>USB cable</b> (USB-C <b>data</b> cable)</li>
      </ul>
      <p class="small mut" style="margin-top:8px">Any shop or seller is fine; check the names above match. Pins already attached = no soldering.</p>
      <div class="risknote">⚠️ Buying and using the hardware is <b>at your own risk</b>. The creator is not responsible for the board, parts, PC or anything else.</div>
    </div>
    <div class="card">
      <h3>Checklist</h3>
      <div class="steps">
        <div class="step"><div class="ico ok">1</div><div><b>The 7 parts on the left</b><span>Wired as in the assembly guide (about 18 wires).</span></div></div>
        <div class="step"><div class="ico ok">2</div><div><b>USB-C data cable</b><span>Not a charge-only cable. Needed to install Junior's software and set Wi-Fi.</span></div></div>
        <div class="step"><div class="ico ok">3</div><div><b>Windows 10 or 11 PC or laptop</b><span>Stays on while you talk. Wi-Fi or a LAN cable both work.</span></div></div>
        <div class="step"><div class="ico ok">4</div><div><b>2.4 GHz Wi-Fi</b><span>Junior can't use 5 GHz. Same router as the PC; no guest network.</span></div></div>
        <div class="step"><div class="ico ok">5</div><div><b>Your own Claude API key</b><span>Required. From console.anthropic.com, you pay for your own use.</span></div></div>
        <div class="step"><div class="ico opt">+</div><div><b>Optional: Sarvam API key</b><span>For a natural Telugu voice and better hearing.</span></div></div>
        <div class="step"><div class="ico opt">+</div><div><b>Optional: 5 V 2 A charger or power bank</b><span>Cleaner sound than a PC USB port; she runs on it after Wi-Fi setup.</span></div></div>
      </div>
    </div>
  </section>

  <h2 class="sec">Disclaimer</h2>
  <section class="row" id="disclaimer">
    <div class="card help">
      <h3>⚠️ Please read: use at your own risk</h3>
      <p class="small">Chitti is shared <b>free, for learning and fun, "as is", with no warranty of any kind.</b> By installing, flashing or using it, you agree that <b>the author, Chaitanya Eshwar Prasad, is not responsible for anything</b> that happens from using it, including:</p>
      <ul class="small">
        <li><b>Your device:</b> damaged or "bricked" boards, batteries, PCs or other hardware; overheating, fire, injury or electrical problems; data loss.</li>
        <li><b>Your bills:</b> API charges from Anthropic, Sarvam or any other service. You use <b>your own</b> API keys and pay your own bills. Set spending limits on their websites.</li>
        <li><b>Your keys:</b> leaked or misused API keys. Never share them or show them on screen.</li>
        <li><b>What Chitti says:</b> AI replies can be wrong, rude or inappropriate. She is not a medical, legal, financial or safety tool, and not for unsupervised young children.</li>
        <li><b>Batteries:</b> LiPo batteries can be dangerous. Use a protected battery, never leave it charging unattended, and stop if anything gets hot or swells.</li>
        <li><b>Flashing</b> replaces the board's original software. Save a backup in the setup wizard if you may want it back.</li>
      </ul>
      <p class="small mut" style="margin-top:8px">Not affiliated with Espressif, Anthropic, Sarvam AI, Microsoft or any parts seller; all trademarks belong to their owners. You are responsible for following each service's terms and your local laws. MIT licence.</p>
    </div>
  </section>

  <h2 class="sec">About the author</h2>
  <section class="row" id="author">
    <div class="card">
      <div class="author">
        <div class="av">CEP</div>
        <div class="who"><b>Chaitanya Eshwar Prasad</b><div class="mut small">Created and maintains Chitti. Free and open source. Follow for the setup video, updates and more Telugu tech.</div></div>
      </div>
      <div class="socials">
        <a class="pill small" href="https://chaitanyaeshwarprasad.com" target="_blank" rel="noopener"><span class="sd" style="background:#005F9E"></span>Website</a>
        <a class="pill small" href="https://www.youtube.com/@chaitanya.eshwar.prasad" target="_blank" rel="noopener"><span class="sd" style="background:#FF0000"></span>YouTube</a>
        <a class="pill small" href="https://instagram.com/acep.tech.in.telugu" target="_blank" rel="noopener"><span class="sd" style="background:#E4405F"></span>Instagram</a>
        <a class="pill small" href="https://github.com/chaitanyaeshwarprasad" target="_blank" rel="noopener"><span class="sd" style="background:#181717"></span>GitHub</a>
        <a class="pill small" href="https://linkedin.com/in/chaitanya-eshwar-prasad" target="_blank" rel="noopener"><span class="sd" style="background:#0A66C2"></span>LinkedIn</a>
      </div>
    </div>
  </section>

  <footer>
    <span>Chitti is free and open source. Use it at your own risk; you pay for your own API use. <a href="#disclaimer">Disclaimer</a></span>
    <span>Created by <a href="https://chaitanyaeshwarprasad.com" target="_blank" rel="noopener" style="text-decoration:none"><b>Chaitanya Eshwar Prasad</b></a></span>
  </footer>
</div>


<div class="wiz-back" id="wizBack" role="dialog" aria-modal="true" aria-labelledby="wizTitle">
  <div class="wiz">
    <div class="wiz-top"><div class="wiz-dots" id="wizDots"><span></span><span></span><span></span><span></span></div><span class="mut small" id="wizCount">Step 1 of 4</span><button class="x" onclick="closeWizard()">Close setup</button></div>

    <div class="wiz-step" data-step="1">
      <h2 id="wizTitle">Welcome! Let's set up Chitti 👋</h2>
      <p class="mut small" style="margin-bottom:8px">Made by <b>Chaitanya Eshwar Prasad</b>. Stuck? Watch the setup video on <a href="https://www.youtube.com/@chaitanya.eshwar.prasad" target="_blank" rel="noopener">YouTube</a>.</p>
      <p class="lead">Chitti uses <b>your own</b> AI keys, so you pay only for what you use (usually a few rupees a day). Keys stay on this PC.</p>
      <label>Claude (Anthropic) key: required</label>
      <div class="inrow"><input class="in" type="password" id="wApiKey" placeholder="sk-ant-…" autocomplete="off"><button class="pill" onclick="wizSaveKey()">Save</button></div>
      <div class="mut small" style="margin-top:6px">Get it at <a href="https://console.anthropic.com" target="_blank">console.anthropic.com</a>: add credits, set a monthly limit, then API Keys → Create Key.</div>
      <div id="wKeyAlert" class="alert"></div>
      <label>Sarvam key: optional, for a natural Telugu voice</label>
      <div class="inrow"><input class="in" type="password" id="wSarvamKey" placeholder="Sarvam API key" autocomplete="off"><button class="pill" onclick="wizSaveSarvam()">Save</button></div>
      <div class="mut small" style="margin-top:6px">Get it at <a href="https://dashboard.sarvam.ai" target="_blank">dashboard.sarvam.ai</a>. Skip it to use a free voice.</div>
      <div id="wSarvamAlert" class="alert"></div>
      <label class="check" style="margin-top:16px"><input type="checkbox" id="wAgree" onchange="wizRefresh()"> <span>I understand Chitti is free and provided as is, and <b>I use it at my own risk</b>: the author is not responsible for my device, my API bills or anything else. <a href="#disclaimer" onclick="closeWizard()">Read the disclaimer</a></span></label>
      <div class="wiz-nav"><span class="sp"></span><button class="pill mint" id="wNext1" onclick="wizGo(2)" disabled>Next</button></div>
    </div>

    <div class="wiz-step" data-step="2">
      <h2>Install Chitti Junior on the board</h2>
      <p class="lead">Plug the ESP32-S3 N16R8 board into this PC with a USB-C <b>data</b> cable. The port marked <b>COM</b> installs most easily. Close Arduino IDE or serial monitors if they're open.
        <br><span class="small">Don't have the board yet? <a href="#hardware" onclick="closeWizard()">See what to buy</a>.</span></p>
      <div class="usb"><span class="dot" id="wUsbDot"></span><span id="wUsbText">Looking for the board…</span></div>
      <label class="check"><input type="checkbox" id="wErase" checked> <span><b>Erase the board first</b>: recommended the first time</span></label>
      <label class="check"><input type="checkbox" id="wBackup"> <span><b>Save a backup</b> of the board's original software first (adds about 3 minutes)</span></label>
      <div class="bar"><div id="wBar"></div></div>
      <div class="mut small" id="wFlashStage" style="margin-top:8px">Takes about 1–2 minutes. Don't unplug the board while it installs.</div>
      <div id="wFlashAlert" class="alert"></div>
      <div class="wiz-nav"><button class="linkbtn" onclick="wizGo(1)">Back</button><span class="sp"></span><button class="linkbtn" onclick="wizGo(3)">Already installed? Skip</button><button class="pill mint" id="wFlashBtn" onclick="wizFlash()">Install Chitti Junior</button></div>
    </div>

    <div class="wiz-step" data-step="3">
      <h2>Connect Chitti Junior to Wi-Fi</h2>
      <p class="lead">Use the <b>same Wi-Fi as this PC</b> (or the same router, if the PC uses a LAN cable). It must be 2.4 GHz: if your router shows two names, use the one without "5G". No guest Wi-Fi. The password goes to the board through the USB cable only. <b>Use the ESP32's port marked USB</b> for this step.</p>
      <label>Wi-Fi name</label>
      <div class="inrow"><input class="in" id="wSsid" placeholder="Wi-Fi name"><button class="pill" onclick="wizScan()">Scan</button></div>
      <select class="in" id="wScan" style="display:none;margin-top:6px" onchange="if(this.value) $('wSsid').value=this.value"></select>
      <label>Password</label>
      <input class="in" type="password" id="wPass" placeholder="leave empty if open">
      <div id="wWifiAlert" class="alert"></div>
      <div class="wiz-nav"><button class="linkbtn" onclick="wizGo(2)">Back</button><span class="sp"></span><button class="linkbtn" onclick="wizGo(4)">Skip</button><button class="pill mint" id="wWifiBtn" onclick="wizWifi()">Send to Chitti</button></div>
    </div>

    <div class="wiz-step" data-step="4">
      <h2 id="wDoneTitle">Waiting for Chitti…</h2>
      <div class="bigok" id="wDoneIcon">⏳</div>
      <p class="lead" id="wDoneText">She's starting up and looking for this PC on your Wi-Fi. This takes up to 30 seconds.</p>
      <ul class="small" id="wDoneTips" style="margin-left:18px;color:var(--mut)">
        <li>When Windows asks about Python and the firewall, tick <b>Private networks</b> and click <b>Allow</b>.</li>
        <li>Check that Chitti and this PC are on the same Wi-Fi (not a guest network).</li>
        <li>Still waiting after a minute? Go back and send the Wi-Fi again.</li>
      </ul>
      <div class="wiz-nav"><button class="linkbtn" onclick="wizGo(3)">Back</button><span class="sp"></span><button class="pill mint" onclick="finishWizard()" id="wFinish">Finish</button></div>
    </div>
  </div>
</div>

<div class="riskbar" role="note">⚠️ Buy the hardware and use Chitti <b>at your own risk</b>.<span class="long"> The creator is not responsible for your device, API bills or anything else.</span> <a href="#disclaimer">Disclaimer</a></div>
<script>
  const _chittiFetch = window.fetch.bind(window);
  window.fetch = (url, opts = {}) => { opts.headers = Object.assign({ "X-Chitti-Dashboard": "1" }, opts.headers || {}); return _chittiFetch(url, opts); };
  // any button pressed while the server is closed: say so plainly instead of failing silently
  addEventListener("unhandledrejection", () => { const b = document.getElementById("bannerText"); if (b) { b.textContent = "Can't reach Chitti's brain. Is the black \"Start Chitti Junior\" window open?"; document.getElementById("banner").className = "banner"; } });
  const SLANGS = __SLANGS__;
  const PERSONAS = __PERSONAS__;
  const $ = id => document.getElementById(id);
  const esc = t => String(t ?? "").replace(/[&<>"']/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const post = (url, body) => fetch(url, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body || {}) }).then(r => r.json());
  function showAlert(id, msg, ok) { const el = $(id); el.textContent = msg; el.className = "alert " + (ok ? "good" : "bad"); clearTimeout(el._t); el._t = setTimeout(() => el.className = "alert", 6000); }
  function applyTheme(t) { document.documentElement.dataset.theme = t; }
  function toggleTheme() { const t = document.documentElement.dataset.theme === "dark" ? "light" : "dark"; applyTheme(t); try { localStorage.setItem("chittiTheme", t); } catch (e) {} }
  try { applyTheme(localStorage.getItem("chittiTheme") || "light"); } catch (e) { applyTheme("light"); }

  // tabs: a click keeps its highlight until you scroll yourself
  const navLinks = [...document.querySelectorAll("#nav a")];
  const targets = navLinks.map(a => document.querySelector(a.getAttribute("href")));
  const setNav = id => navLinks.forEach(a => a.classList.toggle("on", a.getAttribute("href") === "#" + id));
  let pinned = false;
  navLinks.forEach(a => a.addEventListener("click", () => { pinned = true; setNav(a.getAttribute("href").slice(1)); }));
  for (const ev of ["wheel", "touchmove", "keydown"]) addEventListener(ev, () => pinned = false, { passive: true });
  addEventListener("scroll", () => {
    if (pinned) return;
    let best = targets[0];
    for (const el of targets) if (el.getBoundingClientRect().top <= 140) best = el;
    if (innerHeight + scrollY >= document.body.scrollHeight - 4) best = targets[targets.length - 1];
    setNav(best.id);
  }, { passive: true });

  // personality + style
  let curPersona = null, curSlang = null;
  function renderPersonas(a) { curPersona = a; $("personaChips").innerHTML = PERSONAS.map(p => `<button class="chip-btn ${p.key === a ? "on" : ""}" title="${esc(p.description)}" onclick="pickPersona('${p.key}')">${esc(p.short)}</button>`).join(""); }
  function renderSlangs(a) { curSlang = a; $("slangChips").innerHTML = SLANGS.map(s => `<button class="chip-btn ${s.key === a ? "on" : ""}" title="${esc(s.description)}" onclick="pickSlang('${s.key}')">${esc(s.short)}</button>`).join(""); }
  async function pickPersona(k) { renderPersonas(k); const d = await post("/api/save_personality", { personality: k }); showAlert("voiceSlangAlert", d.ok ? "Personality changed." : "Could not change", d.ok); }
  async function pickSlang(k) { renderSlangs(k); const d = await post("/api/save_slang", { slang: k }); showAlert("voiceSlangAlert", d.ok ? "Telugu style changed." : "Could not change", d.ok); }
  async function onVoiceChange() { const d = await post("/api/save_voice", { voice: $("voiceSelect").value }); showAlert("voiceSlangAlert", d.ok ? "Voice changed." : (d.error || "Could not change"), d.ok); if (!d.ok) poll(); }
  async function testVoice() { showAlert("voiceSlangAlert", "Playing a test on Chitti…", true); await post("/api/test_voice", { voice: $("voiceSelect").value }); }
  async function saveWords() { const d = await post("/api/save_words", { use_words: $("useWords").value, avoid_words: $("avoidWords").value }); showAlert("voiceSlangAlert", d.ok ? "Words saved." : "Could not save", d.ok); }
  async function saveCity() { await post("/api/save_city", { city: $("cityInput").value }); showAlert("cityAlert", "City saved.", true); }
  async function saveModel() { const d = await post("/api/save_model", { model: $("modelSelect").value }); showAlert("keyAlert", d.ok ? "Brain switched." : (d.error || "Could not switch"), d.ok); }

  // keys
  async function saveApiKey() {
    const key = $("apiKeyInput").value.trim(); if (!key) return;
    if (!key.startsWith("sk-ant-")) { showAlert("keyAlert", "Claude keys start with sk-ant-", false); return; }
    showAlert("keyAlert", "Checking the key with Anthropic…", true);
    const d = await post("/api/save_key", { api_key: key });
    showAlert("keyAlert", d.ok ? (d.replaced ? "New Claude key saved. The old key was deleted from this PC." : "Claude key saved on this PC.") + (d.note ? " " + d.note : "") : (d.error || "Could not save"), d.ok);
    if (d.ok) { $("apiKeyInput").value = ""; poll(); }
  }
  async function saveSarvamKey() {
    const key = $("sarvamKeyInput").value.trim(); if (!key) return;
    const d = await post("/api/save_sarvam_key", { api_key: key });
    showAlert("sarvamAlert", d.ok ? (d.replaced ? "New Sarvam key saved. The old key was deleted from this PC." : "Sarvam key saved on this PC.") : (d.error || "Could not save"), d.ok);
    if (d.ok) { $("sarvamKeyInput").value = ""; poll(); }
  }

  async function removeKey(which) {
    const label = which === "claude" ? "Claude" : "Sarvam";
    if (!confirm("Delete your " + label + " key from this PC?" + (which === "claude" ? "\nChitti can't reply until you add a key again." : "\nChitti switches to the free voice."))) return;
    const d = await post("/api/remove_key", { which });
    showAlert(which === "claude" ? "keyAlert" : "sarvamAlert", d.ok ? label + " key deleted from this PC." : (d.error || "Could not delete"), d.ok);
    poll();
  }

  // device
  async function setVolume(v) { const d = await post("/api/set_volume", { value: parseInt(v) }); if (!d.ok) showAlert("volAlert", d.error || "Could not change volume", false); }
  async function scanWifi() {
    const sel = $("wifiScanSelect"); sel.style.display = "block"; sel.innerHTML = "<option>Scanning…</option>";
    try {
      const nets = await (await fetch("/api/wifi_scan")).json();
      if (nets[0] === "__LOCATION_OFF__") { sel.innerHTML = "<option value=''>Can't scan: Windows Location services is off. Type the name.</option>"; return; }
      sel.innerHTML = nets.length ? "<option value=''>Pick a network…</option>" + nets.map(n => `<option>${esc(n)}</option>`).join("") : "<option value=''>None found, type it in</option>";
    } catch (e) { sel.innerHTML = "<option>Scan failed</option>"; }
  }
  async function sendWifiConfig() {
    const ssid = $("wifiSsidInput").value.trim(); if (!ssid) { showAlert("wifiAlert", "Type the Wi-Fi name first", false); return; }
    const b = $("btnSendWifi"); b.disabled = true; b.textContent = "Sending…";
    try { const d = await post("/api/save_wifi", { ssid, password: $("wifiPassInput").value, port: $("comPortSelect").value }); showAlert("wifiAlert", d.message, d.ok); }
    catch (e) { showAlert("wifiAlert", "Failed: " + e, false); }
    b.disabled = false; b.textContent = "Send to Chitti";
  }
  async function loadPorts() {
    try {
      const [ports, board] = await Promise.all([fetch("/api/com_ports").then(r => r.json()), fetch("/api/board_usb").then(r => r.json())]);
      const sel = $("comPortSelect"), keep = sel.value;
      sel.innerHTML = `<option value="auto">Find it automatically${board.port ? " (" + esc(board.port) + ")" : ""}</option>` +
        ports.map(x => `<option value="${esc(x)}">${esc(x)}${x === board.port ? " - Chitti board" : ""}</option>`).join("");
      if ([...sel.options].some(o => o.value === keep)) sel.value = keep;
      $("portHint").textContent = board.port ? "Chitti's board found on " + board.port + "." : "Board not found. Plug it in with a USB-C data cable.";
    } catch (e) {}
  }
  async function resetChitti() {
    const typed = prompt("This erases everything Chitti remembers about you and can't be undone.\nType RESET to confirm:");
    if (typed === null) return;
    if (typed.trim().toUpperCase() !== "RESET") { showAlert("resetAlert", "Not reset: you need to type RESET.", false); return; }
    const d = await post("/api/reset_chitti", { confirm: "RESET" }); showAlert("resetAlert", d.ok ? "Done. Chitti is brand new." : "Reset failed", d.ok); lastFeed = ""; poll();
  }

  // chat
  async function clearMemory() { if (!confirm("Clear the chat? Chitti forgets this conversation.")) return; await post("/api/clear_memory"); lastFeed = ""; poll(); }
  function quickAsk(t) { $("askInput").value = t; sendTestChat(); }
  async function sendTestChat() { const t = $("askInput").value.trim(); if (!t) return; $("askInput").value = ""; await post("/api/test_chat", { text: t }); setTimeout(poll, 600); }

  // robot follows the real Chitti
  function setRobot(act) { $("stage").dataset.act = act; }
  document.addEventListener("mousemove", e => {
    const r = document.querySelector(".robot").getBoundingClientRect();
    const dx = (e.clientX - (r.left + r.width / 2)) / innerWidth, dy = (e.clientY - (r.top + r.height * .38)) / innerHeight;
    $("robotEyes").style.transform = `translate(${Math.max(-1, Math.min(1, dx * 2.2)) * 9}px, ${Math.max(-1, Math.min(1, dy * 2.2)) * 6}px)`;
  });

  let lastFeed = "", loaded = false, wizChecked = false;
  function step(id, state, text) { const el = $(id); el.className = "ico " + state; el.textContent = state === "ok" ? "✓" : state === "no" ? "!" : "–"; $(id + "Text").textContent = text; }
  async function poll() {
    let s;
    try { s = await (await fetch("/api/status")).json(); }
    catch (e) { $("bannerText").textContent = "Chitti's brain isn't running. Double-click \"Start Chitti Junior.bat\"."; $("banner").className = "banner"; return; }
    const on = s.board_connected;
    wizStatus = s; if ($("wizBack").classList.contains("open")) wizRefresh();
    if (!wizChecked) { wizChecked = true; if (!s.setup_done) openWizard(s.api_key_set ? 2 : 1); }
    $("boardDot").className = "dot " + (on ? "ok" : ""); $("boardStatusText").textContent = on ? "Junior online" : "Junior offline";
    $("heroDot").className = "dot " + (on ? "ok" : "");
    $("statActivity").textContent = on ? (s.board_activity || "idle").replace(/^./, c => c.toUpperCase()) : "Offline";
    $("heroBatt").textContent = s.board_battery >= 0 ? s.board_battery + "%" + (s.board_charging ? " ⚡" : "") : "–";
    $("modelText").textContent = (s.claude_model || "").replace("claude-", "");
    $("statIp").textContent = on ? s.board_ip : "not connected";
    $("devState").textContent = on ? "Online" : "Offline"; $("devState").style.color = on ? "var(--mint-deep)" : "var(--amber)";
    $("devBatt").textContent = s.board_battery >= 0 ? s.board_battery + "%" + (s.board_charging ? " (charging)" : "") : "–";
    setRobot(on ? (s.board_activity || "idle") : "idle");
    if (s.last_turn && s.last_turn.reply) { $("heroSaid").textContent = s.last_turn.reply; $("heroSub").textContent = s.last_turn.subtitle || ""; }
    step("stepKey", s.api_key_set ? "ok" : "no", s.api_key_set ? "Saved: " + s.api_key_masked : "Add your key in Settings (required).");
    step("stepSarvam", s.sarvam_key_set ? "ok" : "opt", s.sarvam_key_set ? "Saved: natural Telugu voice is on." : "Not added: using the free voice.");
    step("stepBoard", on ? "ok" : "no", on ? "Online at " + s.board_ip : "Not connected yet. See Help.");
    $("keyCurrentStatus").textContent = s.api_key_set ? "● Saved: " + s.api_key_masked : "Not set yet";
    $("sarvamCurrentStatus").textContent = s.sarvam_key_set ? "● Saved: " + s.sarvam_key_masked : "Not set (free voice in use)";
    $("rmClaude").style.display = s.api_key_set ? "" : "none"; $("rmSarvam").style.display = s.sarvam_key_set ? "" : "none";
    const b = $("banner");
    if (s.ffmpeg_ok === false) { b.className = "banner"; $("bannerText").textContent = "ffmpeg is missing, so Chitti can't speak. Open a new window, run: winget install --id Gyan.FFmpeg -e, then restart Chitti."; }
    else if (!s.api_key_set) { b.className = "banner"; $("bannerText").textContent = "Step 1: add your Claude API key in Settings."; }
    else if (!on) { b.className = "banner"; $("bannerText").textContent = "Waiting for Chitti. Switch her on, on the same Wi-Fi as this PC."; }
    else { b.className = "banner ok"; $("bannerText").textContent = "All set. Press Junior's BOOT button and talk!"; }
    if (document.activeElement !== $("modelSelect")) $("modelSelect").value = s.selected_model || s.claude_model;
    const sg = $("sarvamVoices");
    sg.disabled = !s.sarvam_key_set;
    sg.label = s.sarvam_key_set ? "Sarvam voices" : "Sarvam voices: add a Sarvam key to use";
    $("engineInfo").innerHTML = "In use: voice <b>" + (s.sarvam_key_set ? "Sarvam" : "free Microsoft") + "</b> · hearing <b>" +
      (s.sarvam_key_set ? "Sarvam" : "Whisper on this PC") + "</b> · brain <b>Claude " + esc((s.claude_model || "").replace("claude-", "").replace(/-(\d)-(\d)$/, " $1.$2").replace(/^./, c => c.toUpperCase())) + "</b>";
    if (document.activeElement !== $("voiceSelect") && s.selected_voice) $("voiceSelect").value = s.selected_voice;
    if (s.selected_personality !== curPersona) renderPersonas(s.selected_personality);
    if (s.selected_slang !== curSlang) renderSlangs(s.selected_slang);
    if (s.board_volume >= 0 && document.activeElement !== $("volSlider")) { $("volSlider").value = s.board_volume; $("volVal").textContent = s.board_volume + "%"; }
    if (!loaded) { $("useWords").value = s.use_words || ""; $("avoidWords").value = s.avoid_words || ""; $("cityInput").value = s.city || ""; loaded = true; }
    const feed = s.feed || [], sig = feed.length + ":" + (feed.length ? feed[feed.length - 1].reply : "");
    if (sig !== lastFeed) {
      lastFeed = sig;
      $("feedContainer").innerHTML = feed.length ? feed.map(it => (it.user ? `<div class="msg me">${esc(it.user)}</div>` : "") +
        (it.reply ? `<div class="msg bot">${esc(it.reply)}${it.subtitle ? `<div class="s">${esc(it.subtitle)}</div>` : ""}</div>` : "")).join("")
        : `<div class="empty"><span class="big">💬</span>No messages yet. Press Junior's BOOT button, or type below.</div>`;
      $("feedContainer").scrollTop = 1e9;
    }
  }

  // ---------------- first-run setup wizard
  let wizStep = 1, wizTimer = null, wizStatus = {};
  function openWizard(step) { $("wizBack").classList.add("open"); wizGo(step || 1); }
  function closeWizard() { $("wizBack").classList.remove("open"); clearInterval(wizTimer); wizTimer = null; }
  async function finishWizard() { try { await post("/api/setup_done", { done: true, agreed: $("wAgree").checked }); } catch (e) {} closeWizard(); }
  function wizGo(n) {
    wizStep = n;
    document.querySelectorAll(".wiz-step").forEach(el => el.classList.toggle("on", +el.dataset.step === n));
    [...$("wizDots").children].forEach((d, i) => d.classList.toggle("on", i < n));
    $("wizCount").textContent = "Step " + n + " of 4";
    clearInterval(wizTimer); wizTimer = null;
    if (n === 2) { wizUsb(); wizTimer = setInterval(wizUsb, 2000); }
    wizRefresh();
  }
  function wizRefresh() {
    const s = wizStatus;
    const agreed = $("wAgree").checked;
    $("wNext1").disabled = !s.api_key_set || !agreed;
    $("wNext1").textContent = !s.api_key_set ? "Save your Claude key first" : (agreed ? "Next" : "Tick the box above first");
    const on = !!s.board_connected;
    $("wDoneTitle").textContent = on ? "Chitti is online! 🎉" : "Waiting for Chitti…";
    $("wDoneIcon").textContent = on ? "🤖" : "⏳";
    $("wDoneText").innerHTML = on ? "All set. <b>Press BOOT and say \"హాయ్ చిట్టి!\"</b> You can change her voice and personality anytime in Settings." :
      "She's starting up and looking for this PC on your Wi-Fi. This takes up to 30 seconds.";
    $("wDoneTips").style.display = on ? "none" : "block";
    $("wFinish").textContent = on ? "Start talking" : "Finish anyway";
  }
  async function wizSaveKey() {
    const key = $("wApiKey").value.trim(); if (!key) return;
    if (!key.startsWith("sk-ant-")) { showAlert("wKeyAlert", "That isn't a Claude key. It starts with sk-ant-", false); return; }
    showAlert("wKeyAlert", "Checking the key with Anthropic…", true);
    const d = await post("/api/save_key", { api_key: key });
    showAlert("wKeyAlert", d.ok ? "Saved on this PC ✓" + (d.replaced ? " (old key deleted)" : "") : (d.error || "Could not save"), d.ok);
    if (d.ok) { $("wApiKey").value = ""; wizStatus.api_key_set = true; wizRefresh(); poll(); }
  }
  async function wizSaveSarvam() {
    const key = $("wSarvamKey").value.trim(); if (!key) return;
    const d = await post("/api/save_sarvam_key", { api_key: key });
    showAlert("wSarvamAlert", d.ok ? "Saved on this PC ✓" : (d.error || "Could not save"), d.ok);
    if (d.ok) { $("wSarvamKey").value = ""; poll(); }
  }
  let wizUsbPort = null;
  async function wizUsb() {
    try { const b = await (await fetch("/api/board_usb")).json(); wizUsbPort = b.flash_port || b.port; } catch (e) { wizUsbPort = null; }
    $("wUsbDot").className = "dot " + (wizUsbPort ? "ok" : "");
    $("wUsbText").textContent = wizUsbPort ? "Board found on " + wizUsbPort + " ✓" : "Board not found. Plug it in with a USB-C data cable…";
  }
  async function wizFlash() {
    const btn = $("wFlashBtn");
    const d = await post("/api/flash_start", { erase: $("wErase").checked, backup: $("wBackup").checked, port: wizUsbPort || "" });
    if (!d.ok) { showAlert("wFlashAlert", d.message, false); return; }
    btn.disabled = true; btn.textContent = "Installing…";
    const t = setInterval(async () => {
      let f; try { f = await (await fetch("/api/flash_status")).json(); } catch (e) { return; }
      $("wBar").style.width = (f.pct || 0) + "%";
      $("wFlashStage").textContent = (f.stage || "") + (f.running ? "  " + Math.round(f.pct || 0) + "%" : "");
      if (!f.running) {
        clearInterval(t); btn.disabled = false;
        if (f.ok) { btn.textContent = "Next"; btn.onclick = () => wizGo(3); $("wErase").checked = false; showAlert("wFlashAlert", f.message, true); }
        else { btn.textContent = "Try again"; $("wFlashAlert").textContent = f.message; $("wFlashAlert").className = "alert bad"; }
      }
    }, 700);
  }
  async function wizScan() {
    const sel = $("wScan"); sel.style.display = "block"; sel.innerHTML = "<option>Scanning…</option>";
    try {
      const nets = await (await fetch("/api/wifi_scan")).json();
      if (nets[0] === "__LOCATION_OFF__") { sel.innerHTML = "<option value=''>Can't scan: Windows Location services is off. Type the name.</option>"; return; }
      sel.innerHTML = nets.length ? "<option value=''>Pick your network…</option>" + nets.map(n => `<option>${esc(n)}</option>`).join("") : "<option value=''>None found, type it in</option>";
    } catch (e) { sel.innerHTML = "<option>Scan failed</option>"; }
  }
  async function wizWifi() {
    const ssid = $("wSsid").value.trim(); if (!ssid) { showAlert("wWifiAlert", "Type the Wi-Fi name first", false); return; }
    let port = null, other = null;
    try { const b = await (await fetch("/api/board_usb")).json(); port = b.port; other = b.flash_port; } catch (e) {}
    if (!port) { showAlert("wWifiAlert", other ? "Move the cable to the ESP32's port marked USB (not COM), wait 5 s, then try again." : "Board not found. Plug it in with the USB-C cable.", false); return; }
    const b = $("wWifiBtn"); b.disabled = true; b.textContent = "Sending…";
    try {
      const d = await post("/api/save_wifi", { ssid, password: $("wPass").value, port });
      showAlert("wWifiAlert", d.message, d.ok);
      if (d.ok) setTimeout(() => wizGo(4), 1500);
    } catch (e) { showAlert("wWifiAlert", "Failed: " + e, false); }
    b.disabled = false; b.textContent = "Send to Chitti";
  }
  loadPorts(); poll(); setInterval(poll, 1500); setInterval(loadPorts, 4000);
</script>
</body>
</html>
"""


class DashboardHandler(BaseHTTPRequestHandler):
    def log_message(self, format, *args):
        return

    def _allowed(self, needs_dashboard_header: bool) -> bool:
        """Reject requests that don't come from this PC's own dashboard page."""
        host = (self.headers.get("Host") or "").lower()
        if host not in ALLOWED_HOSTS:  # blocks DNS-rebinding tricks
            self.send_error(HTTPStatus.FORBIDDEN)
            return False
        origin = (self.headers.get("Origin") or "").lower()
        if origin and origin not in {"http://" + h for h in ALLOWED_HOSTS}:
            self.send_error(HTTPStatus.FORBIDDEN)
            return False
        # Other websites can't add this header without the browser asking first (and we never allow it)
        if needs_dashboard_header and self.headers.get("X-Chitti-Dashboard") != "1":
            self.send_error(HTTPStatus.FORBIDDEN)
            return False
        return True

    def do_GET(self):
        if not self._allowed(needs_dashboard_header=False):
            return
        url = urlparse(self.path)
        if url.path == "/" or url.path == "/index.html":
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            slangs = [{"key": k, "short": v.get("short", k), "badge": v.get("badge", ""), "description": v.get("description", "")}
                      for k, v in SLANG_PRESETS.items()]
            personas = [{"key": k, "short": v["short"], "description": v["description"]} for k, v in PERSONALITIES.items()]
            page = (DASHBOARD_HTML.replace("__SLANGS__", json.dumps(slangs, ensure_ascii=False).replace("</", "<\\/"))
                    .replace("__PERSONAS__", json.dumps(personas, ensure_ascii=False).replace("</", "<\\/")))
            self.wfile.write(page.encode("utf-8"))
        elif url.path == "/api/status":
            sync_voice_with_keys()
            with STATE_LOCK:
                data = json.dumps(STATE)
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(data.encode("utf-8"))
        elif url.path == "/api/wifi_scan":
            nets = scan_wifi_networks()
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps(nets).encode("utf-8"))
        elif url.path == "/api/flash_status":
            self.send_json(flash_status())
        elif url.path == "/api/board_usb":
            self.send_json({"port": find_board_port(), "flash_port": find_flash_port()})
        elif url.path == "/api/com_ports":
            ports = list_com_ports()
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps(ports).encode("utf-8"))
        elif url.path == "/api/voice_samples":
            idx = HERE / "voice_samples" / "index.json"
            self.send_json(json.loads(idx.read_text(encoding="utf-8")) if idx.exists() else {"samples": []})
        elif url.path == "/api/voice_sample":
            name = (parse_qs(url.query).get("f") or [""])[0]
            path = (HERE / "voice_samples" / name).resolve()
            if not name.endswith(".wav") or path.parent != (HERE / "voice_samples").resolve() or not path.exists():
                self.send_error(HTTPStatus.NOT_FOUND)
                return
            data = path.read_bytes()
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", "audio/wav")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
        elif url.path == "/api/get_persona":
            persona_path = ROOT / "persona.md"
            text = persona_path.read_text(encoding="utf-8") if persona_path.exists() else ""
            self.send_json({"prompt": text})
        elif url.path == "/api/debug_audio":
            audio_path = HERE / "debug" / "last_input.wav"
            if audio_path.exists():
                data = audio_path.read_bytes()
                self.send_response(HTTPStatus.OK)
                self.send_header("Content-Type", "audio/wav")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)
            else:
                self.send_error(HTTPStatus.NOT_FOUND, "No audio recorded yet")
        else:
            self.send_error(HTTPStatus.NOT_FOUND)

    def do_POST(self):
        if not self._allowed(needs_dashboard_header=True):
            return
        url = urlparse(self.path)
        length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(length) if length > 0 else b"{}"
        try:
            payload = json.loads(body.decode("utf-8"))
        except Exception:
            payload = {}

        if url.path == "/api/save_key":
            key = payload.get("api_key", "").strip()
            if not key or not key.startswith("sk-ant-") or not _valid_key_text(key):
                self.send_json({"ok": False, "error": "Invalid API key format (must start with sk-ant-)"})
                return
            works, note = check_claude_key(key)
            if not works:
                self.send_json({"ok": False, "error": note})
                return
            had_old = bool(os.environ.get("ANTHROPIC_API_KEY"))
            ok = save_api_key_disk(key)
            if ok:
                update_state(api_key_set=True, api_key_masked=mask_key(key), brain_mode="claude")
                if CALLBACKS["update_key"]:
                    CALLBACKS["update_key"](key)
                self.send_json({"ok": True, "masked": mask_key(key), "replaced": had_old, "note": note})
            else:
                self.send_json({"ok": False, "error": "Failed to persist API key"})
        elif url.path == "/api/save_sarvam_key":
            key = payload.get("api_key", "").strip()
            if not key or not _valid_key_text(key):
                self.send_json({"ok": False, "error": "Sarvam API key looks invalid"})
                return
            had_old = bool(os.environ.get("SARVAM_API_KEY"))
            ok = save_sarvam_key_disk(key)
            if ok:
                update_state(sarvam_key_set=True, sarvam_key_masked=mask_key(key), tts_engine="sarvam", stt_engine="sarvam")
                if CALLBACKS.get("update_sarvam_key"):
                    CALLBACKS["update_sarvam_key"](key)
                self.send_json({"ok": True, "masked": mask_key(key), "replaced": had_old})
            else:
                self.send_json({"ok": False, "error": "Failed to persist Sarvam key"})
        elif url.path == "/api/remove_key":
            which = payload.get("which")
            name = {"claude": "ANTHROPIC_API_KEY", "sarvam": "SARVAM_API_KEY"}.get(which)
            if not name:
                self.send_json({"ok": False, "error": "Unknown key"})
                return
            _store_key(name, None)
            if which == "claude":
                update_state(api_key_set=False, api_key_masked="", brain_mode="echo")
            else:
                update_state(sarvam_key_set=False, sarvam_key_masked="", tts_engine="edge-tts", stt_engine="whisper")
            if CALLBACKS.get("remove_key"):
                CALLBACKS["remove_key"](which)
            self.send_json({"ok": True})
        elif url.path == "/api/save_voice":
            voice = str(payload.get("voice", "")).strip()
            if not voice.startswith(("sarvam:", "sarvam3:", "edge:")):
                self.send_json({"ok": False, "error": "Unknown voice"})
                return
            if voice.startswith("sarvam") and not os.environ.get("SARVAM_API_KEY"):
                self.send_json({"ok": False, "error": "Add a Sarvam key first to use this voice."})
                return
            save_settings({"voice": voice, "voice_auto": bool(payload.get("auto", False))})
            update_state(selected_voice=voice)
            if CALLBACKS.get("update_voice"):
                CALLBACKS["update_voice"](voice)
            self.send_json({"ok": True, "voice": voice})
        elif url.path == "/api/save_personality":
            personality = payload.get("personality", "")
            if personality not in PERSONALITIES:
                self.send_json({"ok": False, "error": "Unknown personality"})
                return
            save_settings({"personality": personality})
            update_state(selected_personality=personality)
            self.send_json({"ok": True})
        elif url.path == "/api/save_slang":
            slang = payload.get("slang", "telgish")
            save_settings({"slang": slang})
            update_state(selected_slang=slang)
            if CALLBACKS.get("update_slang"):
                CALLBACKS["update_slang"](slang)
            self.send_json({"ok": True, "slang": slang})
        elif url.path == "/api/save_custom_persona":
            prompt = payload.get("prompt", "").strip()
            if prompt:
                (ROOT / "persona.md").write_text(prompt, encoding="utf-8")
                save_settings({"slang": "custom"})
                update_state(selected_slang="custom")
                if CALLBACKS.get("update_slang"):
                    CALLBACKS["update_slang"]("custom")
                self.send_json({"ok": True})
            else:
                self.send_json({"ok": False, "error": "Prompt cannot be empty"})
        elif url.path == "/api/save_credits":
            try:
                credits_val = float(payload.get("starting_credits", 1000.0))
                save_settings({"sarvam_starting_credits": credits_val})
                with STATE_LOCK:
                    STATE["sarvam_starting_credits"] = credits_val
                    STATE["sarvam_remaining_credits"] = max(0.0, credits_val - STATE["sarvam_total_cost_inr"])
                self.send_json({"ok": True, "starting_credits": credits_val})
            except Exception as e:
                self.send_json({"ok": False, "error": str(e)})
        elif url.path == "/api/test_voice":
            voice = payload.get("voice", STATE["selected_voice"])
            if CALLBACKS.get("test_voice"):
                CALLBACKS["test_voice"](voice)
            self.send_json({"ok": True})
        elif url.path == "/api/save_wifi":
            ssid = payload.get("ssid", "").strip()
            password = payload.get("password", "")
            port = str(payload.get("port", "auto")).strip()
            if not ssid:
                self.send_json({"ok": False, "message": "SSID cannot be empty"})
                return
            ok, msg = send_wifi_to_board(ssid, password, port)
            self.send_json({"ok": ok, "message": msg})
        elif url.path == "/api/clear_memory":
            if CALLBACKS.get("clear_memory"):
                CALLBACKS["clear_memory"]()
            with STATE_LOCK:
                STATE["feed"] = []
                STATE["total_turns"] = 0
            self.send_json({"ok": True})
        elif url.path == "/api/save_model":
            model = payload.get("model", "")
            if model not in MODELS:
                self.send_json({"ok": False, "error": "Unknown model"})
                return
            save_settings({"model": model})
            update_state(selected_model=model, claude_model=model)
            if CALLBACKS.get("update_model"):
                CALLBACKS["update_model"](model)
            self.send_json({"ok": True})
        elif url.path == "/api/save_balance":
            service = payload.get("service")
            try:
                amount = float(payload.get("amount"))
            except (TypeError, ValueError):
                amount = -1
            if service not in ("claude", "sarvam") or not (0 <= amount < 1_000_000):
                self.send_json({"ok": False, "error": "Enter a number"})
                return
            wallet_set_balance(service, amount)
            self.send_json({"ok": True})
        elif url.path == "/api/reset_chitti":
            if payload.get("confirm") != "RESET":
                self.send_json({"ok": False, "error": "Not confirmed"})
                return
            if CALLBACKS.get("reset_chitti"):
                CALLBACKS["reset_chitti"]()
            with STATE_LOCK:
                STATE["feed"] = []
                STATE["total_turns"] = 0
            self.send_json({"ok": True})
        elif url.path == "/api/set_volume":
            try:
                percent = max(0, min(100, int(payload.get("value"))))
            except (TypeError, ValueError):
                self.send_json({"ok": False, "error": "Enter a number"})
                return
            if not STATE["board_connected"] or not CALLBACKS.get("set_volume"):
                self.send_json({"ok": False, "error": "Chitti isn't connected"})
                return
            CALLBACKS["set_volume"](percent)
            self.send_json({"ok": True})
        elif url.path == "/api/save_words":
            use = str(payload.get("use_words", "")).strip()[:300]
            avoid = str(payload.get("avoid_words", "")).strip()[:300]
            save_settings({"use_words": use, "avoid_words": avoid})
            update_state(use_words=use, avoid_words=avoid)
            self.send_json({"ok": True})
        elif url.path == "/api/save_city":
            city = str(payload.get("city", "")).strip()[:60]
            save_settings({"city": city})
            update_state(city=city)
            self.send_json({"ok": True})
        elif url.path == "/api/lists":
            action = payload.get("action")
            if action in ("delete_fact", "clear_facts", "cancel_reminder") and CALLBACKS.get("lists"):
                CALLBACKS["lists"](action, payload.get("value"))
            self.send_json({"ok": True})
        elif url.path == "/api/flash_start":
            ok, msg = start_flash(str(payload.get("port", "")).strip(), bool(payload.get("erase", True)),
                                  bool(payload.get("backup", False)))
            self.send_json({"ok": ok, "message": msg})
        elif url.path == "/api/setup_done":
            done = bool(payload.get("done", True))
            save_settings({"setup_done": done, "disclaimer_agreed": bool(payload.get("agreed", False))})
            update_state(setup_done=done)
            self.send_json({"ok": True})
        elif url.path == "/api/test_chat":
            text = payload.get("text", "").strip()
            if text and CALLBACKS.get("test_chat"):
                CALLBACKS["test_chat"](text)
            self.send_json({"ok": True})
        else:
            self.send_error(HTTPStatus.NOT_FOUND)

    def send_json(self, obj):
        data = json.dumps(obj).encode("utf-8")
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(data)


ALLOWED_HOSTS: set[str] = set()


def start_dashboard_server(port: int = 8768) -> ThreadingHTTPServer:
    ALLOWED_HOSTS.update({f"localhost:{port}", f"127.0.0.1:{port}"})
    # 127.0.0.1 = reachable only from this PC (not from other devices on the Wi-Fi/hotspot)
    server = ThreadingHTTPServer(("127.0.0.1", port), DashboardHandler)
    t = threading.Thread(target=server.serve_forever, daemon=True)
    t.start()
    log.info("Chitti Web Dashboard running at http://localhost:%d", port)
    return server


_wallet_publish(_wallet_load())
