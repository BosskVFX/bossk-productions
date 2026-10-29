"""Text-to-speech backends.

CoreTTS: asks the BOSSK core for audio (POST /tts). The core holds the ElevenLabs key; the mini never
sees it. Audio is cached on the mini by text hash, and the fixed phrases are pre-rendered at startup so
only the variable part of an ask is ever a live call.
SayTTS: macOS built-in `say`. Zero setup, sounds like Apple. The fallback when the core or ElevenLabs is down.
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
    def synth(self, text: str) -> Path | None: ...       # path to an audio file, or None to skip speaking


class CoreTTS:
    def __init__(self, core_url: str, token: str, cache_dir: str | Path, voice: str = "", fallback: "TTS | None" = None):
        self.core_url, self.token, self.voice = core_url.rstrip("/"), token, voice
        self.cache = Path(cache_dir).expanduser()
        self.cache.mkdir(parents=True, exist_ok=True)
        self.fallback = fallback

    def _key(self, text: str) -> Path:
        return self.cache / (hashlib.sha1(f"{self.voice}|{text}".encode()).hexdigest() + ".mp3")

    def synth(self, text: str) -> Path | None:
        p = self._key(text)
        if p.exists() and p.stat().st_size > 0:
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
        n = 0
        for ph in phrases:
            if not self._key(ph).exists() and self.synth(ph):
                n += 1
        return n


class SayTTS:
    def __init__(self, voice: str = "Samantha", cache_dir: str | Path = "~/.bossk/tts_cache_say"):
        self.voice = voice
        self.cache = Path(cache_dir).expanduser()
        self.cache.mkdir(parents=True, exist_ok=True)

    def synth(self, text: str) -> Path | None:
        if not shutil.which("say"):
            return None
        p = self.cache / (hashlib.sha1(f"{self.voice}|{text}".encode()).hexdigest() + ".aiff")
        if not p.exists():
            subprocess.run(["say", "-v", self.voice, "-o", str(p), text], check=False)
        return p if p.exists() else None


class FakeTTS:
    def __init__(self):
        self.spoken: list[str] = []

    def synth(self, text: str) -> Path | None:
        self.spoken.append(text)
        return None
