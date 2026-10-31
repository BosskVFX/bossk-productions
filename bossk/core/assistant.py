"""The spoken assistant behind push-to-talk: answers through headless Claude Code on Jason's Claude
subscription (never the API), with live state in the prompt and the shared knowledge folder as its working
directory, so the knowledge folder's CLAUDE.md boot file and notes are its memory. Streams sentences so
speech can start before the answer is finished. Also renders ElevenLabs speech when a key is configured."""
from __future__ import annotations

import hashlib
import json
import threading
import time
import urllib.request
from pathlib import Path
from typing import Any, Iterator

from .claude_cli import ClaudeCLI, sentences

CHAT_SYSTEM = """You are BOSSK, Jason Miller's background operations supervisor, answering out loud through a speaker.
Answer in one to three short spoken sentences. Plain words, no lists, no markdown, no code, no file paths read aloud.
Use the live state in the message and the notes in this folder; read notes with your tools when the question needs them.
If you do not know, say so in one sentence. You can only report; actions happen when Jason says yes, no, pause or resume."""

CONVERSATION_TTL_S = 600   # a follow-up within 10 minutes continues the same Claude session


class Assistant:
    def __init__(self, cli: ClaudeCLI | None, model: str = "haiku"):
        self.cli = cli if (cli and cli.available) else None
        self.model = model
        self._sessions: dict[str, tuple[str, float]] = {}
        self._lock = threading.Lock()

    @property
    def available(self) -> bool:
        return self.cli is not None

    def _prompt(self, text: str, state: dict[str, Any], recent: list[dict] | None) -> str:
        ctx = {"paused": state.get("paused"), "presence": state.get("presence"),
               "pending_asks": [a.get("text") for a in state.get("pending_asks", [])],
               "recent_events": [{"kind": e.get("kind"), "outcome": e.get("outcome"), "summary": e.get("summary")}
                                 for e in (recent or [])[:25]]}
        return f"Live state:\n{json.dumps(ctx, default=str)[:4000]}\n\nJason said: {text}"

    def stream(self, text: str, state: dict[str, Any], recent: list[dict] | None = None, *, machine: str = "",
               cancel: threading.Event | None = None) -> Iterator[str]:
        """Yield spoken sentences."""
        if not self.cli:
            yield self._fallback(state)
            return
        with self._lock:
            prev = self._sessions.get(machine)
        resume = prev[0] if prev and time.time() - prev[1] < CONVERSATION_TTL_S else None
        meta: dict[str, Any] = {}
        any_out = False
        for s in sentences(self.cli.stream(self._prompt(text, state, recent), system=CHAT_SYSTEM, resume=resume,
                                           model=self.model, max_turns=4, tools=("Read", "Grep", "Glob"),
                                           cancel=cancel, meta=meta)):
            if cancel is not None and cancel.is_set():
                break
            any_out = True
            yield s
        if meta.get("session_id"):
            with self._lock:
                self._sessions[machine] = (meta["session_id"], time.time())
        if not any_out and not (cancel and cancel.is_set()):
            yield "Sorry, I could not get an answer just now." if meta.get("error") else self._fallback(state)

    def reply(self, text: str, state: dict[str, Any], recent: list[dict] | None = None, machine: str = "") -> str:
        return " ".join(self.stream(text, state, recent, machine=machine))

    @staticmethod
    def _fallback(state: dict[str, Any]) -> str:
        pending = len(state.get("pending_asks", []))
        machines = state.get("presence", {}).get("machines", {})
        sessions = sum(len(m.get("sessions", [])) for m in machines.values())
        return f"My Claude login is not set up on the server yet. {sessions} sessions running, {pending} asks pending."


class ElevenLabsTTS:
    """Optional premium voice. The mini speaks with local Kokoro by default; this only runs if a key is set."""
    def __init__(self, api_key: str | None, voice_id: str = "", model_id: str = "eleven_flash_v2_5", cache_dir: str | Path = "~/.bossk/tts_cache"):
        self.api_key, self.voice_id, self.model_id = api_key, voice_id, model_id
        self.cache = Path(cache_dir).expanduser()
        self.cache.mkdir(parents=True, exist_ok=True)

    @property
    def available(self) -> bool:
        return bool(self.api_key and self.voice_id)

    def synth(self, text: str, voice_id: str = "") -> bytes | None:
        vid = voice_id or self.voice_id
        if not self.api_key or not vid:
            return None
        key = self.cache / (hashlib.sha1(f"{vid}|{self.model_id}|{text}".encode()).hexdigest() + ".mp3")
        if key.exists():
            return key.read_bytes()
        req = urllib.request.Request(f"https://api.elevenlabs.io/v1/text-to-speech/{vid}?output_format=mp3_44100_64",
                                     data=json.dumps({"text": text, "model_id": self.model_id}).encode(),
                                     headers={"xi-api-key": self.api_key, "Content-Type": "application/json", "Accept": "audio/mpeg"}, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                data = r.read()
        except Exception:  # noqa: BLE001
            return None
        if data:
            key.write_bytes(data)
        return data or None
