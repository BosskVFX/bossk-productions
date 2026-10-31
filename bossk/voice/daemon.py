#!/usr/bin/env python3
"""BOSSK voice daemon, push-to-talk. Runs on the Mac mini (and the laptop).

How it behaves
  * Hold the talk button and speak; let go when done. The mic is open only while the button is held, so BOSSK
    never hears itself. Tap the button (Stream Deck) instead to start, and it stops after a pause.
  * Press the button while BOSSK is talking and it stops instantly. Whatever you say next is the new request.
  * When a Claude Code session needs Jason, BOSSK chimes and speaks the ask, then waits. It never gives up and
    walks away: the ask stays open (and goes to the phone after the core's grace period) until it is answered.
    Hold the button and say:
        "yes" / "no"          answers it ("confirm" is needed after "yes" for money and messages)
        "details"             reads exactly what will run
        "repeat"              says the ask again
  * Anything else goes to BOSSK's brain on Mother (Claude Code on Jason's Claude plan) and is spoken back
    sentence by sentence as it is written. "Stop everything" pauses all auto-approvals; "resume" undoes it;
    "status" says what is running.

The face (expressions window, http://127.0.0.1:8751/) follows state: idle, listening, thinking, speaking,
asking (an ask is waiting), with live level and a 64-point waveform from the mic or the voice.

Run:  python3 -m voice.daemon --config ~/.bossk/voice.yml
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import queue
import re
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Callable

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from voice.audio import SAMPLE_RATE, Player, rms, waveform64, write_wav  # noqa: E402

log = logging.getLogger("bossk.voice")

ANSWER_RE = re.compile(r"^\W*(yes|yeah|yep|yup|no|nope|nah|confirm|confirmed|approve|approved|deny|denied|go ahead|do it|cancel|don'?t)\b", re.I)
DETAILS_RE = re.compile(r"\b(details?|what (exactly|command)|show me|what will (it|you) run|what is it running)\b", re.I)
REPEAT_RE = re.compile(r"\b(repeat|say (that|it) again|what did you (ask|say)|come again|what was (that|the question))\b", re.I)
PAUSE_RE = re.compile(r"\b(stop everything|pause everything|pause all|stop all|hold everything|freeze everything)\b", re.I)
UNPAUSE_RE = re.compile(r"\b(unpause|resume|carry on|continue everything|you can go)\b", re.I)
STATUS_RE = re.compile(r"^\W*(status|what'?s running|what is running|what are you doing|anything pending|how many sessions)\b", re.I)
NEVERMIND_RE = re.compile(r"^\W*(never ?mind|forget it|nothing|ignore that)\W*$", re.I)

PHRASES = {
    "ack_yes": "Done.",
    "ack_no": "Okay, I won't.",
    "confirm": "Say confirm to go ahead.",
    "unclear_answer": "Say yes, no, or details.",
    "already": "That one was already answered.",
    "didnt_catch": "I didn't catch that.",
    "paused": "Paused. Nothing runs on its own until you say resume.",
    "unpaused": "Resumed.",
    "nothing_pending": "Nothing is waiting on you.",
    "okay": "Okay.",
    "core_down": "I can't reach Mother right now.",
    "handled_elsewhere": "That ask was answered on your phone.",
}


# ---------------------------------------------------------------------- core client
class CoreClient:
    def __init__(self, url: str, token: str, machine: str):
        self.url, self.token, self.machine = url.rstrip("/"), token, machine

    def _req(self, method: str, path: str, body: dict | None = None, timeout: float = 30):
        return urllib.request.Request(self.url + path, data=json.dumps(body).encode() if body is not None else None, method=method,
                                      headers={"Content-Type": "application/json", "Authorization": f"Bearer {self.token}"})

    def call(self, method: str, path: str, body: dict | None = None, timeout: float = 30) -> dict[str, Any]:
        with urllib.request.urlopen(self._req(method, path, body), timeout=timeout) as r:
            return json.loads(r.read() or b"{}")

    def poll_asks(self, wait: int = 20) -> list[dict] | None:
        try:
            return self.call("GET", f"/asks?channel=voice:{self.machine}&wait={wait}", timeout=wait + 15).get("asks", [])
        except (urllib.error.URLError, OSError, ValueError):
            return None

    def pending(self) -> list[dict]:
        try:
            return self.call("GET", "/asks?channel=all").get("asks", [])
        except (urllib.error.URLError, OSError, ValueError):
            return []

    def answer(self, ask_id: str, text: str) -> str:
        try:
            return str(self.call("POST", f"/asks/{ask_id}/answer", {"text": text, "by": f"voice:{self.machine}"}).get("result", "error"))
        except (urllib.error.URLError, OSError, ValueError):
            return "error"

    def health(self) -> dict:
        return self.call("GET", "/health")

    def pause(self, reason: str) -> None:
        self.call("POST", "/pause", {"reason": reason})

    def unpause(self) -> None:
        self.call("POST", "/unpause", {})

    def event(self, kind: str, message: str) -> None:
        try:
            self.call("POST", "/events", {"machine": self.machine, "session_id": "voice", "event": kind, "message": message}, timeout=5)
        except Exception:  # noqa: BLE001
            pass

    def chat_stream(self, text: str, cancel: threading.Event):
        """Yield sentences from the core's streaming /chat. Closing the connection on cancel stops Claude on Mother."""
        resp = urllib.request.urlopen(self._req("POST", "/chat", {"text": text, "machine": self.machine, "stream": True}), timeout=120)
        try:
            for raw in resp:
                if cancel.is_set():
                    return
                line = raw.decode(errors="replace").strip()
                if not line:
                    continue
                try:
                    msg = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if msg.get("done"):
                    return
                if msg.get("text"):
                    yield msg["text"]
        finally:
            resp.close()


