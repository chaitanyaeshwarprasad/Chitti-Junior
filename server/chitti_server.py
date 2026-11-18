# Chitti - Telugu AI companion. Created by Chaitanya Eshwar Prasad (https://chaitanyaeshwarprasad.com). MIT licence.
"""Chitti PC server: speech in (Telugu/English) -> Claude -> female voice out.

Board connects over WebSocket (see ../docs/protocol.md).
Run:  python chitti_server.py
Needs ANTHROPIC_API_KEY in the environment (or an `ant auth login` profile).
"""
import asyncio
import base64
import datetime as dt
import io
import json
import logging
import os
import re
import subprocess
import time
import urllib.request
import wave
from pathlib import Path

import aiohttp
import anthropic
import edge_tts
import numpy as np
import websockets
from websockets.asyncio.server import serve

HERE = Path(__file__).resolve().parent

# Use IPv4 only. On this network IPv6 to some services (e.g. api.anthropic.com) sometimes hangs,
# which made Chitti go silent while a request waited for a timeout.
import socket

_getaddrinfo = socket.getaddrinfo


def _ipv4_getaddrinfo(host, port, family=0, *args, **kwargs):
    return _getaddrinfo(host, port, socket.AF_INET if family in (0, socket.AF_UNSPEC) else family, *args, **kwargs)


socket.getaddrinfo = _ipv4_getaddrinfo

# CUDA libraries from the nvidia-* pip packages (Windows needs their folders on the DLL path).
try:
    import nvidia  # type: ignore

    for base in nvidia.__path__:
        for bin_dir in Path(base).glob("*/bin"):
            os.add_dll_directory(str(bin_dir))
            os.environ["PATH"] = str(bin_dir) + os.pathsep + os.environ["PATH"]
except ImportError:
    pass

import dashboard
import features

# Load local .env file if available (takes precedence)
_env_path = HERE / ".env"
if _env_path.exists():
    for _line in _env_path.read_text(encoding="utf-8").splitlines():
        _line = _line.strip()
        if _line.startswith("ANTHROPIC_API_KEY="):
            _val = _line.split("=", 1)[1].strip().strip('"').strip("'")
            if _val:
                os.environ["ANTHROPIC_API_KEY"] = _val
        elif _line.startswith("SARVAM_API_KEY="):
            _val = _line.split("=", 1)[1].strip().strip('"').strip("'")
            if _val:
                os.environ["SARVAM_API_KEY"] = _val

ROOT = HERE.parent
HOST = os.environ.get("CHITTI_HOST", "0.0.0.0")
PORT = int(os.environ.get("CHITTI_PORT", "8765"))
def _has_cuda() -> bool:
    try:
        import ctranslate2
        return ctranslate2.get_cuda_device_count() > 0
    except Exception:
        return False


# Local speech recognition (used when there's no Sarvam key or no internet). NVIDIA GPU if there is one.
WHISPER_DEVICE = os.environ.get("CHITTI_WHISPER_DEVICE") or ("cuda" if _has_cuda() else "cpu")
WHISPER_MODEL = os.environ.get("CHITTI_WHISPER") or ("large-v3-turbo" if WHISPER_DEVICE == "cuda" else "small")
# Brain: Claude Sonnet 5.5 by default (accurate, funny); Haiku 5.5 in the dashboard is ~15x cheaper.
CLAUDE_MODEL = os.environ.get("CHITTI_MODEL") or dashboard.load_settings().get("model") or "claude-haiku-5-5"
if CLAUDE_MODEL not in ("claude-haiku-5-5", "claude-sonnet-5-5"):  # only current models (older ones get retired)
    CLAUDE_MODEL = "claude-haiku-5-5"
VOICES = {"te": "te-IN-ShrutiNeural", "en": "en-IN-NeerjaNeural", "hi": "hi-IN-SwaraNeural"}
SARVAM_SPEAKER = os.environ.get("CHITTI_SARVAM_SPEAKER", "pooja_te_conversation")
# Broadcast loudness gain + limiter: raises volume by ~6 dB so Chitti is loud and punchy on the board speaker without clipping
# Clean voice for the board's small speaker: soften hissy "s/sh" highs, gentle compression, soft limiter.
# Measured on a raw Sarvam sentence: old fixed +7 dB boost = 132 harsh sample jumps (crackle), loudnorm = 63,
# this chain = 10 (raw voice = 2). Loudness comes from the board volume (now 90%) instead of squashing the audio.
AUDIO_BOOST_FILTER = ("highshelf=f=7000:g=-4,acompressor=threshold=0.18:ratio=2.5:attack=15:release=200:makeup=1.6,"
                      "alimiter=limit=0.9:attack=15:release=150:level=disabled")
EDGE_BOOST_FILTER = AUDIO_BOOST_FILTER  # same clean chain for the free fallback voice
MOODS = {"happy", "excited", "calm", "thinking", "surprised", "sad", "sleepy", "playful", "angry", "laughing", "dancing", "driving"}
TELUGU_MOOD_MAP = {
    "సంతోషం": "happy", "సంతోషంగా": "happy", "హ్యాపీ": "happy",
    "బాధ": "sad", "బాధగా": "sad", "చింతగా": "sad",
    "ఆశ్చర్యం": "surprised", "ఆశ్చర్యంగా": "surprised",
    "ఆలోచన": "thinking", "ఆలోచిస్తూ": "thinking",
    "కోపం": "angry", "కోపంగా": "angry",
    "నవ్వు": "laughing", "నవ్వుతూ": "laughing",
    "ఉత్సాహం": "excited", "ఉత్సాహంగా": "excited",
    "ప్రశాంతం": "calm", "ప్రశాంతంగా": "calm",
    "సరదా": "playful", "సరదాగా": "playful",
    "నిద్ర": "sleepy", "నిద్రగా": "sleepy",
    "డాన్స్": "dancing", "డ్రైవింగ్": "driving",
}
# Claude prices, USD per million tokens: (input, output, cache read). Cache writes cost 1.25x input.
CLAUDE_PRICES = {
    "claude-haiku-5-5": (0.10, 0.50, 0.01),
    "claude-sonnet-5-5": (2.00, 10.00, 0.10),
    "claude-opus-5-5": (4.00, 20.00, 0.20),
}
USD_TO_INR = 86.50
# Sarvam pay-as-you-go prices (docs.sarvam.ai/api/pricing, checked Nov 2026)
SARVAM_STT_INR_PER_SEC = 0.49 / 60     # speech to text: Rs 0.49 per minute, billed per second
SARVAM_TTS_INR_PER_CHAR = 10.0 / 10000  # bulbul:v4-flash: Rs 10 per 10,000 characters
SARVAM_V3_INR_PER_CHAR = 30.0 / 10000   # bulbul:v3 (more expressive voices): Rs 30 per 10,000 characters
SARVAM_V3_TEMPERATURE = 0.75            # Sarvam's advice for warm, conversational personas: 0.7-0.8


def claude_cost_usd(model: str, usage) -> float:
    """Cost of one reply from the API's own token counts."""
    price = next((v for k, v in CLAUDE_PRICES.items() if model.startswith(k)), CLAUDE_PRICES["claude-sonnet-5-5"])
    p_in, p_out, p_read = price
    cache_read = getattr(usage, "cache_read_input_tokens", 0) or 0
    cache_write = getattr(usage, "cache_creation_input_tokens", 0) or 0
    searches = getattr(getattr(usage, "server_tool_use", None), "web_search_requests", 0) or 0
    return ((usage.input_tokens or 0) * p_in + cache_write * p_in * 1.25 + cache_read * p_read
            + (usage.output_tokens or 0) * p_out) / 1_000_000 + searches * 0.01  # web search: $10 per 1000


SR = 16000       # Whisper's rate
OUT_SR = 24000   # speech to the board (the voice is made at 24 kHz; no quality loss)
MAX_TURNS = 15  # remembered exchanges: enough context, faster and cheaper replies
HISTORY_FILE = HERE / "memory.json"

log = logging.getLogger("chitti")


from slang_presets import DEFAULT_PERSONALITY, PERSONALITIES, build_prompt, custom_prompt, style_reminder, words_rule

# How speech-to-text mishears "Chitti" through the board's mic (seen in real recordings, e.g. "హాయ్ జిటి").
# None of these is a normal Telugu word, so they're corrected anywhere in the sentence.
NAME_MISHEARS = {"సిటీ", "సిటి", "సిట్టి", "సిట్టీ", "చిట్టీ", "చిటి", "చిటీ", "జిటి", "జిటీ", "జిట్టి", "జిట్టీ",
                 "చిత్తి", "షిట్టి", "ఛిట్టి", "జిట్", "city", "chitty", "chiti", "jitti", "giti", "chetti"}


