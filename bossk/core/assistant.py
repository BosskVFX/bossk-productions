"""The spoken assistant behind "Hey BOSSK, ...": answers a question from live state and the knowledge
folder using Claude, and renders speech through ElevenLabs. Both degrade: no API key means a short
deterministic status answer, and no ElevenLabs key means the mini falls back to macOS `say`."""
from __future__ import annotations

import hashlib
import json
import time
import urllib.request
from pathlib import Path
from typing import Any

try:
    import anthropic  # type: ignore
except ImportError:  # pragma: no cover
    anthropic = None

CHAT_SYSTEM = """You are BOSSK, Jason Miller's background operations supervisor, answering out loud through a speaker.
Answer in one to three short spoken sentences. Plain words, no lists, no markdown, no code. Numbers as words
when small. If the live state answers the question, use it. If you do not know, say so in one sentence.
Never promise to take an action you cannot: you can only report, and tell Jason to say 'yes', 'no', 'pause' or 'resume'."""


class Assistant:
    def __init__(self, api_key: str | None, model: str = "claude-sonnet-5-5", knowledge_dir: str | Path | None = None):
        self.client = anthropic.Anthropic(api_key=api_key) if (anthropic and api_key) else None
        self.model = model
        self.knowledge_dir = Path(knowledge_dir).expanduser() if knowledge_dir else None

    def _knowledge(self, text: str, limit_chars: int = 6000) -> str:
        """Crude retrieval: notes whose filename or tags share a word with the question, newest first."""
        if not self.knowledge_dir or not self.knowledge_dir.exists():
            return ""
        words = {w.lower() for w in text.split() if len(w) > 3}
        hits = []
        for p in sorted(self.knowledge_dir.rglob("*.md"), key=lambda p: p.stat().st_mtime, reverse=True)[:400]:
            head = p.read_text(errors="replace")[:600].lower()
            if any(w in p.stem.lower() or w in head for w in words):
                hits.append(p)
            if len(hits) >= 4:
                break
        out, used = [], 0
        for p in hits:
            body = p.read_text(errors="replace")[:2000]
            out.append(f"--- {p.relative_to(self.knowledge_dir)} ---\n{body}")
            used += len(body)
            if used > limit_chars:
                break
        return "\n".join(out)

    def reply(self, text: str, state: dict[str, Any], recent: list[dict] | None = None) -> str:
        if not self.client:
            return self._fallback(text, state)
        ctx = {"paused": state.get("paused"), "presence": state.get("presence"), "pending_asks": [a.get("text") for a in state.get("pending_asks", [])],
               "recent_events": [{"kind": e.get("kind"), "outcome": e.get("outcome"), "summary": e.get("summary")} for e in (recent or [])[:25]]}
        user = f"Live state:\n{json.dumps(ctx, default=str)[:4000]}\n\nKnowledge notes:\n{self._knowledge(text)}\n\nJason said: {text}"
        try:
            msg = self.client.messages.create(model=self.model, max_tokens=200, system=CHAT_SYSTEM, messages=[{"role": "user", "content": user}])
            return "".join(getattr(b, "text", "") for b in msg.content).strip() or self._fallback(text, state)
        except Exception:  # noqa: BLE001
            return self._fallback(text, state)

    @staticmethod
    def _fallback(text: str, state: dict[str, Any]) -> str:
        pending = len(state.get("pending_asks", []))
        machines = state.get("presence", {}).get("machines", {})
        sessions = sum(len(m.get("sessions", [])) for m in machines.values())
        return f"I cannot think out loud without my API key yet. {sessions} sessions running, {pending} asks pending."


class ElevenLabsTTS:
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