# ---------------------------------------------------------------------- face state
class VoiceState:
    def __init__(self, path: str | Path | None = None):
        self.path = Path(path).expanduser() if path else None
        if self.path:
            self.path.parent.mkdir(parents=True, exist_ok=True)
        self.state, self.level, self.text, self.waveform = "idle", 0.0, "", [0.0] * 64
        self.pending_ask = False
        self._lock = threading.Lock()
        self._last_write = 0.0

    def set(self, state: str | None = None, *, level: float | None = None, text: str | None = None, waveform: list[float] | None = None) -> None:
        with self._lock:
            if state is not None:
                self.state = state
            if level is not None:
                self.level = max(0.0, min(1.0, level))
            if text is not None:
                self.text = text
            if waveform is not None:
                self.waveform = waveform
            now = time.time()
            if self.path and (state is not None or now - self._last_write > 0.1):
                self._last_write = now
                tmp = self.path.with_suffix(".tmp")
                tmp.write_text(json.dumps(self._snap()))
                os.replace(tmp, self.path)

    def _snap(self) -> dict:
        return {"state": self.state, "level": round(self.level, 3), "text": self.text, "waveform": self.waveform,
                "pending_ask": self.pending_ask, "ts": time.time()}

    def snapshot(self) -> dict:
        with self._lock:
            return self._snap()


# ---------------------------------------------------------------------- speaker
class Speaker:
    """Speaks queued sentences on its own thread. interrupt() drops the queue and cuts the current sentence."""
    def __init__(self, tts, player, state: VoiceState, rest_state: Callable[[], str]):
        self.tts, self.player, self.state, self.rest_state = tts, player, state, rest_state
        self.q: queue.Queue = queue.Queue()
        self.gen = 0
        self.busy = threading.Event()
        self.spoken: list[str] = []
        threading.Thread(target=self._run, daemon=True, name="speaker").start()

    def say(self, text: str, state: str = "speaking") -> None:
        if text:
            self.busy.set()
            self.q.put((text, state, self.gen))

    def interrupt(self) -> None:
        self.gen += 1
        try:
            while True:
                self.q.get_nowait()
        except queue.Empty:
            pass
        self.player.stop()
        self.q.put((None, None, -1))      # wake the speaker thread so it settles back to rest

    def wait_idle(self, timeout: float = 30) -> bool:
        end = time.time() + timeout
        while time.time() < end:
            if not self.busy.is_set() and self.q.empty():
                return True
            time.sleep(0.02)
        return False

    def _run(self) -> None:
        while True:
            text, st, gen = self.q.get()
            if text is None or gen != self.gen:
                self._maybe_rest()
                continue
            self.busy.set()
            self.state.set(st, text=text, level=0.3)
            log.info("BOSSK: %s", text)
            self.spoken.append(text)
            path = self.tts.synth(text)
            if path and gen == self.gen:
                self.player.play(path, level_cb=lambda l, wf: self.state.set(level=l, waveform=wf))
            self._maybe_rest()

    def _maybe_rest(self) -> None:
        if self.q.empty():
            self.busy.clear()
            if self.state.state in ("speaking", "asking"):
                self.state.set(self.rest_state(), level=0.0, text="", waveform=[0.0] * 64)