# "సిటీ" is also the English word "city" (సిటీ బస్సు): only treat it as her name when she's being called.
AMBIGUOUS_NAMES = {"సిటీ", "సిటి", "city"}
GREETINGS = {"హాయ్", "హలో", "ఓకే", "ఏయ్", "ఒరేయ్", "అరేయ్", "రేయ్", "థాంక్యూ", "hi", "hello", "ok", "hey"}


def fix_name(text: str) -> str:
    words = text.split()
    for i, w in enumerate(words):
        core = w.strip(",.!?;:")
        name = core.lower()
        if name not in NAME_MISHEARS:
            continue
        if name in AMBIGUOUS_NAMES:
            prev = words[i - 1].strip(",.!?").lower() if i else ""
            calling = i == 0 or w != core or prev in GREETINGS  # first word, followed by , . ? !, or after "హాయ్"
            if not calling:
                continue
        words[i] = w.replace(core, "చిట్టి")
    return " ".join(words)


def persona_choice() -> tuple[str, str]:
    """(dialect, personality) from the settings; older saved choices are mapped to the new ones."""
    st = dashboard.load_settings()
    slang, personality = st.get("slang", "telgish"), st.get("personality", DEFAULT_PERSONALITY)
    if slang == "sarcastic":  # old "Funny & Sarcastic" style = Telgish + Roast master
        slang, personality = "telgish", "roaster"
    elif slang == "caring":
        slang, personality = "telgish", "caring"
    if personality not in PERSONALITIES:
        personality = DEFAULT_PERSONALITY
    return slang, personality


def load_persona() -> str:
    """Shared rules + personality + dialect + what Chitti remembers about you."""
    slang, personality = persona_choice()
    prompt = build_prompt(slang, personality)
    if slang == "custom":
        try:
            text = (ROOT / "persona.md").read_text(encoding="utf-8")
            prompt = custom_prompt(text.split("\n---\n", 1)[-1], personality)
        except OSError:
            pass
    st = dashboard.load_settings()
    return prompt.strip() + "\n" + words_rule(st.get("use_words", ""), st.get("avoid_words", "")) + features.facts_block()


SYSTEM_PROMPT = load_persona()

# ---------------------------------------------------------------- speech to text
_whisper = None


