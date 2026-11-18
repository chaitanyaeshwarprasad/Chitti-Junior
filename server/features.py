# Part of Chitti by Chaitanya Eshwar Prasad (https://chaitanyaeshwarprasad.com). MIT licence.
"""Chitti extras: remembered facts, reminders, voice-emotion hints, catchphrase tracking,
weather and night mode. Kept separate from chitti_server.py so each piece stays simple."""
import json
import logging
import re
import threading
import time
import datetime as dt
import urllib.parse
import urllib.request
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
FACTS_FILE = HERE / "facts.json"
REMINDERS_FILE = HERE / "reminders.json"
BASELINE_FILE = HERE / "voice_baseline.json"
MAX_FACTS = 40
log = logging.getLogger("chitti.features")
_lock = threading.Lock()


def _read(path: Path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default


def _write(path: Path, data) -> None:
    path.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")


# ---------------------------------------------------------------- remembered facts
def load_facts() -> list[str]:
    return _read(FACTS_FILE, [])


_FILLER = {"he", "she", "is", "a", "an", "the", "his", "her", "says", "very", "much", "lot", "and", "to", "of", "be", "wants", "likes"}


def _key_words(text: str) -> set[str]:
    return {w for w in re.findall(r"\w+", text.lower()) if w not in _FILLER}


def add_fact(fact: str) -> bool:
    fact = fact.strip().rstrip(".")[:200]
    if len(fact) < 3:
        return False
    with _lock:
        facts = load_facts()
        words = _key_words(fact)
        for f in facts:  # skip near-duplicates ("Loves biryani" vs "He loves biryani a lot")
            other = _key_words(f)
            if f.lower() == fact.lower() or (words and other and len(words & other) / max(len(words), len(other)) >= 0.75):
                return False
        facts.append(fact)
        _write(FACTS_FILE, facts[-MAX_FACTS:])
    log.info("remembered: %s", fact)
    return True


def delete_fact(index: int) -> None:
    with _lock:
        facts = load_facts()
        if 0 <= index < len(facts):
            facts.pop(index)
            _write(FACTS_FILE, facts)


def clear_facts() -> None:
    with _lock:
        _write(FACTS_FILE, [])


def facts_block() -> str:
    facts = load_facts()
    if not facts:
        return ""
    return ("\n## What you remember about your friend (use naturally, don't list them; preferences here always win)\n"
            + "\n".join(f"- {f}" for f in facts))


# ---------------------------------------------------------------- reminders
def load_reminders() -> list[dict]:
    return _read(REMINDERS_FILE, [])


def add_reminder(minutes: float, say: str, sub: str) -> dict | None:
    if not say or minutes <= 0 or minutes > 60 * 24 * 7:
        return None
    r = {"id": int(time.time() * 1000), "due": time.time() + minutes * 60, "say": say.strip(), "sub": sub.strip()}
    with _lock:
        items = load_reminders()
        items.append(r)
        _write(REMINDERS_FILE, items)
    log.info("reminder in %.0f min: %s", minutes, say)
    return r


def cancel_reminder(rid: int) -> None:
    with _lock:
        _write(REMINDERS_FILE, [r for r in load_reminders() if r.get("id") != rid])


def clear_reminders() -> None:
    with _lock:
        _write(REMINDERS_FILE, [])


def pop_due_reminders() -> list[dict]:
    """Reminders whose time has come (ones more than 6 h late are dropped)."""
    now = time.time()
    with _lock:
        items = load_reminders()
        due = [r for r in items if r["due"] <= now]
        if due:
            _write(REMINDERS_FILE, [r for r in items if r["due"] > now])
    return [r for r in due if now - r["due"] < 6 * 3600]


def reminders_for_dashboard() -> list[dict]:
    return [{"id": r["id"], "when": dt.datetime.fromtimestamp(r["due"]).strftime("%a %I:%M %p"), "sub": r.get("sub") or r["say"]}
            for r in sorted(load_reminders(), key=lambda r: r["due"])]


# hidden command lines Claude adds at the end of a reply
COMMAND = re.compile(r"^\s*#\s*(REMIND|FACT)\b\s*(.*)$", re.IGNORECASE)


def parse_command(line: str):
    m = COMMAND.match(line)
    if not m:
        return None
    kind, rest = m.group(1).upper(), m.group(2).strip()
    if kind == "FACT":
        return ("fact", rest)
    parts = [p.strip() for p in rest.split("|")]
    try:
        minutes = float(re.sub(r"[^\d.]", "", parts[0]) or 0)
    except ValueError:
        return None
    return ("remind", (minutes, parts[1] if len(parts) > 1 else "", parts[2] if len(parts) > 2 else ""))


# ---------------------------------------------------------------- how the voice sounded
class VoiceBaseline:
    """Learns how loud and how fast this person usually talks, then describes each turn against it."""

    def __init__(self):
        b = _read(BASELINE_FILE, {})
        self.loud = b.get("loud")   # dB of voiced frames
        self.rate = b.get("rate")   # spoken characters per voiced second
        self.n = b.get("n", 0)

    def save(self):
        _write(BASELINE_FILE, {"loud": self.loud, "rate": self.rate, "n": self.n})

    def describe(self, pcm: bytes, sr: int, text: str) -> str:
        x = np.frombuffer(pcm, dtype=np.int16).astype(np.float32)
        frame = max(1, sr // 50)  # 20 ms
        n = x.size // frame
        if n < 15 or not text:
            return ""
        rms = np.sqrt(np.mean(x[: n * frame].reshape(n, frame) ** 2, axis=1)) + 1e-6
        db = 20 * np.log10(rms)
        floor = np.percentile(db, 20)
        voiced = db > floor + 10
        voiced_sec = voiced.sum() * 0.02
        if voiced_sec < 0.6:
            return ""
        loud = float(np.percentile(db[voiced], 75))
        chars = len(re.sub(r"[\s\W]", "", text))
        rate = chars / voiced_sec
        # variation in loudness: bursts (laughing, shouting) vs flat (tired, low)
        spread = float(np.std(db[voiced]))
        words = []
        if self.n >= 3:
            if loud > self.loud + 5:
                words.append("louder than usual")
            elif loud < self.loud - 6:
                words.append("quieter than usual")
            if rate > self.rate * 1.3:
                words.append("talking fast")
            elif rate < self.rate * 0.7:
                words.append("talking slowly")
            if spread > 9:
                words.append("very up-and-down (could be laughing or upset)")
        # learn slowly, so one shouting match doesn't become "normal"
        k = 0.15 if self.n >= 3 else 0.5
        self.loud = loud if self.loud is None else self.loud * (1 - k) + loud * k
        self.rate = rate if self.rate is None else self.rate * (1 - k) + rate * k
        self.n += 1
        self.save()
        return ", ".join(words)


# laughter written into the transcript ("హహహ", "haha") is a strong hint too
LAUGH = re.compile(r"(హ\s*){3,}|(ha){2,}|(హా){2,}|😂|lol", re.IGNORECASE)


# ---------------------------------------------------------------- catchphrases
CATCHPHRASES = ["మచ్చా", "డ్యూడ్", "బ్రో", "బాస్", "బావా", "ఉస్తాద్", "రేయ్", "అబ్బో", "పెద్ద తోపు", "లైట్ తీస్కో", "చిల్",
                "కిరాక్", "బిందాస్", "సీన్", "బాబూ", "అన్నా", "నాయనా", "బావ్", "వైబ్", "లిట్", "క్రింజ్"]


def recent_openers(history: list[dict], turns: int = 3) -> list[str]:
    """First couple of words of Chitti's last few replies, so she can start the next one differently."""
    out = []
    for m in [m for m in history if m.get("role") == "assistant" and isinstance(m.get("content"), str)][-turns:]:
        first = re.sub(r"^\s*\[[^\]]+\]\s*", "", m["content"].strip().splitlines()[0]).split("|")[0]
        words = re.sub(r"[!?.,]", " ", first).split()[:2]
        if words:
            out.append(" ".join(words))
    return out


def recent_catchphrases(history: list[dict], turns: int = 2) -> list[str]:
    replies = [m["content"] for m in history if m.get("role") == "assistant" and isinstance(m.get("content"), str)][-turns:]
    text = " ".join(replies)
    return [w for w in CATCHPHRASES if w in text]


# ---------------------------------------------------------------- weather (open-meteo.com, free, no key)
_weather_cache: dict = {}
WMO = {0: "clear sky", 1: "mostly clear", 2: "partly cloudy", 3: "cloudy", 45: "foggy", 48: "foggy", 51: "light drizzle",
       53: "drizzle", 55: "heavy drizzle", 61: "light rain", 63: "rain", 65: "heavy rain", 80: "rain showers",
       81: "rain showers", 82: "heavy showers", 95: "thunderstorm", 96: "thunderstorm with hail", 99: "thunderstorm with hail"}


def _get_json(url: str) -> dict:
    req = urllib.request.Request(url, headers={"User-Agent": "chitti-companion"})
    with urllib.request.urlopen(req, timeout=4) as r:
        return json.loads(r.read().decode())


def weather_text(city: str) -> str:
    """'Hyderabad: 27°C, light rain, today 24-31°C, rain chance 60%' or '' if unavailable (cached 30 min)."""
    city = (city or "").strip()
    if not city:
        return ""
    hit = _weather_cache.get(city.lower())
    if hit and time.time() - hit[0] < 1800:
        return hit[1]
    try:
        g = _get_json("https://geocoding-api.open-meteo.com/v1/search?count=1&name=" + urllib.parse.quote(city))
        if not g.get("results"):
            return ""
        p = g["results"][0]
        w = _get_json(
            f"https://api.open-meteo.com/v1/forecast?latitude={p['latitude']}&longitude={p['longitude']}"
            "&current=temperature_2m,weather_code&daily=temperature_2m_max,temperature_2m_min,precipitation_probability_max"
            "&forecast_days=1&timezone=auto")
        cur, d = w["current"], w["daily"]
        text = (f"{p['name']}: {cur['temperature_2m']:.0f}°C, {WMO.get(cur['weather_code'], 'mixed')}, "
                f"today {d['temperature_2m_min'][0]:.0f}-{d['temperature_2m_max'][0]:.0f}°C, "
                f"rain chance {d['precipitation_probability_max'][0]}%")
        _weather_cache[city.lower()] = (time.time(), text)
        return text
    except Exception as e:
        log.info("weather unavailable: %s", e)
        return ""


WEATHER_WORDS = re.compile(r"weather|rain|వాతావరణం|వర్షం|వాన|ఎండ|చలి|temperature|టెంపరేచర్|గొడుగు", re.IGNORECASE)
# things that need fresh facts from the internet
LIVE_WORDS = re.compile(r"score|match|cricket|news|price|రేట్|ధర|స్కోర్|మ్యాచ్|క్రికెట్|వార్త|న్యూస్|ఐపీఎల్|ipl|ఎన్నిక|election|"
                        r"stock|షేర్|gold|బంగారం|release|రిలీజ్|trailer|ట్రైలర్", re.IGNORECASE)


# ---------------------------------------------------------------- night
def is_night(now: dt.datetime | None = None) -> bool:
    h = (now or dt.datetime.now()).hour
    return h >= 23 or h < 6
