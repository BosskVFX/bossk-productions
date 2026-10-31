"""Text-to-speech.

KokoroTTS: local and free (pip install kokoro soundfile; brew install espeak-ng). Default. bm_lewis is a
           British male voice; ~60 others. Output is WAV, so the face can follow the waveform.
CoreTTS  : ElevenLabs through the core (key stays on Mother). Optional upgrade once a voice is picked.
SayTTS   : macOS `say`. Zero setup fallback.
"""
from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
import urllib.request
from pathlib import Path
from typing import Protocol


class TTS(Protocol):
    def synth(self, text: str) -> Path | None: ...


def _key(cache: Path, *parts: str, ext: str) -> Path:
    return cache / (hashlib.sha1("|".join(parts).encode()).hexdigest() + ext)


class KokoroTTS:
    def __init__(self, voice: str = "bm_lewis", speed: float = 1.05, cache_dir: str | Path = "~/.bossk/tts_cache", fallback: "TTS | None" = None):
        from kokoro import KPipeline  # type: ignore
        import numpy  # type: ignore
        import soundfile  # type: ignore
        self._np, self._sf = numpy, soundfile
        self.voice, self.speed = voice, speed
        self._pipe = KPipeline(lang_code=voice[0])          # 'b' British, 'a' American
        self.cache = Path(cache_dir).expanduser()
        self.cache.mkdir(parents=True, exist_ok=True)
        self.fallback = fallback

    def synth(self, text: str) -> Path | None:
        p = _key(self.cache, "kokoro", self.voice, str(self.speed), text, ext=".wav")
        if p.exists():
            return p
        try:
            chunks = [audio for _, _, audio in self._pipe(text, voice=self.voice, speed=self.speed)]
            if not chunks:
                return None
            self._sf.write(str(p), self._np.concatenate(chunks), 24000, subtype="PCM_16")
            return p
        except Exception:  # noqa: BLE001
            return self.fallback.synth(text) if self.fallback else None

    def prerender(self, phrases: list[str]) -> int:
        return sum(1 for ph in phrases if self.synth(ph))


class CoreTTS:
    def __init__(self, core_url: str, token: str, cache_dir: str | Path, voice: str = "", fallback: "TTS | None" = None):
        self.core_url, self.token, self.voice = core_url.rstrip("/"), token, voice
        self.cache = Path(cache_dir).expanduser()
        self.cache.mkdir(parents=True, exist_ok=True)
        self.fallback = fallback

    def synth(self, text: str) -> Path | None:
        p = _key(self.cache, "eleven", self.voice, text, ext=".mp3")
        if p.exists() and p.stat().st_size:
            return p
        req = urllib.request.Request(self.core_url + "/tts", data=json.dumps({"text": text, "voice": self.voice}).encode(),
                                     headers={"Content-Type": "application/json", "Authorization": f"Bearer {self.token}"}, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                if r.headers.get("Content-Type", "").startswith("audio/"):
                    p.write_bytes(r.read())
                    return p
        except Exception:  # noqa: BLE001
            pass
        return self.fallback.synth(text) if self.fallback else None

    def prerender(self, phrases: list[str]) -> int:
        return sum(1 for ph in phrases if self.synth(ph))


class SayTTS:
    def __init__(self, voice: str = "Daniel", cache_dir: str | Path = "~/.bossk/tts_cache"):
        self.voice = voice
        self.cache = Path(cache_dir).expanduser()
        self.cache.mkdir(parents=True, exist_ok=True)

    def synth(self, text: str) -> Path | None:
        if not shutil.which("say"):
            return None
        p = _key(self.cache, "say", self.voice, text, ext=".wav")
        if not p.exists():
            subprocess.run(["say", "-v", self.voice, "--file-format=WAVE", "--data-format=LEI16@22050", "-o", str(p), text], check=False)
        return p if p.exists() else None


class FakeTTS:
    def __init__(self):
        self.spoken: list[str] = []

    def synth(self, text: str) -> Path | None:
        self.spoken.append(text)
        return None