def whisper():
    global _whisper, WHISPER_DEVICE, WHISPER_MODEL
    if _whisper is None:
        from faster_whisper import WhisperModel

        compute = "int8_float16" if WHISPER_DEVICE == "cuda" else "int8"
        log.info("loading Whisper %s on %s (%s)", WHISPER_MODEL, WHISPER_DEVICE, compute)
        model = WhisperModel(WHISPER_MODEL, device=WHISPER_DEVICE, compute_type=compute)
        if WHISPER_DEVICE == "cuda":
            try:  # missing NVIDIA libraries only show up on first use, so test once
                list(model.transcribe(np.zeros(SR // 2, dtype=np.float32), language="te")[0])
            except RuntimeError as e:
                log.warning("Whisper can't use the graphics card (%s); using the CPU instead", e)
                WHISPER_DEVICE = "cpu"
                WHISPER_MODEL = os.environ.get("CHITTI_WHISPER") or "small"
                log.info("loading Whisper %s on cpu (int8)", WHISPER_MODEL)
                model = WhisperModel(WHISPER_MODEL, device="cpu", compute_type="int8")
        _whisper = model
    return _whisper


def resample_to_16k(pcm: bytes, rate: int) -> bytes:
    if rate == SR:
        return pcm
    return subprocess.run(["ffmpeg", "-loglevel", "error", "-f", "s16le", "-ar", str(rate), "-ac", "1", "-i", "pipe:0",
                           "-f", "s16le", "-ar", str(SR), "-ac", "1", "pipe:1"], input=pcm, capture_output=True).stdout


# Common spoken-Telugu words and Chitti's name: steers Whisper toward the right spellings.
# Common spoken-Telugu words and Chitti's name: steers Whisper toward the right spellings.
STT_HINT = "చిట్టి, ఏంట్రా, ఒరేయ్, అరేయ్, నువ్వు, నువ్వేం చేస్తున్నావ్, ఏం చేస్తున్నావ్, ఏం చేస్తున్నావు, ఎలా ఉన్నావ్, ఎలా ఉన్నావు, ఏంటి సంగతి, తిన్నావా, విశేషాలు చెప్పు, బోర్ కొడుతోంది, నాకు బోర్ కొడుతోంది, నువ్వు ఎవరు, బాగున్నావా, ఏం విశేషాలు, కాసేపు మాట్లాడు, హాయ్."


def clean_audio(audio: np.ndarray, rate: int) -> np.ndarray:
    """Remove DC offset, normalize volume safely without distortion/clipping."""
    audio = audio - audio.mean()
    loud = np.percentile(np.abs(audio), 99.0) if audio.size else 0
    if loud > 1e-4:
        # Boost safely up to 6x max, targeting 0.65 peak so it never clips at 1.0
        audio = audio * min(6.0, 0.65 / max(loud, 0.05))
    return np.clip(audio, -1.0, 1.0)


def transcribe_sarvam(pcm: bytes, rate: int = SR, key: str | None = None) -> tuple[str, str]:
    """Call Sarvam AI Speech-to-Text (saaras:v4) for near-instant (0.27s), 100% accurate Telugu/Tanglish speech recognition."""
    k = key or os.environ.get("SARVAM_API_KEY")
    if not k:
        return "", ""
    k = k.strip().strip('"').strip("'")

    bio = io.BytesIO()
    with wave.open(bio, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(pcm)
    wav_bytes = bio.getvalue()

    boundary = "----WebKitFormBoundary7MA4YWxkTrZu0gW"
    body = bytearray()
    body.extend(f"--{boundary}\r\n".encode())
    body.extend(b'Content-Disposition: form-data; name="file"; filename="audio.wav"\r\nContent-Type: audio/wav\r\n\r\n')
    body.extend(wav_bytes)
    body.extend(b"\r\n")

    body.extend(f"--{boundary}\r\n".encode())
    body.extend(b'Content-Disposition: form-data; name="model"\r\n\r\nsaaras:v4\r\n')

    body.extend(f"--{boundary}\r\n".encode())
    body.extend(b'Content-Disposition: form-data; name="language_code"\r\n\r\nte-IN\r\n')

    body.extend(f"--{boundary}--\r\n".encode())

    req = urllib.request.Request(
        "https://api.sarvam.ai/speech-to-text",
        data=bytes(body),
        headers={
            "api-subscription-key": k,
            "Content-Type": f"multipart/form-data; boundary={boundary}"
        }
    )
    with urllib.request.urlopen(req, timeout=5.0) as resp:
        res = json.loads(resp.read().decode())
        transcript = res.get("transcript", "").strip()
        lang = res.get("language_code", "te-IN")
        l = "te" if lang.startswith("te") else ("hi" if lang.startswith("hi") else "en")
        return transcript, l


def transcribe(pcm: bytes, rate: int = SR) -> tuple[str, str]:
    """Return (text, language) using Sarvam saaras:v4 if SARVAM_API_KEY is present, else Whisper."""
    if os.environ.get("SARVAM_API_KEY"):
        try:
            text, lang = transcribe_sarvam(pcm, rate)
            stt_sec = len(pcm) / (rate * 2) if rate else 2.0
            stt_cost_inr = stt_sec * SARVAM_STT_INR_PER_SEC
            dashboard.record_sarvam_stt(stt_sec, stt_cost_inr)
            if text:
                log.info("Sarvam STT (saaras:v4) heard: %s (lang=%s)", text, lang)
                return text, lang
            elif text == "":
                # Pure silence or background tap/click correctly detected by Sarvam
                log.info("Sarvam STT: silence/noise ignored")
                return "", "te"
        except Exception as e:
            if getattr(e, "code", None) == 402:  # out of credits
                dashboard.wallet_mark_empty("sarvam")
            log.warning("Sarvam STT failed (%s), falling back to local Whisper", e)

    pcm = resample_to_16k(pcm, rate)
    audio = clean_audio(np.frombuffer(pcm, dtype=np.int16).astype(np.float32) / 32768.0, SR)
    model = whisper()
    seconds = len(audio) / SR
    segments, _ = model.transcribe(
        audio,
        language="te",
        beam_size=2,
        vad_filter=False,
        condition_on_previous_text=False,
        initial_prompt=STT_HINT,
        without_timestamps=True,
        temperature=0.0,
        repetition_penalty=1.15,
        no_repeat_ngram_size=3,
        max_new_tokens=int(25 + seconds * 14),
    )
    text = " ".join(s.text.strip() for s in segments).strip()
    lang = "te"
    return text, lang


# ---------------------------------------------------------------- text to speech
TELUGU = re.compile(r"[ఀ-౿]")
DEVANAGARI = re.compile(r"[ऀ-ॿ]")


def voice_for(text: str) -> str:
    if TELUGU.search(text):
        return VOICES["te"]
    if DEVANAGARI.search(text):
        return VOICES["hi"]
    return VOICES["en"]


async def synthesize_sarvam(text: str, key: str | None = None, speaker: str | None = None) -> bytes:
    k = key or os.environ.get("SARVAM_API_KEY")
    if not k:
        return b""
    k = k.strip().strip('"').strip("'")
    url = "https://api.sarvam.ai/text-to-speech"
    headers = {
        "api-subscription-key": k,
        "Content-Type": "application/json"
    }
    lang = "te-IN" if TELUGU.search(text) else ("hi-IN" if DEVANAGARI.search(text) else "en-IN")
    # speaker = the dashboard setting: "sarvam:<v4-flash id>" or "sarvam3:<v3 name>"
    voice = speaker or dashboard.load_settings().get("voice", "sarvam:pooja_te_conversation")
    if ":" not in voice:
        voice = "sarvam:" + voice
    kind, spk = voice.split(":", 1)
    tts_chars = len(text)
    if kind == "sarvam3":  # bulbul:v3: expressive, and each speaker handles Telugu, English and Hindi
        payload = {"text": text, "target_language_code": lang, "speaker": spk, "model": "bulbul:v3",
                   "pace": 1.0, "temperature": SARVAM_V3_TEMPERATURE, "speech_sample_rate": OUT_SR}
        tts_cost_inr = tts_chars * SARVAM_V3_INR_PER_CHAR
    else:
        if kind != "sarvam":
            spk = SARVAM_SPEAKER
        if lang != "te-IN":
            spk = "ishita_enhi_companion" if lang == "en-IN" else "roopa_hi_conversational"
        payload = {"inputs": [text], "target_language_code": lang, "speaker": spk, "model": "bulbul:v4-flash",
                   "pace": 1.02, "enable_preprocessing": True}
        tts_cost_inr = tts_chars * SARVAM_TTS_INR_PER_CHAR
    timeout = aiohttp.ClientTimeout(total=8.0)
    async with aiohttp.ClientSession(timeout=timeout) as session:
        async with session.post(url, json=payload, headers=headers) as resp:
            if resp.status != 200:
                err_text = await resp.text()
                if resp.status == 402:  # out of credits
                    dashboard.wallet_mark_empty("sarvam")
                raise RuntimeError(f"Sarvam API {resp.status}: {err_text[:200]}")
            dashboard.record_sarvam_tts(tts_chars, tts_cost_inr)  # only billed when it worked
            data = await resp.json()
            audios = data.get("audios", [])
            if not audios:
                raise RuntimeError("No audio returned from Sarvam")
            raw_audio = base64.b64decode(audios[0])
            proc = await asyncio.create_subprocess_exec(
                "ffmpeg", "-loglevel", "error", "-i", "pipe:0",
                "-filter:a", AUDIO_BOOST_FILTER,
                "-f", "s16le", "-ac", "1", "-ar", str(OUT_SR), "pipe:1",
                stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE)
            pcm, _ = await proc.communicate(raw_audio)
            return pcm


async def synthesize(text: str) -> bytes:
    """Synthesize speech using selected voice (Sarvam Bulbul or Edge-TTS)."""
    voice_setting = dashboard.load_settings().get("voice", "sarvam:pooja_te_conversation")
    if voice_setting.startswith("sarvam") and os.environ.get("SARVAM_API_KEY"):
        try:
            pcm = await synthesize_sarvam(text, speaker=voice_setting)
            if pcm:
                return pcm
        except Exception as e:
            log.warning("Sarvam Bulbul TTS failed (%s), using Edge-TTS", e)

    # Edge-TTS fallback
    edge_voice = voice_setting.split(":", 1)[1] if voice_setting.startswith("edge:") else voice_for(text)
    mp3 = bytearray()
    async for chunk in edge_tts.Communicate(text, edge_voice, rate="+2%", pitch="+2Hz").stream():
        if chunk["type"] == "audio":
            mp3 += chunk["data"]
    proc = await asyncio.create_subprocess_exec(
        "ffmpeg", "-loglevel", "error", "-i", "pipe:0",
        "-filter:a", EDGE_BOOST_FILTER,
        "-f", "s16le", "-ac", "1", "-ar", str(OUT_SR), "pipe:1",
        stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE)
    pcm, _ = await proc.communicate(bytes(mp3))
    return pcm


def start_tts(text: str) -> asyncio.Queue:
    """Start making speech for `text` now; PCM chunks arrive on the returned queue (None = done).
    Uses selected voice (Sarvam Bulbul or Edge-TTS)."""
    q: asyncio.Queue = asyncio.Queue()

    async def produce():
        voice_setting = dashboard.load_settings().get("voice", "sarvam:pooja_te_conversation")
        if voice_setting.startswith("sarvam") and os.environ.get("SARVAM_API_KEY"):
            try:
                pcm = await synthesize_sarvam(text, speaker=voice_setting)
                if pcm:
                    chunk_size = OUT_SR * 2 // 10  # ~100 ms pieces
                    for i in range(0, len(pcm), chunk_size):
                        q.put_nowait(pcm[i:i + chunk_size])
                    q.put_nowait(None)
                    return
            except Exception as e:
                log.warning("Sarvam Bulbul TTS failed (%s), falling back to Edge-TTS", e)

        # Edge-TTS fallback
        edge_voice = voice_setting.split(":", 1)[1] if voice_setting.startswith("edge:") else voice_for(text)
        proc = await asyncio.create_subprocess_exec(
            "ffmpeg", "-loglevel", "error", "-fflags", "nobuffer", "-probesize", "32", "-analyzeduration", "0",
            "-f", "mp3", "-i", "pipe:0",
            "-filter:a", EDGE_BOOST_FILTER,
            "-f", "s16le", "-ac", "1", "-ar", str(OUT_SR), "pipe:1",
            stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE)

        async def feed():
            try:
                async for chunk in edge_tts.Communicate(text, edge_voice, rate="+2%", pitch="+2Hz").stream():
                    if chunk["type"] == "audio":
                        proc.stdin.write(chunk["data"])
                        await proc.stdin.drain()
            except Exception as e:  # network hiccup: end this sentence quietly
                log.warning("voice failed for %r: %s", text[:30], e)
            finally:
                proc.stdin.close()

        feeder = asyncio.create_task(feed())
        try:
            while data := await proc.stdout.read(OUT_SR // 5):  # ~100 ms pieces
                q.put_nowait(data)
        finally:
            await feeder
            await proc.wait()
            q.put_nowait(None)

    asyncio.create_task(produce())
    return q


def save_debug_audio(pcm: bytes, rate: int) -> None:
    """Keep the board's last recording (debug/last_input.wav) and log how loud it was."""
    import wave
    x = np.frombuffer(pcm, dtype=np.int16)
    if x.size:
        peak = int(np.abs(x.astype(np.int32)).max())
        rms = float(np.sqrt(np.mean(x.astype(np.float32) ** 2)))
        clipped = float(np.mean(np.abs(x.astype(np.int32)) >= 32000)) * 100
        log.info("mic: %.1fs at %d Hz, peak %d, rms %.0f, clipped %.2f%%", x.size / rate, rate, peak, rms, clipped)
    d = HERE / "debug"
    d.mkdir(exist_ok=True)
    name = time.strftime("rec_%H%M%S.wav")
    for target in (d / "last_input.wav", d / name):
        with wave.open(str(target), "wb") as w:
            w.setnchannels(1); w.setsampwidth(2); w.setframerate(rate); w.writeframes(pcm)
    for old in sorted(d.glob("rec_*.wav"))[:-20]:  # keep the last 20 recordings
        old.unlink()
    if x.size > rate:
        head = float(np.sqrt(np.mean(x[: rate // 2].astype(np.float32) ** 2)))
        tail = float(np.sqrt(np.mean(x[rate // 2:].astype(np.float32) ** 2)))
        log.info("mic: first 0.5 s rms %.0f vs rest %.0f (%s)", head, tail, name)


# ---------------------------------------------------------------- brain
def make_anthropic_client(key: str | None = None) -> anthropic.AsyncAnthropic:
    k = key or os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_AUTH_TOKEN")
    if not k:
        return anthropic.AsyncAnthropic(timeout=30.0, max_retries=2)
    k = k.strip().strip('"').strip("'")
    return anthropic.AsyncAnthropic(api_key=k, timeout=30.0, max_retries=2)


client = make_anthropic_client()


async def detect_working_model():
    """Keep the chosen model if this account can use it, otherwise pick the best available fast one."""
    global CLAUDE_MODEL
    if BRAIN != "claude":
        return
    try:
        models = [m.id async for m in client.models.list()]
        if models and CLAUDE_MODEL not in models:
            pick = next((m for m in model_candidates() if m in models), CLAUDE_MODEL)
            log.info("Model %s not on this account; using %s", CLAUDE_MODEL, pick)
            CLAUDE_MODEL = pick
        dashboard.update_state(claude_model=CLAUDE_MODEL)
        log.info("Claude model: %s", CLAUDE_MODEL)
    except Exception as e:
        log.info("Model check skipped (%s), using: %s", e, CLAUDE_MODEL)


def load_history() -> list[dict]:
    try:
        return json.loads(HISTORY_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []


def save_history(history: list[dict]) -> None:
    HISTORY_FILE.write_text(json.dumps(history[-MAX_TURNS * 2:], ensure_ascii=False, indent=1), encoding="utf-8")


def split_subtitle(line: str) -> tuple[str, str]:
    """'spoken sentence | English subtitle' -> (spoken, subtitle)."""
    spoken, _, sub = line.partition("|")
    return spoken.strip(), sub.strip()


def has_claude_key() -> bool:
    return bool(os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_AUTH_TOKEN"))


def has_sarvam_key() -> bool:
    return bool(os.environ.get("SARVAM_API_KEY"))


# "claude" = real brain; "echo" = test mode that repeats what it heard (no API key needed).
BRAIN = os.environ.get("CHITTI_BRAIN") or ("claude" if has_claude_key() else "echo")

active_loop = None
active_session = None


def on_update_api_key(new_key: str):
    global client, BRAIN
    os.environ["ANTHROPIC_API_KEY"] = new_key
    client = make_anthropic_client(new_key)
    BRAIN = "claude"
    dashboard.update_state(api_key_set=True, api_key_masked=dashboard.mask_key(new_key), brain_mode="claude")
    if active_loop:
        asyncio.run_coroutine_threadsafe(detect_working_model(), active_loop)
    log.info("Anthropic API key dynamically activated: %s", dashboard.mask_key(new_key))


dashboard.CALLBACKS["update_key"] = on_update_api_key


def on_update_sarvam_key(new_key: str):
    os.environ["SARVAM_API_KEY"] = new_key
    dashboard.update_state(
        sarvam_key_set=True,
        sarvam_key_masked=dashboard.mask_key(new_key),
        tts_engine="sarvam",
        stt_engine="sarvam",
    )
    log.info("Sarvam AI key activated: %s (Bulbul TTS & Saaras STT online)", dashboard.mask_key(new_key))


dashboard.CALLBACKS["update_sarvam_key"] = on_update_sarvam_key


def on_remove_key(which: str):
    """A key was deleted from the dashboard: stop using it immediately."""
    global BRAIN
    if which == "claude":
        BRAIN = "echo"
        log.info("Claude key deleted; test mode until a new key is added")
    else:
        log.info("Sarvam key deleted; using the free voice and Whisper")


dashboard.CALLBACKS["remove_key"] = on_remove_key


def on_update_voice(voice: str):
    dashboard.update_state(selected_voice=voice)
    log.info("Voice dynamically switched to: %s", voice)


dashboard.CALLBACKS["update_voice"] = on_update_voice


def on_update_slang(slang: str):
    dashboard.update_state(selected_slang=slang)
    log.info("Slang style dynamically switched to: %s", slang)


dashboard.CALLBACKS["update_slang"] = on_update_slang


def on_update_model(model: str):
    global CLAUDE_MODEL
    CLAUDE_MODEL = model
    dashboard.update_state(claude_model=model)
    log.info("Claude model switched to: %s", model)


dashboard.CALLBACKS["update_model"] = on_update_model


def on_set_volume(percent: int):
    """Dashboard slider -> board speaker volume (the board saves it)."""
    if active_session and active_loop:
        asyncio.run_coroutine_threadsafe(active_session.send(type="volume", value=int(percent)), active_loop)
        dashboard.update_state(board_volume=int(percent))


dashboard.CALLBACKS["set_volume"] = on_set_volume


def on_lists_changed(action: str, value=None):
    if action == "delete_fact":
        features.delete_fact(int(value))
    elif action == "clear_facts":
        features.clear_facts()
    elif action == "cancel_reminder":
        features.cancel_reminder(int(value))
    sync_dashboard_lists()


dashboard.CALLBACKS["lists"] = on_lists_changed


async def play_voice_test(voice: str):
    if active_session:
        is_male = "tarun" in voice or "Mohan" in voice
        test_text = "హాయ్ మచ్చా! ఇది నా కొత్త వాయిస్ రా, ఎలా ఉంది బ్రో?" if is_male else "హాయ్ మచ్చా! ఇది నా కొత్త వాయిస్, బాగుందా?"
        sub = "Hi dude! This is my new voice!"
        await active_session.send(type="mood", mood="excited")
        await active_session.send(type="state", state="speaking")
        await active_session.send(type="say", text=test_text, en=sub)
        await active_session.send(type="tts", state="start")
        pcm = await synthesize(test_text)
        await active_session.stream_audio(pcm)
        await active_session.send(type="tts", state="stop")
        await active_session.send(type="state", state="idle")


def on_test_voice(voice: str):
    if active_loop:
        asyncio.run_coroutine_threadsafe(play_voice_test(voice), active_loop)


dashboard.CALLBACKS["test_voice"] = on_test_voice


def on_clear_memory():
    HISTORY_FILE.write_text("[]", encoding="utf-8")
    if active_session:
        active_session.history = []
        if active_loop:
            async def clear_board():
                try:
                    await active_session.send(type="say", text="", en="")
                    await active_session.send(type="mood", mood="happy")
                    await active_session.send(type="state", state="idle")
                except Exception:
                    pass
            asyncio.run_coroutine_threadsafe(clear_board(), active_loop)
    log.info("Conversation history cleared from dashboard and board screen reset.")


dashboard.CALLBACKS["clear_memory"] = on_clear_memory


def on_reset_chitti():
    """Brand-new Chitti: forget the conversation, the facts, reminders, how your voice sounds,
    and the saved microphone recordings. Keys, voice/style choices and the budget stay."""
    on_clear_memory()
    features.clear_facts()
    features.clear_reminders()
    for f in (features.BASELINE_FILE,):
        f.unlink(missing_ok=True)
    for f in [*(HERE / "debug").glob("*.wav"), *HERE.glob("memory_backup*.json")]:
        f.unlink(missing_ok=True)  # saved mic recordings and old copies of the conversation
    dashboard.save_settings({"last_greeting_day": ""})
    if active_session:
        active_session.baseline = features.VoiceBaseline()
    dashboard.update_state(last_turn=None)
    sync_dashboard_lists()
    log.info("Chitti reset: memory, facts, reminders, voice profile and recordings erased.")


dashboard.CALLBACKS["reset_chitti"] = on_reset_chitti


async def standalone_test_chat(text: str):
    dashboard.add_feed_entry({
        "user": text,
        "reply": "Thinking...",
        "subtitle": "Processing in Claude...",
        "mood": "thinking",
        "stt_sec": 0.0,
        "first_tok_sec": 0.0,
        "cost_inr": 0.0,
    })
    history = load_history()
    reply, sub, mood = "", "", "happy"
    cost_usd = 0.0
    t0 = time.monotonic()
    t_first = 0.0
    try:
        async for kind, val in think(history, text, "te", persist=True):
            if kind == "mood":
                mood = val
            elif kind == "sentence":
                if not t_first:
                    t_first = time.monotonic()
                reply += val[0] + " "
                sub = val[1]
            elif kind == "usage":
                cost_usd = claude_cost_usd(val[1], val[0])
            elif kind == "command":
                kind_, arg = val
                if kind_ == "fact":
                    features.add_fact(arg)
                else:
                    features.add_reminder(*arg)
                sync_dashboard_lists()
            elif kind == "done":
                reply = val

        cost_inr = cost_usd * USD_TO_INR
        dashboard.update_last_feed_or_add({
            "user": text,
            "reply": reply,
            "subtitle": sub,
            "mood": mood,
            "stt_sec": 0.0,
            "first_tok_sec": round(t_first - t0, 2) if t_first else 0.5,
            "cost_inr": round(cost_inr, 2),
            "cost_usd": round(cost_usd, 4),
        })
        dashboard.update_state(
            last_turn={"user": text, "reply": reply, "subtitle": sub, "mood": mood, "cost_inr": round(cost_inr, 2)},
            total_turns=dashboard.STATE["total_turns"] + 1,
        )
        dashboard.record_claude_usage(0, 0, 0, cost_usd, cost_inr)  # also counts against the Claude balance
    except Exception as e:
        log.error("Test chat error: %s", e)
        if isinstance(e, anthropic.APIStatusError) and ("credit" in str(e.message).lower() or "balance" in str(e.message).lower()):
            dashboard.wallet_mark_empty("claude")
        dashboard.update_last_feed_or_add({
            "user": text,
            "reply": f"Error: {e}",
            "subtitle": "Brain error",
            "mood": "sad",
        })


def on_test_chat(text: str):
    if active_loop:
        asyncio.run_coroutine_threadsafe(standalone_test_chat(text), active_loop)


dashboard.CALLBACKS["test_chat"] = on_test_chat


async def think_echo(user_text: str, lang: str):
    yield "mood", "happy"
    yield "sentence", (("నువ్వు అన్నావురా: " if lang == "te" else "You said: ") + user_text, "You said something")
    yield "done", user_text


# How hard Sonnet 5.5 thinks before speaking ("low" = fastest; "medium" = more careful answers)
SONNET_EFFORT = os.environ.get("CHITTI_EFFORT", "low")


def thinking_for(model: str) -> dict | None:
    """Thinking off for a quick spoken reply (each model has its own way to say that)."""
    if model.startswith("claude-haiku-5-5"):
        return {"type": "disabled"}
    if model.startswith("claude-sonnet-5-5"):
        # brief thinking at low effort: sharper jokes and answers for ~0.2 s more, same cost (measured)
        return {"type": "adaptive"}
    return None


def web_search_tool(model: str) -> dict:
    newer = model.startswith(("claude-sonnet-5", "claude-opus"))
    return {"type": "web_search_20260209" if newer else "web_search_20250305", "name": "web_search", "max_uses": 1}


def model_candidates() -> list[str]:
    out = []
    for m in [CLAUDE_MODEL, "claude-haiku-5-5", "claude-sonnet-5-5"]:  # current models only
        if m and m not in out:
            out.append(m)
    return out


async def think(history: list[dict], user_text: str, lang: str, persist: bool = True,
                voice_hint: str = "", note: bool = False):
    """Stream Claude's reply. Yields ("mood", m) once, ("sentence", (spoken, sub)) pieces,
    ("command", (kind, arg)) for hidden #REMIND/#FACT lines, ("usage", ...) and ("done", reply)."""
    global CLAUDE_MODEL
    if BRAIN == "echo":
        async for item in think_echo(user_text, lang):
            yield item
        return
    settings = dashboard.load_settings()
    now = dt.datetime.now()
    tags = [now.strftime("%A %d %B %Y, %I:%M %p")]
    if note:
        tags.append("note from Chitti's own system, not words your friend said")
    else:
        tags.append("heard in " + {"te": "Telugu", "en": "English", "hi": "Hindi"}.get(lang, lang))
    if voice_hint:
        tags.append("voice: " + voice_hint)
    if features.is_night(now):
        tags.append("late night: speak softly and briefly")
    openers = features.recent_openers(history)
    if openers:  # stops every reply starting with the same "అయ్యబాబోయ్"
        tags.append("your last replies began with: " + "; ".join(openers) + " - begin differently")
    style = style_reminder(*persona_choice(), settings.get("use_words", ""), settings.get("avoid_words", ""))
    if style:  # named every turn, so a style change wins over older replies in the history
        tags.append(style)
    if features.WEATHER_WORDS.search(user_text) and not note:
        w = await asyncio.to_thread(features.weather_text, settings.get("city", ""))
        if w:
            tags.append("weather now: " + w)
    live = bool(features.LIVE_WORDS.search(user_text)) and not note
    if live:
        tags.append("you may use web_search once for fresh facts; do not write anything before searching")
    tags.append("reply as: [mood] spoken | English subtitle, one sentence per line")
    # Volatile context goes in the user turn, so the system prompt + older turns stay cached.
    content = "[" + " | ".join(tags) + "]\n" + user_text
    messages = history[-MAX_TURNS * 2:] + [{"role": "user", "content": content}]
    persona = load_persona()

    buf, full, final = "", "", None

    banned = [w.strip() for w in re.split(r"[,،]", settings.get("avoid_words", "")) if w.strip()]

    def scrub(line: str) -> str:
        for w in banned:  # e.g. "హాయ్ మచ్చా!" -> "హాయ్!"; also inside the English subtitle side
            line = re.sub(r"\s*" + re.escape(w) + r"\s*,?", " ", line)
        return re.sub(r"\s+([!?.,|])", r"\1", re.sub(r" {2,}", " ", line)).strip()

    def lines_from(text: str):
        for line in text.split("\n"):
            line = scrub(line) if banned else line
            # a mood tag in the middle of a reply changes the face; it must never be spoken
            m = re.match(r"\s*\[([a-zA-Z]+)\]\s*", line)
            if m and m.group(1).lower() in MOODS:
                yield "mood", m.group(1).lower()
                line = line[m.end():]
            # never speak stray markup or broken characters (a rare model glitch)
            if re.search(r"</?[A-Za-z][\w:-]*(\s[^>]*)?>", line):
                log.warning("dropped a garbled line: %r", line[:80])
                continue
            line = line.replace("�", "")
            if not line.strip():
                continue
            cmd = features.parse_command(line)
            if cmd:
                yield "command", cmd
            else:
                # normally "spoken | subtitle"; sometimes two pairs end up on one line: speak both
                parts = [p.strip() for p in line.split("|")]
                if len(parts) <= 2:
                    yield "sentence", split_subtitle(line)
                else:
                    for i in range(0, len(parts), 2):
                        if parts[i]:
                            yield "sentence", (parts[i], parts[i + 1] if i + 1 < len(parts) else "")

    models = model_candidates()
    for model_name in models:
        use_tools = live
        while True:
            emitted, buf, full, mood_done = False, "", "", False
            # Two cache points: the persona (reused even by a fresh conversation) and the whole history.
            kwargs = dict(model=model_name, max_tokens=2048, messages=messages,
                          system=[{"type": "text", "text": persona, "cache_control": {"type": "ephemeral"}}],
                          cache_control={"type": "ephemeral"})
            th = thinking_for(model_name)
            if th:
                kwargs["thinking"] = th
            if model_name.startswith("claude-sonnet-5-5"):
                kwargs["extra_body"] = {"output_config": {"effort": SONNET_EFFORT}}
            if use_tools:
                kwargs["tools"] = [web_search_tool(model_name)]
            try:
                async with client.messages.stream(**kwargs) as stream:
                    async for text in stream.text_stream:
                        buf += text
                        full += text
                        if not mood_done:
                            m = re.match(r"\s*\[([a-zA-Zఀ-౿]+)\]\s*", buf)
                            if m:
                                raw_mood = m.group(1).lower()
                                mood = TELUGU_MOOD_MAP.get(raw_mood, raw_mood)
                                emitted = True
                                yield "mood", mood if mood in MOODS else "happy"
                                buf, mood_done = buf[m.end():], True
                            elif len(buf) > 20 or (buf.strip() and not buf.lstrip().startswith("[")):
                                emitted = True
                                yield "mood", "happy"
                                mood_done = True
                            else:
                                continue
                        while "\n" in buf:
                            line, buf = buf.split("\n", 1)
                            for item in lines_from(line):
                                emitted = True
                                yield item
                    final = await stream.get_final_message()
                break
            except anthropic.NotFoundError:
                if emitted or model_name == models[-1]:
                    raise
                log.warning("Model %s not available, trying the next one", model_name)
                break
            except anthropic.BadRequestError as err:
                if use_tools and not emitted:
                    log.warning("web search not available (%s); answering without it", err.message)
                    use_tools = False
                    continue
                raise
        if final is not None:
            if CLAUDE_MODEL != model_name:
                CLAUDE_MODEL = model_name
                dashboard.update_state(claude_model=model_name)
                log.info("Using Claude model: %s", model_name)
            break

    for item in lines_from(buf):
        yield item
    if final is not None:
        u = final.usage
        yield "usage", (u, CLAUDE_MODEL)
        cached = (u.cache_read_input_tokens or 0) + (u.cache_creation_input_tokens or 0)
        log.info("tokens: in %d (+%d cache) out %d", u.input_tokens, cached, u.output_tokens)
        if final.stop_reason == "refusal":
            log.warning("refusal: %s", final.stop_details)
    kept = [l for l in full.strip().splitlines() if l.strip() and not features.parse_command(l)]
    reply = re.sub(r"^\s*\[[^\]]+\]\s*", "", "\n".join(kept), flags=re.M).strip()  # mood tags on any line
    reply = "\n".join(split_subtitle(l)[0] for l in reply.splitlines())  # spoken part only (feed, logs)
    # History keeps the full "[mood] spoken | subtitle" lines (minus hidden commands), so the model keeps
    # seeing the right format; spoken-only history made it drop the English subtitles.
    # Text only (no thinking blocks): keeps the stored history valid across restarts.
    history.append({"role": "user", "content": content})
    history.append({"role": "assistant", "content": "\n".join(kept) or "..."})
    if persist:
        save_history(history)
    yield "done", reply


# ---------------------------------------------------------------- end-of-speech detection
class Endpointer:
    """Energy-based voice activity: finishes after ~0.9 s of silence following speech."""

    def __init__(self, rate: int = SR):
        self.rate = rate
        self.noise = 300.0
        self.speech_ms = 0
        self.silence_ms = 0
        self.total_ms = 0

    def feed(self, pcm: bytes) -> bool:
        x = np.frombuffer(pcm, dtype=np.int16).astype(np.float32)
        if x.size == 0:
            return False
        rms = float(np.sqrt(np.mean(x * x)))
        ms = x.size * 1000 // self.rate
        self.total_ms += ms
        if rms > max(300.0, self.noise * 2.5):  # soft word endings still count as speech
            self.speech_ms += ms
            self.silence_ms = 0
        else:
            self.noise = 0.95 * self.noise + 0.05 * rms
            self.silence_ms += ms
        # End of turn: after a short start (like just "Chitti...") people pause, so wait longer then.
        need_quiet = 1600 if self.speech_ms < 1000 else 1100
        if self.speech_ms >= 250 and self.silence_ms >= need_quiet:
            return True
        if self.speech_ms == 0 and self.total_ms > 7000:
            return True  # nobody spoke
        return self.total_ms > 20000


# ---------------------------------------------------------------- short ready-made speech
FILLER_AFTER = 1.3  # if Chitti's first words aren't ready this long after you stop talking, she says "హ్మ్..."
FILLERS = ["హ్మ్...", "ఆ...", "ఉమ్..."]
# (spoken, subtitle, mood) reactions to touch and motion: made once per voice, then reused (no Claude cost)
REACTIONS = {
    "pat": [("హీ హీ, బాగుంది!", "Hehe, that feels nice!", "happy"), ("ఇంకొంచెం... హ్మ్!", "A little more... hmm!", "playful")],
    "poke": [("ఏయ్! ఎందుకు పొడుస్తున్నావ్?", "Hey! Why poke me?", "angry"), ("అబ్బా, నొప్పి!", "Ouch!", "surprised")],
    "shake": [("అయ్యో అయ్యో, తల తిరుగుతోంది!", "Whoa, I'm dizzy!", "laughing"), ("ఆపు ఆపు, పడిపోతా!", "Stop, I'll fall!", "surprised")],
    "pickup": [("ఓహ్, ఎక్కడికి తీసుకెళ్తున్నావ్?", "Oh, where are we going?", "surprised")],
}
_speech_cache: dict[tuple[str, str], bytes] = {}


async def cached_speech(text: str) -> bytes:
    voice = dashboard.load_settings().get("voice", "")
    key = (voice, text)
    if key not in _speech_cache:
        if len(_speech_cache) > 64:
            _speech_cache.clear()
        _speech_cache[key] = await synthesize(text)
    return _speech_cache[key]


# ---------------------------------------------------------------- session
class Session:
    def __init__(self, ws):
        self.ws = ws
        self.listening = False
        self.audio = bytearray()
        self.vad = Endpointer()
        self.rate = SR  # microphone rate, from the board's hello
        self.turn: asyncio.Task | None = None
        self.history = load_history()
        self.persist = True
        self.baseline = features.VoiceBaseline()
        self.burst = 0.0        # seconds of speech still allowed to go out at full speed (fills the board's buffer)
        self.last_reaction = 0.0
        self.night: bool | None = None
        self.sent_audio = bytearray()
        self.underruns = None

    def busy(self) -> bool:
        return self.listening or bool(self.turn and not self.turn.done())

    async def send(self, **msg):
        await self.ws.send(json.dumps(msg, ensure_ascii=False))
        if msg.get("type") == "tts":  # keep an exact copy of each spoken reply (debug/last_reply.wav)
            if msg.get("state") == "start":
                self.sent_audio = bytearray()
            elif self.sent_audio:
                self.save_sent_audio()

    def save_sent_audio(self):
        try:
            d = HERE / "debug"
            d.mkdir(exist_ok=True)
            with wave.open(str(d / "last_reply.wav"), "wb") as w:
                w.setnchannels(1); w.setsampwidth(2); w.setframerate(OUT_SR); w.writeframes(bytes(self.sent_audio))
            x = np.frombuffer(bytes(self.sent_audio), dtype=np.int16).astype(np.int32)
            jumps = int(np.sum(np.abs(np.diff(x)) > 12000))  # sudden sample jumps = clicks in the audio itself
            log.info("reply audio: %.1fs, peak %.2f, sudden jumps %d", x.size / OUT_SR, np.abs(x).max() / 32768 if x.size else 0, jumps)
        except Exception as e:
            log.warning("could not save reply audio: %s", e)

    async def run(self):
        async for frame in self.ws:
            if isinstance(frame, bytes):
                if self.listening:
                    self.audio += frame
                    if self.vad.feed(frame):
                        await self.end_listening()
                continue
            msg = json.loads(frame)
            kind = msg.get("type")
            if kind == "hello":
                log.info("board connected: %s", msg)
                self.rate = int(msg.get("rate", SR))
                if msg.get("device") == "test":  # test runs get a blank memory and never save
                    self.history, self.persist = [], False
                board_ip = self.ws.remote_address[0] if (hasattr(self.ws, "remote_address") and self.ws.remote_address) else "Connected"
                dashboard.update_state(board_connected=True, board_ip=board_ip, board_activity="idle")
                await self.send(type="audio", rate=OUT_SR)
                await self.send(type="state", state="idle")
                await self.update_night()
            elif kind == "battery":
                pct = int(msg.get("pct", -1))
                chg = bool(msg.get("chg", False))
                dashboard.update_state(board_battery=pct, board_charging=chg)
                if "vol" in msg:
                    dashboard.update_state(board_volume=int(msg["vol"]))
                if "temp" in msg:
                    dashboard.update_state(board_temp=float(msg["temp"]))
                if "und" in msg:  # speaker ran dry mid-sound (crackle risk): log when it grows
                    und = int(msg["und"])
                    if self.underruns is not None and und > self.underruns:
                        log.warning("board speaker ran dry %d time(s) since last report", und - self.underruns)
                    self.underruns = und
                    dashboard.update_state(board_underruns=und)
            elif kind == "listen" and msg.get("state") == "start":
                self.cancel_turn()
                dashboard.update_state(board_activity="listening")
                self.listening, self.audio, self.vad = True, bytearray(), Endpointer(self.rate)
                await self.send(type="state", state="listening")
            elif kind == "listen" and msg.get("state") == "stop":
                await self.end_listening()
            elif kind == "abort":
                self.cancel_turn()
                dashboard.update_state(board_activity="idle")
                await self.send(type="tts", state="stop")
                await self.send(type="state", state="idle")
            elif kind == "event":
                name = msg.get("name")
                log.info("event: %s", name)
                if name in ("poke", "shake"):
                    dashboard.update_state(board_mood="angry" if name == "poke" else "laughing")
                if name == "wake":
                    self.maybe_morning_greeting()
                elif name in REACTIONS:
                    self.react(name)

    def cancel_turn(self):
        if self.turn and not self.turn.done():
            self.turn.cancel()

    async def end_listening(self):
        if not self.listening:
            return
        self.listening = False
        await self.send(type="listen", state="stop")
        pcm = bytes(self.audio)
        self.turn = asyncio.create_task(self.respond(pcm))

    # ---- things Chitti starts on her own
    def react(self, name: str):
        """Short spoken reaction to a pat / poke / shake / pick-up (not at night, not mid-conversation)."""
        if self.busy() or features.is_night() or time.monotonic() - self.last_reaction < 8:
            return
        self.last_reaction = time.monotonic()
        import random
        text, sub, mood = random.choice(REACTIONS[name])
        self.turn = asyncio.create_task(self.say_canned(text, sub, mood))

    def maybe_morning_greeting(self):
        now = dt.datetime.now()
        today = now.strftime("%Y-%m-%d")
        if not (5 <= now.hour < 11) or BRAIN != "claude" or self.busy():
            return
        if dashboard.load_settings().get("last_greeting_day") == today:
            return
        dashboard.save_settings({"last_greeting_day": today})
        self.turn = asyncio.create_task(self.morning_greeting())

    async def morning_greeting(self):
        city = dashboard.load_settings().get("city", "")
        weather = await asyncio.to_thread(features.weather_text, city)
        note = "Your friend just woke you up this morning. Greet them warmly in one or two short lines"
        note += f" and mention today's weather naturally ({weather})." if weather else "."
        try:
            await self.reply_to(note, "te", "", time.monotonic(), note=True)
        finally:
            dashboard.update_state(board_activity="idle")
            if not self.listening:
                await self.send(type="state", state="idle")

    async def deliver_reminder(self, r: dict):
        await self.say_canned(r["say"], r.get("sub", ""), "excited", beep=True)
        dashboard.add_feed_entry({"user": "", "reply": "⏰ " + r["say"], "subtitle": r.get("sub", ""), "mood": "excited"})

    async def update_night(self):
        night = features.is_night()
        if night != self.night:
            self.night = night
            await self.send(type="night", on=night)

    async def say_canned(self, text: str, sub: str, mood: str, beep: bool = False):
        try:
            pcm = await cached_speech(text)
            dashboard.update_state(board_activity="speaking", board_mood=mood)
            await self.send(type="mood", mood=mood)
            await self.send(type="state", state="speaking")
            await self.send(type="tts", state="start")
            await self.send(type="say", text=text, en=sub)
            self.burst = 0.8
            if beep:
                await self.stream_audio(chime())
            await self.stream_audio(pcm)
            await self.send(type="tts", state="stop")
        finally:
            dashboard.update_state(board_activity="idle")
            if not self.listening:
                await self.send(type="state", state="idle")

    # ---- a normal conversation turn
    async def respond(self, pcm: bytes):
        t0 = time.monotonic()
        dashboard.update_state(board_activity="thinking")
        await self.send(type="state", state="thinking")
        try:
            if len(pcm) < self.rate * 2 * 0.3:
                return
            if self.persist:
                save_debug_audio(pcm, self.rate)
            text, lang = await asyncio.to_thread(transcribe, pcm, self.rate)
            text = fix_name(text)
            log.info("heard [%s] %.1fs: %s", lang, time.monotonic() - t0, text)
            if not text:
                return
            await self.send(type="stt", text=text, lang=lang)
            hint = await asyncio.to_thread(self.baseline.describe, pcm, self.rate, text)
            if features.LAUGH.search(text):
                hint = (hint + ", " if hint else "") + "laughing"
            if hint:
                log.info("voice hint: %s", hint)
            await self.reply_to(text, lang, hint, t0)
        finally:
            if not self.listening:
                dashboard.update_state(board_activity="idle")
                await self.send(type="state", state="idle")

    async def reply_to(self, text: str, lang: str, voice_hint: str, t0: float, note: bool = False):
        t_stt = time.monotonic()
        speech_q: asyncio.Queue = asyncio.Queue()
        timing = {}
        speaker = asyncio.create_task(self.speak(speech_q, t_stt, timing, filler=not note))
        t_first = 0.0
        last_mood, last_sub, reply = "happy", "", ""
        u_in = u_cached = u_out = 0
        reply_cost_usd = 0.0
        try:
            async for kind, value in think(self.history, text, lang, self.persist, voice_hint=voice_hint, note=note):
                if kind == "mood":
                    last_mood = value
                    dashboard.update_state(board_mood=value)
                    await self.send(type="mood", mood=value)
                elif kind == "sentence":
                    spoken, sub = value
                    if not t_first:
                        t_first = time.monotonic()
                        log.info("timing: speech->text %.2fs, Claude first sentence +%.2fs", t_stt - t0, t_first - t_stt)
                    if sub:
                        last_sub = sub
                    if spoken:
                        speech_q.put_nowait((spoken, sub, start_tts(spoken)))  # all sentences start making audio at once
                elif kind == "command":
                    self.run_command(*value)
                elif kind == "usage":
                    usage, used_model = value
                    u_in = usage.input_tokens or 0
                    u_cached = (usage.cache_read_input_tokens or 0) + (usage.cache_creation_input_tokens or 0)
                    u_out = usage.output_tokens or 0
                    reply_cost_usd = claude_cost_usd(used_model, usage)
                elif kind == "done":
                    reply = value
                    log.info("reply: %s", value)
            speech_q.put_nowait(None)
            await speaker
        except asyncio.CancelledError:
            speaker.cancel()
            raise
        except anthropic.AuthenticationError as e:
            log.error("Anthropic API key missing or invalid: %s", e)
            dashboard.add_feed_entry({"user": text, "reply": "Anthropic API key missing or invalid. Please check dashboard.", "subtitle": "API key error", "mood": "sad"})
            await self.say_error("My brain key is missing. Please check the dashboard.")
            return
        except anthropic.APIConnectionError as e:
            log.error("cannot reach the Claude API: %s", e)
            dashboard.add_feed_entry({"user": text, "reply": "Network error: Cannot reach Claude API. Check internet.", "subtitle": "Connection error", "mood": "sad"})
            await self.say_error("I can't reach the internet right now.")
            return
        except anthropic.APIStatusError as e:
            log.error("Claude API error %s: %s", e.status_code, e.message)
            dashboard.add_feed_entry({"user": text, "reply": f"Claude API Error ({e.status_code}): {e.message}", "subtitle": f"Error {e.status_code}", "mood": "sad"})
            if "credit" in str(e.message).lower() or "balance" in str(e.message).lower():
                dashboard.wallet_mark_empty("claude")
                await self.say_error("My API credit balance is empty.")
            else:
                await self.say_error("Something went wrong in my brain. Try again?")
            return
        cost_inr = reply_cost_usd * USD_TO_INR
        dashboard.record_claude_usage(u_in, u_out, u_cached, reply_cost_usd, cost_inr)
        stt_sec = round(t_stt - t0, 2) if not note else 0.0
        brain_sec = round(t_first - t_stt, 2) if t_first else 0.0
        voice_sec = round(timing["first_audio"] - t_first, 2) if t_first and timing.get("first_audio", 0) > t_first else 0.0
        dashboard.add_feed_entry({
            "user": "" if note else text,
            "reply": reply,
            "subtitle": last_sub,
            "mood": last_mood,
            "voice_hint": voice_hint,
            "stt_sec": stt_sec,
            "first_tok_sec": brain_sec,
            "voice_sec": voice_sec,
            "total_sec": round(timing["first_audio"] - t0, 2) if timing.get("first_audio") else 0.0,
            "filler": timing.get("filler", False),
            "cost_inr": round(cost_inr, 2),
            "cost_usd": round(reply_cost_usd, 4),
        })
        dashboard.update_state(
            last_turn={"user": text, "reply": reply, "subtitle": last_sub, "mood": last_mood, "cost_inr": round(cost_inr, 2)},
            total_turns=dashboard.STATE["total_turns"] + 1,
        )
        dashboard.record_timing(stt_sec, brain_sec, voice_sec)
        sync_dashboard_lists()

    def run_command(self, kind: str, arg):
        if kind == "fact":
            features.add_fact(arg)
        elif kind == "remind":
            minutes, say, sub = arg
            features.add_reminder(minutes, say, sub)
        sync_dashboard_lists()

    async def speak(self, q: asyncio.Queue, t_stt: float, timing: dict, filler: bool = True):
        started = False

        async def next_sentence():
            item = await q.get()
            if item is None:
                return None, None
            return item, await item[2].get()  # wait only for the first piece of this sentence's audio

        pending = asyncio.ensure_future(next_sentence())
        if filler:
            wait = FILLER_AFTER - (time.monotonic() - t_stt)
            done, _ = await asyncio.wait({pending}, timeout=max(0.0, wait))
            if not done:  # still working on it: a natural "hmm..." so the pause doesn't feel like lag
                import random
                pcm = await cached_speech(random.choice(FILLERS))
                if not pending.done():
                    await self.send(type="state", state="speaking")
                    await self.send(type="tts", state="start")
                    started = True
                    timing["filler"] = True
                    self.burst = 0.8
                    await self.stream_audio(pcm)
        try:
            while True:
                item, first = await pending
                if item is None:
                    break
                sentence, sub, audio_q = item
                if not started:
                    await self.send(type="state", state="speaking")
                    await self.send(type="tts", state="start")
                    started = True
                    self.burst = 0.8
                if "first_audio" not in timing:
                    self.burst = max(self.burst, 0.6)
                    timing["first_audio"] = time.monotonic()
                    log.info("first audio %.2fs after you stopped talking", timing["first_audio"] - t_stt)
                await self.send(type="say", text=sentence, en=sub)
                chunk = first
                pending = asyncio.ensure_future(next_sentence())
                while chunk is not None:
                    await self.stream_audio(chunk)
                    chunk = await audio_q.get()
        finally:
            if not pending.done():
                pending.cancel()
            if started:
                await self.send(type="tts", state="stop")

    async def stream_audio(self, pcm: bytes):
        chunk = OUT_SR * 2 // 10  # 100 ms
        for i in range(0, len(pcm), chunk):
            piece = pcm[i:i + chunk]
            await self.ws.send(piece)
            self.sent_audio += piece
            sec = len(piece) / (OUT_SR * 2)
            if self.burst > 0:  # first ~0.8 s go out at once so the board has a cushion (Wi-Fi may nap)
                self.burst -= sec
                continue
            await asyncio.sleep(sec * 0.8)  # then ~1.25x real time; the board buffers

    async def say_error(self, text: str):
        await self.send(type="mood", mood="sad")
        await self.send(type="state", state="speaking")
        await self.send(type="say", text=text, en=text)
        await self.send(type="tts", state="start")
        self.burst = 0.8
        pcm = await offline_safe_speech(text)
        if pcm:
            await self.stream_audio(pcm)
        await self.send(type="tts", state="stop")


ERROR_LINES = ["I can't reach the internet right now.", "My brain key is missing. Please check the dashboard.",
               "My API credit balance is empty.", "Something went wrong in my brain. Try again?"]
OFFLINE_DIR = HERE / "offline_audio"


async def offline_safe_speech(text: str, prefer_saved: bool = False) -> bytes:
    """Speech for a fixed message that still works without internet: made online once, then kept on disk."""
    import hashlib
    voice = dashboard.load_settings().get("voice", "")
    path = OFFLINE_DIR / (hashlib.md5((voice + "|" + text).encode()).hexdigest() + ".pcm")
    if prefer_saved and path.exists():  # already made for this voice: no need to pay again
        return path.read_bytes()
    try:
        pcm = await synthesize(text)
        if pcm:
            OFFLINE_DIR.mkdir(exist_ok=True)
            path.write_bytes(pcm)
            return pcm
    except Exception as e:
        log.info("voice unavailable (%s); using the saved copy", e)
    if path.exists():
        return path.read_bytes()
    saved = sorted(OFFLINE_DIR.glob("*.pcm")) if OFFLINE_DIR.exists() else []  # any voice is better than silence
    return saved[0].read_bytes() if saved and "internet" in text else b""


def chime() -> bytes:
    """Two soft notes before a reminder."""
    out = []
    for freq, ms in ((880, 140), (1320, 220)):
        n = OUT_SR * ms // 1000
        t = np.arange(n) / OUT_SR
        env = np.minimum(1, np.minimum(np.arange(n), n - np.arange(n)) / 300)
        out.append((np.sin(2 * np.pi * freq * t) * 6000 * env).astype(np.int16))
    out.append(np.zeros(OUT_SR // 10, dtype=np.int16))
    return np.concatenate(out).tobytes()


def sync_dashboard_lists():
    dashboard.update_state(facts=features.load_facts(), reminders=features.reminders_for_dashboard())


def ensure_dashboard():
    """Restart the dashboard web page server if it ever stopped answering (it once went missing at startup)."""
    try:
        with socket.create_connection(("127.0.0.1", DASHBOARD_PORT), timeout=2):
            return
    except OSError:
        pass
    try:
        dashboard.start_dashboard_server(port=DASHBOARD_PORT)
        log.warning("Dashboard was not running; restarted it at http://localhost:%d", DASHBOARD_PORT)
    except OSError as e:
        log.error("Dashboard could not start on port %d: %s", DASHBOARD_PORT, e)


async def proactive_loop():
    """Reminders when they're due, and night mode on the board."""
    tick = 0
    while True:
        await asyncio.sleep(5)
        tick += 1
        if tick % 6 == 0:
            await asyncio.to_thread(ensure_dashboard)
        s = active_session
        if not s:
            continue
        try:
            if tick % 12 == 0:
                await s.update_night()
            if not s.busy():
                due = features.pop_due_reminders()
                if due:
                    sync_dashboard_lists()
                    s.turn = asyncio.create_task(s.deliver_reminder(due[0]))
                    for r in due[1:]:  # more than one at the same moment: say the next one shortly after
                        features.add_reminder(0.2, r["say"], r.get("sub", ""))
        except Exception as e:
            log.warning("proactive loop: %s", e)


async def handler(ws):
    global active_session
    session = Session(ws)
    active_session = session
    board_ip = ws.remote_address[0] if (hasattr(ws, "remote_address") and ws.remote_address) else "Connected"
    dashboard.update_state(board_connected=True, board_ip=board_ip, board_activity="idle")
    try:
        await session.run()
    except websockets.ConnectionClosed:
        pass
    finally:
        session.cancel_turn()
        # When Chitti restarts, her old connection closes just after the new one opens:
        # only mark her offline if this was still the live connection.
        if active_session is session:
            active_session = None
            dashboard.update_state(board_connected=False, board_activity="idle")
            log.info("board disconnected")
        else:
            log.info("old board connection closed (a newer one is live)")


DISCOVERY_PORT = int(os.environ.get("CHITTI_DISCOVERY_PORT", "8766"))
DASHBOARD_PORT = int(os.environ.get("CHITTI_DASHBOARD_PORT", "8768"))


class Discovery(asyncio.DatagramProtocol):
    """Answers the board's "CHITTI?" broadcast so it finds this PC without typing an IP."""

    def connection_made(self, transport):
        self.transport = transport

    def datagram_received(self, data, addr):
        if data.strip() == b"CHITTI?":
            self.transport.sendto(f"CHITTI {PORT}".encode(), addr)
            log.info("answered discovery from %s", addr[0])


async def main():
    global active_loop
    active_loop = asyncio.get_running_loop()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    # Also keep a log file (2 x 2 MB), so a crash or problem can be looked at afterwards.
    from logging.handlers import RotatingFileHandler
    fh = RotatingFileHandler(HERE / "chitti.log", maxBytes=2_000_000, backupCount=1, encoding="utf-8")
    fh.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    logging.getLogger().addHandler(fh)

    # Start the local web dashboard immediately
    dashboard.start_dashboard_server(port=DASHBOARD_PORT)
    if os.environ.get("CHITTI_NO_BROWSER") != "1":  # open the dashboard now that it's actually listening
        try:
            os.startfile(f"http://localhost:{DASHBOARD_PORT}")
        except OSError as e:
            log.info("Could not open the browser (%s); open http://localhost:%d yourself", e, DASHBOARD_PORT)
    dashboard.update_state(
        api_key_set=has_claude_key(),
        api_key_masked=dashboard.mask_key(os.environ.get("ANTHROPIC_API_KEY", "")),
        sarvam_key_set=has_sarvam_key(),
        sarvam_key_masked=dashboard.mask_key(os.environ.get("SARVAM_API_KEY", "")),
        tts_engine="sarvam" if has_sarvam_key() else "edge-tts",
        stt_engine="sarvam" if has_sarvam_key() else "whisper",
        claude_model=CLAUDE_MODEL,
        brain_mode=BRAIN,
    )

    # Start UDP Discovery on port 8766 so the board finds this PC instantly
    loop = asyncio.get_running_loop()
    try:
        await loop.create_datagram_endpoint(Discovery, local_addr=("0.0.0.0", DISCOVERY_PORT), allow_broadcast=True)
    except OSError as e:
        log.warning("Auto-discovery port %d is busy (%s); Chitti may not find this PC automatically", DISCOVERY_PORT, e)

    # Warm up Whisper and detect working Claude models in background so server listens without delay
    async def warmup():
        await detect_working_model()
        dashboard.update_state(
            claude_model=CLAUDE_MODEL,
            api_key_set=has_claude_key(),
            api_key_masked=dashboard.mask_key(os.environ.get("ANTHROPIC_API_KEY", "")),
            sarvam_key_set=has_sarvam_key(),
            sarvam_key_masked=dashboard.mask_key(os.environ.get("SARVAM_API_KEY", "")),
            tts_engine="sarvam" if has_sarvam_key() else "edge-tts",
            stt_engine="sarvam" if has_sarvam_key() else "whisper",
            brain_mode=BRAIN,
        )
        await asyncio.to_thread(whisper)
        # warm up the local backup (Whisper) only; sending silence to Sarvam just cost credit and logged "ఆ."
        await asyncio.to_thread(lambda: list(whisper().transcribe(np.zeros(SR, dtype=np.float32), language="te")[0]))
        log.info("Whisper & Claude models ready!")
        for line in ERROR_LINES:  # keep these spoken messages on disk so Chitti can say them offline
            path_ok = await offline_safe_speech(line, prefer_saved=True)
            if not path_ok:
                break

    asyncio.create_task(warmup())
    asyncio.create_task(proactive_loop())
    sync_dashboard_lists()

    async with serve(handler, HOST, PORT, max_size=None, ping_interval=20):
        log.info("Chitti WebSocket server listening on ws://%s:%d/chitti", HOST, PORT)
        log.info("Chitti Web Dashboard: http://localhost:%d", DASHBOARD_PORT)
        log.info("Chitti by Chaitanya Eshwar Prasad - youtube.com/@chaitanya.eshwar.prasad")
        if BRAIN == "echo":
            log.warning("TEST MODE: no Anthropic API key set, Chitti will repeat what she hears")
        await asyncio.Future()


if __name__ == "__main__":
    asyncio.run(main())
