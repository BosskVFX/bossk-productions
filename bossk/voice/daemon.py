#!/usr/bin/env python3
"""BOSSK voice daemon for the Mac mini (and the laptop).

Two loops in one process:
  1. Asks. Long-polls the core for asks addressed to this machine (GET /asks?channel=voice:<machine>).
     Speaks the ask, opens the mic, sends what Jason said to the core (POST /asks/<id>/answer), handles
     the confirm step, and gives up politely after two unclear answers (the ask stays open on the phone).
  2. Wake word. Reads the mic continuously; audio never leaves the machine until "Hey BOSSK" fires.
     Then it records one utterance, transcribes it locally, and routes it:
       - "stop everything" / "pause"           -> POST /pause
       - "resume" / "unpause" / "carry on"      -> POST /unpause
       - "yes" / "no" / "confirm" with an ask pending -> answers the oldest pending ask
       - "status" / "what's running"            -> spoken summary from GET /health
       - anything else                          -> POST /chat, speaks the reply

State for the expressions window is written to ~/.bossk/voice_state.json on every change:
  {"state": idle|listening|thinking|speaking|asking, "level": 0..1, "text": "...", "ts": ...}
and served at http://127.0.0.1:8751/ with the bare window from voice/expressions/.

Run:   python3 -m voice.daemon --config ~/.bossk/voice.yml
Dev:   python3 -m voice.daemon --dev      (keyboard wake word, macOS `say`, no Porcupine or whisper needed)
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import re
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from voice import audio as audio_mod  # noqa: E402
from voice.audio import FRAME_LENGTH, Player, record_utterance, rms, write_wav  # noqa: E402

log = logging.getLogger("bossk.voice")

PAUSE_RE = re.compile(r"\b(stop everything|pause everything|stop all|hold everything|freeze)\b", re.I)
UNPAUSE_RE = re.compile(r"\b(unpause|resume|carry on|continue everything|you can go)\b", re.I)
STATUS_RE = re.compile(r"\b(status|what'?s running|what are you doing|anything pending|how many sessions)\b", re.I)
ANSWER_RE = re.compile(r"^\s*(yes|yeah|yep|no|nope|confirm|confirmed|approve|deny|go ahead|cancel)\b", re.I)


class CoreClient:
    def __init__(self, url: str, token: str, machine: str):
        self.url, self.token, self.machine = url.rstrip("/"), token, machine

    def _req(self, method: str, path: str, body: dict | None = None, timeout: float = 30) -> dict[str, Any]:
        req = urllib.request.Request(self.url + path, data=json.dumps(body).encode() if body is not None else None, method=method,
                                     headers={"Content-Type": "application/json", "Authorization": f"Bearer {self.token}"})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read() or b"{}")

    def poll_asks(self, wait: int = 25) -> list[dict]:
        try:
            return self._req("GET", f"/asks?channel=voice:{self.machine}&wait={wait}", timeout=wait + 15).get("asks", [])
        except (urllib.error.URLError, OSError, ValueError):
            return []

    def answer(self, ask_id: str, text: str) -> str:
        return str(self._req("POST", f"/asks/{ask_id}/answer", {"text": text, "by": f"voice:{self.machine}"}).get("result", "error"))

    def pending(self) -> list[dict]:
        try:
            return self._req("GET", "/asks?channel=all").get("asks", [])
        except (urllib.error.URLError, OSError, ValueError):
            return []

    def health(self) -> dict:
        return self._req("GET", "/health")

    def pause(self, reason: str) -> None:
        self._req("POST", "/pause", {"reason": reason})

    def unpause(self) -> None:
        self._req("POST", "/unpause", {})

    def chat(self, text: str) -> str:
        return str(self._req("POST", "/chat", {"text": text, "machine": self.machine}, timeout=60).get("reply", ""))

    def event(self, kind: str, message: str) -> None:
        try:
            self._req("POST", "/events", {"machine": self.machine, "session_id": "voice", "event": kind, "message": message}, timeout=5)
        except Exception:  # noqa: BLE001
            pass


class VoiceState:
    """Writes voice_state.json for the expressions window and serves it on a tiny local HTTP server."""
    def __init__(self, path: str | Path, port: int = 8751, serve: bool = True):
        self.path = Path(path).expanduser()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.state, self.level, self.text = "idle", 0.0, ""
        self._lock = threading.Lock()
        self._write()
        if serve:
            from voice.expressions.server import start as start_expr
            start_expr(self, port)

    def set(self, state: str | None = None, level: float | None = None, text: str | None = None) -> None:
        with self._lock:
            if state is not None:
                self.state = state
            if level is not None:
                self.level = max(0.0, min(1.0, level))
            if text is not None:
                self.text = text
            self._write()

    def snapshot(self) -> dict:
        return {"state": self.state, "level": round(self.level, 3), "text": self.text, "ts": time.time()}

    def _write(self) -> None:
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.snapshot()))
        os.replace(tmp, self.path)


class VoiceDaemon:
    def __init__(self, *, core: CoreClient, recorder, wake, stt, tts, player: Player | None = None, state: VoiceState | None = None,
                 phrases: dict[str, str] | None = None, listen_seconds: float = 8.0, sleep=time.sleep):
        self.core, self.rec, self.wake, self.stt, self.tts = core, recorder, wake, stt, tts
        self.player = player or Player()
        self.state = state or VoiceState(tempfile.gettempdir() + "/bossk_voice_state.json", serve=False)
        self.ph = {**DEFAULT_PHRASES, **(phrases or {})}
        self.listen_seconds = listen_seconds
        self._sleep = sleep
        self._mic_lock = threading.Lock()       # the ask loop and the wake loop share one microphone
        self._stop = threading.Event()
        self.handled: list[dict] = []           # for tests and the ledger

    # ------------------------------------------------------------------ speaking and listening
    def say(self, text: str, state: str = "speaking") -> None:
        if not text:
            return
        self.state.set(state=state, text=text, level=0.6)
        log.info("BOSSK: %s", text)
        path = self.tts.synth(text)
        if path:
            self.player.play(path)
        self.state.set(state="idle", level=0.0)

    def listen(self) -> str:
        """Record one utterance and transcribe it. Returns "" when nothing was said."""
        self.state.set(state="listening", level=0.0, text="")
        samples = record_utterance(self.rec, max_seconds=self.listen_seconds, level_cb=lambda l: self.state.set(level=l))
        self.state.set(state="thinking", level=0.0)
        if rms(samples[: FRAME_LENGTH * 4]) == 0 and max((abs(s) for s in samples), default=0) < 300:
            self.state.set(state="idle")
            return ""
        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as f:
            wav = write_wav(samples, f.name)
        try:
            text = self.stt.transcribe(wav)
        finally:
            try:
                os.unlink(wav)
            except OSError:
                pass
        self.state.set(state="idle")
        log.info("Jason: %s", text)
        return text.strip()

    # ------------------------------------------------------------------ asks
    def handle_ask(self, ask: dict) -> str:
        """Speak the ask and run the answer loop. Returns the final core result."""
        with self._mic_lock:
            self.say(ask["text"], state="asking")
            result = "unclear"
            for attempt in range(3):
                heard = self.listen()
                if not heard:
                    if attempt == 0:
                        self.say(self.ph["repeat"], state="asking")
                        continue
                    break
                result = self.core.answer(ask["id"], heard)
                if result == "yes":
                    self.say(self.ph["ack_yes"]); break
                if result == "no":
                    self.say(self.ph["ack_no"]); break
                if result == "awaiting_confirm":
                    self.say(ask.get("confirm_text") or self.ph["confirm"], state="asking")
                    continue
                if result in ("closed", "not_found"):
                    self.say(self.ph["already_handled"]); break
                self.say(self.ph["unclear"], state="asking")
            else:
                result = "gave_up"
            if result in ("unclear", "awaiting_confirm", "gave_up"):
                self.say(self.ph["leave_on_phone"])
                result = "gave_up"
        self.handled.append({"ask_id": ask["id"], "result": result})
        self.core.event("voice_ask", f"{ask['id']}: {result}")
        return result

    def ask_loop(self) -> None:
        while not self._stop.is_set():
            for ask in self.core.poll_asks():
                if self._stop.is_set():
                    return
                self.handle_ask(ask)

    # ------------------------------------------------------------------ wake word
    def handle_wake(self) -> str:
        with self._mic_lock:
            self.state.set(state="listening", text="")
            heard = self.listen()
            if not heard:
                self.say(self.ph["didnt_catch"])
                return "nothing"
            return self.route(heard)

    def route(self, heard: str) -> str:
        self.core.event("voice_command", heard)
        if PAUSE_RE.search(heard):
            self.core.pause(f"Jason said: {heard}")
            self.say(self.ph["paused"])
            return "paused"
        if UNPAUSE_RE.search(heard):
            self.core.unpause()
            self.say(self.ph["unpaused"])
            return "unpaused"
        if ANSWER_RE.match(heard):
            pending = self.core.pending()
            if pending:
                res = self.core.answer(pending[0]["id"], heard)
                self.say({"yes": self.ph["ack_yes"], "no": self.ph["ack_no"], "awaiting_confirm": pending[0].get("confirm_text") or self.ph["confirm"]}.get(res, self.ph["unclear"]))
                return f"answered:{res}"
            self.say(self.ph["nothing_pending"])
            return "nothing_pending"
        if STATUS_RE.search(heard):
            self.say(self.status_text())
            return "status"
        self.state.set(state="thinking")
        reply = self.core.chat(heard) or self.ph["no_answer"]
        self.say(reply)
        return "chat"

    def status_text(self) -> str:
        try:
            h = self.core.health()
        except Exception:  # noqa: BLE001
            return self.ph["core_down"]
        sessions = sum(len(m.get("sessions", [])) for m in h.get("presence", {}).get("machines", {}).values())
        waiting = sum(1 for m in h.get("presence", {}).get("machines", {}).values() for s in m.get("sessions", []) if s.get("waitingFor"))
        pending = len(h.get("pending_asks", []))
        parts = [f"{sessions} session{'s' if sessions != 1 else ''} running"]
        if waiting:
            parts.append(f"{waiting} waiting on input")
        parts.append(f"{pending} ask{'s' if pending != 1 else ''} pending" if pending else "nothing pending")
        if h.get("paused"):
            parts.append("and I am paused")
        return ", ".join(parts) + "."

    def wake_loop(self) -> None:
        self.rec.start()
        try:
            while not self._stop.is_set():
                if self._mic_lock.locked():
                    self._sleep(0.05)
                    continue
                frame = self.rec.read()
                if self.wake.process(frame):
                    self.handle_wake()
        finally:
            self.rec.stop()

    # ------------------------------------------------------------------ lifecycle
    def run(self) -> None:
        t = threading.Thread(target=self.ask_loop, daemon=True, name="asks")
        t.start()
        self.wake_loop()

    def stop(self) -> None:
        self._stop.set()


DEFAULT_PHRASES = {
    "repeat": "Sorry, I did not hear you. Yes or no?",
    "unclear": "I did not get that. Say yes or no.",
    "confirm": "Say confirm to go ahead.",
    "ack_yes": "Done.",
    "ack_no": "Understood, I will not.",
    "already_handled": "That one was already answered.",
    "leave_on_phone": "No problem. I will leave it on your phone.",
    "didnt_catch": "I did not catch that.",
    "paused": "Paused. Nothing runs until you say resume.",
    "unpaused": "Resumed.",
    "nothing_pending": "Nothing is waiting on you.",
    "no_answer": "I do not have an answer for that yet.",
    "core_down": "I cannot reach the core right now.",
}


# ---------------------------------------------------------------------- wiring
def build_from_config(cfg: dict[str, Any], dev: bool = False) -> VoiceDaemon:
    from voice.stt import FakeSTT, WhisperCpp
    from voice.tts import CoreTTS, SayTTS
    from voice.wake import KeyboardWake, PorcupineWake

    agent = json.loads(Path(cfg.get("agent_file", "~/.bossk/agent.json")).expanduser().read_text())
    core = CoreClient(agent["core"], agent["token"], agent.get("machine", "mini"))
    state = VoiceState(cfg.get("state_file", "~/.bossk/voice_state.json"), port=int(cfg.get("expressions_port", 8751)),
                       serve=bool(cfg.get("expressions_window", True)))
    say = SayTTS(voice=cfg.get("say_voice", "Samantha"))
    tts = CoreTTS(agent["core"], agent["token"], cfg.get("tts_cache", "~/.bossk/tts_cache"), voice=cfg.get("voice", ""), fallback=say) \
        if cfg.get("tts", "core") == "core" else say
    phrases = dict(DEFAULT_PHRASES)
    if cfg.get("phrases_file") and Path(cfg["phrases_file"]).expanduser().exists():
        phrases.update(yaml.safe_load(Path(cfg["phrases_file"]).expanduser().read_text()) or {})
    if hasattr(tts, "prerender"):
        n = tts.prerender(list(phrases.values()))
        log.info("pre-rendered %d phrases", n)
    if dev:
        rec = audio_mod.PvRecorder() if _has("pvrecorder") else audio_mod.FakeRecorder()
        wake = KeyboardWake()
        stt = WhisperCpp(cfg["whisper_model"]) if cfg.get("whisper_model") else FakeSTT()
    else:
        rec = audio_mod.PvRecorder(int(cfg.get("mic_index", -1)))
        secrets = _read_env_file(Path(cfg.get("secrets_file", "~/.bossk/secrets.env")).expanduser())
        wake = PorcupineWake(secrets.get("PICOVOICE_ACCESS_KEY", ""), keyword_path=cfg.get("wake_keyword_path"),
                             sensitivity=float(cfg.get("wake_sensitivity", 0.6)))
        stt = WhisperCpp(cfg["whisper_model"], threads=int(cfg.get("whisper_threads", 6)))
    return VoiceDaemon(core=core, recorder=rec, wake=wake, stt=stt, tts=tts, state=state, phrases=phrases,
                       listen_seconds=float(cfg.get("listen_seconds", 8)))


def _has(mod: str) -> bool:
    try:
        __import__(mod)
        return True
    except ImportError:
        return False


def _read_env_file(p: Path) -> dict[str, str]:
    out = {}
    if p.exists():
        for line in p.read_text().splitlines():
            if "=" in line and not line.strip().startswith("#"):
                k, v = line.split("=", 1)
                out[k.strip()] = v.strip().strip("\"'")
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", default="~/.bossk/voice.yml")
    ap.add_argument("--dev", action="store_true", help="keyboard wake word, no Porcupine; whisper only if configured")
    ap.add_argument("-v", "--verbose", action="store_true")
    a = ap.parse_args()
    logging.basicConfig(level=logging.DEBUG if a.verbose else logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    p = Path(a.config).expanduser()
    cfg = yaml.safe_load(p.read_text()) if p.exists() else {}
    d = build_from_config(cfg or {}, dev=a.dev)
    log.info("BOSSK voice daemon up (%s). %s", "dev: press Enter to wake" if a.dev else "say Hey BOSSK", "")
    try:
        d.run()
    except KeyboardInterrupt:
        d.stop()
    return 0


if __name__ == "__main__":
    sys.exit(main())