# ---------------------------------------------------------------------- daemon
class VoiceDaemon:
    def __init__(self, *, core: CoreClient, capture, stt, tts, player=None, state: VoiceState | None = None,
                 phrases: dict[str, str] | None = None, min_press_s: float = 0.3, toggle_silence_s: float = 1.2,
                 toggle_max_s: float = 25.0, chime: str | Path | None = None):
        self.core, self.capture, self.stt = core, capture, stt
        self.player = player or Player()
        self.state = state or VoiceState()
        self.ph = {**PHRASES, **(phrases or {})}
        self.min_press_s, self.toggle_silence_s, self.toggle_max_s = min_press_s, toggle_silence_s, toggle_max_s
        self.chime = Path(chime).expanduser() if chime else None
        self.speaker = Speaker(tts, self.player, self.state, self._rest_state)
        self.events: queue.Queue = queue.Queue()
        self.work: queue.Queue = queue.Queue()
        self.asks: list[dict] = []                 # open asks for this machine, oldest first; asks[0] is "the" ask
        self.announced: set[str] = set()
        self.listening = False
        self.toggle_mode = False
        self.press_t = 0.0
        self._spoke_t = 0.0
        self._heard_speech = False
        self.chat_cancel: threading.Event | None = None
        self.thinking = threading.Event()
        self._stop = threading.Event()
        self.log: list[dict] = []                  # what happened, for tests and the ledger

    # -------------------------------------------------------------- input events (from any trigger)
    def post(self, kind: str, payload: Any = None) -> None:
        self.events.put((kind, payload))

    def _rest_state(self) -> str:
        self.state.pending_ask = bool(self.asks)
        return "asking" if self.asks else "idle"

    def interrupt(self) -> None:
        self.speaker.interrupt()
        if self.chat_cancel:
            self.chat_cancel.set()

    def _on_frame(self, f: list[int]) -> None:
        lvl = rms(f)
        self.state.set(level=lvl, waveform=waveform64(f))
        if lvl >= 0.04:
            self._heard_speech, self._spoke_t = True, time.time()

    def _begin_listening(self, toggle: bool) -> None:
        self.interrupt()
        self.listening, self.toggle_mode, self.press_t = True, toggle, time.time()
        self._heard_speech, self._spoke_t = False, 0.0
        self.state.set("listening", level=0.0, text="")
        self.capture.begin(self._on_frame)

    def _end_listening(self) -> None:
        if not self.listening:
            return
        samples = self.capture.end()
        held = time.time() - self.press_t
        self.listening, self.toggle_mode = False, False
        if held < self.min_press_s and not self._heard_speech:
            self.state.set(self._rest_state(), level=0.0)          # a tap: only an interrupt
            return
        self.state.set("thinking", level=0.0)
        self.work.put(samples)

    def handle_event(self, kind: str, payload: Any = None) -> None:
        if kind == "down":
            if not self.listening:
                self._begin_listening(toggle=False)
        elif kind == "up":
            self._end_listening()
        elif kind == "toggle":
            if self.listening:
                self._end_listening()
            elif self.speaker.busy.is_set() or self.thinking.is_set():
                self.interrupt()
                self.state.set(self._rest_state(), level=0.0)
            else:
                self._begin_listening(toggle=True)
        elif kind == "stop":
            self.interrupt()
            self.state.set(self._rest_state(), level=0.0)
        elif kind == "asks":
            self._sync_asks(payload or [])

    def _sync_asks(self, current: list[dict]) -> None:
        ids = {a["id"] for a in current}
        gone = [a for a in self.asks if a["id"] not in ids]
        self.asks = [a for a in self.asks if a["id"] in ids] + [a for a in current if a["id"] not in {x["id"] for x in self.asks}]
        for a in gone:
            if a["id"] in self.announced and a.get("_answered_here") is not True:
                self.speaker.say(self.ph["handled_elsewhere"])
        if self.asks and self.asks[0]["id"] not in self.announced and not self.listening and not self.thinking.is_set():
            self._announce(self.asks[0])
        self.state.pending_ask = bool(self.asks)
        if not self.asks and self.state.state == "asking":
            self.state.set("idle")

    def _announce(self, ask: dict) -> None:
        self.announced.add(ask["id"])
        if self.chime and self.chime.exists():
            self.player.play(self.chime)
        self.speaker.say(ask["text"], state="asking")
        self.log.append({"announce": ask["id"]})

    # -------------------------------------------------------------- understanding (worker thread)
    def handle_text(self, heard: str) -> str:
        t = heard.strip()
        self.core.event("voice_command", t)
        if not t:
            self.speaker.say(self.ph["didnt_catch"])
            return "nothing"
        if NEVERMIND_RE.match(t):
            self.speaker.say(self.ph["okay"])
            return "nevermind"
        if PAUSE_RE.search(t):
            try:
                self.core.pause(f"Jason said: {t}")
                self.speaker.say(self.ph["paused"])
            except Exception:  # noqa: BLE001
                self.speaker.say(self.ph["core_down"])
            return "paused"
        if UNPAUSE_RE.search(t):
            try:
                self.core.unpause()
                self.speaker.say(self.ph["unpaused"])
            except Exception:  # noqa: BLE001
                self.speaker.say(self.ph["core_down"])
            return "unpaused"
        ask = self.asks[0] if self.asks else None
        if ask:
            if DETAILS_RE.search(t):
                self.speaker.say(details_text(ask), state="asking")
                return "details"
            if REPEAT_RE.search(t):
                self.speaker.say(ask["text"], state="asking")
                return "repeat"
            if ANSWER_RE.match(t):
                return self._answer(ask, t)
        elif ANSWER_RE.match(t):
            others = self.core.pending()
            if others:
                return self._answer(others[0], t)
            self.speaker.say(self.ph["nothing_pending"])
            return "nothing_pending"
        if STATUS_RE.match(t):
            self.speaker.say(self.status_text())
            return "status"
        return self._chat(t)

    def _answer(self, ask: dict, t: str) -> str:
        res = self.core.answer(ask["id"], t)
        if res == "yes":
            ask["_answered_here"] = True
            self.asks = [a for a in self.asks if a["id"] != ask["id"]]
            self.speaker.say(self.ph["ack_yes"])
        elif res == "no":
            ask["_answered_here"] = True
            self.asks = [a for a in self.asks if a["id"] != ask["id"]]
            self.speaker.say(self.ph["ack_no"])
        elif res == "awaiting_confirm":
            self.speaker.say(ask.get("confirm_text") or self.ph["confirm"], state="asking")
        elif res in ("closed", "not_found"):
            self.asks = [a for a in self.asks if a["id"] != ask["id"]]
            self.speaker.say(self.ph["already"])
        elif res == "error":
            self.speaker.say(self.ph["core_down"])
        else:
            self.speaker.say(self.ph["unclear_answer"], state="asking")
        self.log.append({"answer": ask["id"], "result": res})
        if self.asks and self.asks[0]["id"] not in self.announced:
            self._announce(self.asks[0])
        return f"answered:{res}"

    def _chat(self, t: str) -> str:
        cancel = threading.Event()
        self.chat_cancel = cancel
        self.state.set("thinking")
        n = 0
        try:
            for sentence in self.core.chat_stream(t, cancel):
                if cancel.is_set():
                    break
                self.speaker.say(sentence)
                n += 1
        except Exception:  # noqa: BLE001
            if not cancel.is_set():
                self.speaker.say(self.ph["core_down"])
        if n == 0 and not cancel.is_set():
            self.state.set(self._rest_state())
        return "interrupted" if cancel.is_set() else "chat"

    def status_text(self) -> str:
        try:
            h = self.core.health()
        except Exception:  # noqa: BLE001
            return self.ph["core_down"]
        machines = h.get("presence", {}).get("machines", {})
        sessions = sum(len(m.get("sessions", [])) for m in machines.values())
        waiting = sum(1 for m in machines.values() for s in m.get("sessions", []) if s.get("waitingFor"))
        pending = len(h.get("pending_asks", []))
        parts = [f"{sessions} session{'s' if sessions != 1 else ''} running"]
        if waiting:
            parts.append(f"{waiting} waiting on input")
        parts.append(f"{pending} ask{'s' if pending != 1 else ''} pending" if pending else "nothing pending")
        if h.get("paused"):
            parts.append("and I'm paused")
        return ", ".join(parts) + "."

    def process(self, samples: list[int]) -> str:
        self.thinking.set()
        try:
            if max((abs(s) for s in samples), default=0) < 300:
                self.speaker.say(self.ph["didnt_catch"])
                return "nothing"
            with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as f:
                wav = write_wav(samples, f.name, SAMPLE_RATE)
            try:
                text = self.stt.transcribe(wav)
            finally:
                try:
                    os.unlink(wav)
                except OSError:
                    pass
            log.info("Jason: %s", text)
            result = self.handle_text(text)
            self.log.append({"heard": text, "result": result})
            return result
        finally:
            self.thinking.clear()
            if self.asks and self.asks[0]["id"] not in self.announced and not self.listening:
                self._announce(self.asks[0])

    # -------------------------------------------------------------- threads
    def _event_loop(self) -> None:
        while not self._stop.is_set():
            try:
                kind, payload = self.events.get(timeout=0.05)
                self.handle_event(kind, payload)
            except queue.Empty:
                pass
            if self.listening and self.toggle_mode:
                now = time.time()
                if (self._heard_speech and now - self._spoke_t > self.toggle_silence_s) or now - self.press_t > self.toggle_max_s:
                    self._end_listening()

    def _work_loop(self) -> None:
        while not self._stop.is_set():
            try:
                samples = self.work.get(timeout=0.1)
            except queue.Empty:
                continue
            try:
                self.process(samples)
            except Exception:  # noqa: BLE001
                log.exception("processing failed")
                self.speaker.say(self.ph["didnt_catch"])

    def _ask_loop(self) -> None:
        while not self._stop.is_set():
            asks = self.core.poll_asks(wait=20)
            if asks is None:
                time.sleep(3)
                continue
            self.post("asks", asks)
            if not asks:
                continue
            time.sleep(1)            # long-poll returns at once while asks are pending; re-check each second

    def start(self) -> None:
        for fn, name in ((self._event_loop, "events"), (self._work_loop, "work"), (self._ask_loop, "asks")):
            threading.Thread(target=fn, daemon=True, name=name).start()

    def stop(self) -> None:
        self._stop.set()
        self.interrupt()


