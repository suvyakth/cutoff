"""Studio voice: text -> speech through any OpenAI-compatible /audio/speech endpoint (Groq works).

Every clip is cached as web/audio/<sha1 of the text>.wav. The page asks for that file first, so a
judge with no key and no server key still hears the recorded voice. Only a cache miss needs a key.
Configure in .env: HQ_OPENAI_BASE, HQ_OPENAI_KEY (shared with live mode), HQ_TTS_MODEL, HQ_TTS_VOICE.
"""
import hashlib
import io
import json
import os
import re
import struct
import sys
import time
import urllib.error
import urllib.request
import wave

from .data import ROOT

AUDIO_DIR = os.path.join(ROOT, "web", "audio")
CHUNK = 190  # Orpheus-style models cap input near 200 characters, so long text is split at sentence ends


class TtsError(Exception):
    pass


def key(text):
    return hashlib.sha1(text.strip().encode("utf-8")).hexdigest()


def path_for(text):
    return os.path.join(AUDIO_DIR, key(text) + ".wav")


def configured():
    return bool(os.environ.get("HQ_OPENAI_BASE") and os.environ.get("HQ_OPENAI_KEY"))


def settings():
    return {"model": os.environ.get("HQ_TTS_MODEL", "canopylabs/orpheus-v1-english"), "voice": os.environ.get("HQ_TTS_VOICE", "troy")}


def chunks(text, limit=CHUNK):
    sentences = re.split(r"(?<=[.!?;:])\s+", text.strip())
    out, cur = [], ""
    for s in sentences:
        while len(s) > limit:  # a very long sentence: cut at the last comma or space under the limit
            cut = max(s.rfind(", ", 0, limit), s.rfind(" ", 0, limit))
            cut = cut if cut > 40 else limit
            if cur:
                out.append(cur); cur = ""
            out.append(s[:cut + 1].strip()); s = s[cut + 1:].strip()
        if cur and len(cur) + 1 + len(s) > limit:
            out.append(cur); cur = s
        else:
            cur = (cur + " " + s).strip()
    if cur:
        out.append(cur)
    return [c for c in out if c]


def _request(piece):
    cfg = settings()
    body = json.dumps({"model": cfg["model"], "input": piece, "voice": cfg["voice"], "response_format": "wav"}).encode()
    req = urllib.request.Request(os.environ["HQ_OPENAI_BASE"].rstrip("/") + "/audio/speech", body,
                                 {"Authorization": "Bearer " + os.environ["HQ_OPENAI_KEY"], "content-type": "application/json", "User-Agent": "cutoff-hackathon/1.0"})
    for attempt in range(8):
        try:
            with urllib.request.urlopen(req, timeout=90) as r:
                return r.read()
        except urllib.error.HTTPError as e:
            msg = e.read().decode("utf-8", "replace")
            if e.code == 429 and attempt < 7:  # speech models have their own free-tier limits: wait as asked, then retry
                m = re.search(r"try again in (?:(\d+)m)?(\d+(?:\.\d+)?)s", msg)
                wait = (int(m.group(1) or 0) * 60 + float(m.group(2)) + 1.5) if m else 20
                if "per day" in msg or wait > 300:  # a daily quota will not recover in minutes: say so instead of waiting
                    raise TtsError("daily speech quota used up on this key: " + (re.search(r"Please try again in [^.]*", msg).group(0) if re.search(r"Please try again in [^.]*", msg) else "try again later"))
                print(f"[tts] 429, waiting {min(wait, 75):.0f}s (attempt {attempt + 1}): {msg[:600]}", file=sys.stderr, flush=True)
                time.sleep(min(wait, 75))
                continue
            raise TtsError(f"{e.code} from the speech endpoint: {msg[:400]}")
        except TtsError:
            raise
        except Exception as e:
            raise TtsError(f"{type(e).__name__}: {e}")


def _pcm(b):
    """Parse a WAV leniently. Streaming servers write placeholder sizes (0 or 0xFFFFFFFF) in the header."""
    if b[:4] != b"RIFF" or b[8:12] != b"WAVE":
        raise TtsError("the speech endpoint did not return a WAV file")
    i, fmt = 12, None
    while i + 8 <= len(b):
        cid, size = b[i:i + 4], struct.unpack("<I", b[i + 4:i + 8])[0]
        body = i + 8
        if cid == b"fmt ":
            fmt = struct.unpack("<HHIIHH", b[body:body + 16])
        if cid == b"data":
            end = len(b) if size in (0, 0xFFFFFFFF) or body + size > len(b) else body + size
            if fmt is None:
                raise TtsError("WAV data before format")
            return (fmt[1], fmt[2], fmt[5]), b[body:end]
        i = body + size + (size & 1)
    raise TtsError("WAV has no audio data")


def merge(wavs):
    """Join WAV clips into one clean WAV with correct header sizes."""
    parts = [_pcm(w) for w in wavs]
    spec = parts[0][0]
    if any(p[0] != spec for p in parts):
        raise TtsError("speech chunks came back in different audio formats")
    out = io.BytesIO()
    with wave.open(out, "wb") as w:
        w.setnchannels(spec[0]); w.setframerate(spec[1]); w.setsampwidth(spec[2] // 8)
        for _, pcm in parts:
            w.writeframes(pcm)
    return out.getvalue()


def synth(text):
    """Return WAV bytes for text, from cache if possible. Raises TtsError."""
    text = text.strip()
    p = path_for(text)
    if os.path.exists(p):
        with open(p, "rb") as f:
            return f.read()
    if not configured():
        raise TtsError("no speech key configured (set HQ_OPENAI_BASE and HQ_OPENAI_KEY in .env)")
    audio = merge([_request(c) for c in chunks(text)])  # always re-written, so headers are valid
    os.makedirs(AUDIO_DIR, exist_ok=True)
    with open(p, "wb") as f:
        f.write(audio)
    return audio