def details_text(ask: dict) -> str:
    ti = ask.get("tool_input") or {}
    what = ti.get("command") or ti.get("file_path") or ti.get("url") or json.dumps(ti)[:160]
    what = str(what)
    if len(what) > 220:
        what = what[:220] + ", and more"
    session = (ask.get("context") or {}).get("session_name") or ask.get("session_id", "")[:8]
    return f"The {session} session on {ask.get('machine')} wants to run: {what}. Yes or no?"


# ---------------------------------------------------------------------- wiring
def build_from_config(cfg: dict[str, Any]) -> tuple[VoiceDaemon, list]:
    from voice.audio import SoundDeviceCapture
    from voice.expressions.server import start as start_face
    from voice.ptt import KeyTrigger
    from voice.stt import build as build_stt
    from voice.tts import CoreTTS, KokoroTTS, SayTTS

    agent = json.loads(Path(cfg.get("agent_file", "~/.bossk/agent.json")).expanduser().read_text())
    core = CoreClient(agent["core"], agent["token"], agent.get("machine", "mini"))
    state = VoiceState(cfg.get("state_file", "~/.bossk/voice_state.json"))
    say = SayTTS(voice=cfg.get("say_voice", "Daniel"))
    kind = cfg.get("tts", "kokoro")
    if kind == "kokoro":
        try:
            tts = KokoroTTS(voice=cfg.get("kokoro_voice", "bm_lewis"), speed=float(cfg.get("kokoro_speed", 1.05)), fallback=say)
        except ImportError:
            log.warning("kokoro not installed, using macOS say")
            tts = say
    elif kind == "elevenlabs":
        tts = CoreTTS(agent["core"], agent["token"], cfg.get("tts_cache", "~/.bossk/tts_cache"), voice=cfg.get("voice", ""), fallback=say)
    else:
        tts = say
    if hasattr(tts, "prerender"):
        tts.prerender(list(PHRASES.values()))
    stt = build_stt(cfg.get("stt", "mlx"), cfg.get("stt_model", ""))
    d = VoiceDaemon(core=core, capture=SoundDeviceCapture(cfg.get("mic_device")), stt=stt, tts=tts, state=state,
                    min_press_s=float(cfg.get("min_press_s", 0.3)), toggle_silence_s=float(cfg.get("toggle_silence_s", 1.2)),
                    chime=cfg.get("chime"))
    keep = []
    if cfg.get("expressions_window", True):
        keep.append(start_face(state, port=int(cfg.get("expressions_port", 8751)), on_ptt=d.post))
    if cfg.get("talk_key"):
        k = KeyTrigger(cfg["talk_key"], d.post, mode=cfg.get("talk_mode", "hold"))
        k.start()
        keep.append(k)
    return d, keep


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", default="~/.bossk/voice.yml")
    ap.add_argument("-v", "--verbose", action="store_true")
    a = ap.parse_args()
    logging.basicConfig(level=logging.DEBUG if a.verbose else logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    p = Path(a.config).expanduser()
    cfg = (yaml.safe_load(p.read_text()) if p.exists() else {}) or {}
    d, _keep = build_from_config(cfg)
    d.start()
    log.info("BOSSK voice up. Hold %s to talk.", cfg.get("talk_key", "the talk button"))
    try:
        while True:
            time.sleep(3600)
    except KeyboardInterrupt:
        d.stop()
    return 0


if __name__ == "__main__":
    sys.exit(main())
