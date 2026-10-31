"""Microphone capture and speaker playback for push-to-talk.

The mic is open only while the talk button is held (or toggled on), so BOSSK never hears its own voice:
no echo cancellation, no speakerphone needed. Playback can be cut off instantly, which is how pressing the
button interrupts BOSSK mid-sentence.
"""
from __future__ import annotations

import array
import math
import shutil
import subprocess
import threading
import time
import wave
from pathlib import Path
from typing import Callable

SAMPLE_RATE = 16000
FRAME = 512                       # 32 ms at 16 kHz
FrameCb = Callable[[list[int]], None]


def rms(frame: list[int]) -> float:
    """0..1 loudness of a frame."""
    if not frame:
        return 0.0
    return min(1.0, math.sqrt(sum(s * s for s in frame) / len(frame)) / 32768.0 * 4)


def waveform64(frame: list[int]) -> list[float]:
    """64 points of -1..1 for the face to draw."""
    if not frame:
        return [0.0] * 64
    step = max(1, len(frame) // 64)
    return [round(max(-1.0, min(1.0, frame[i * step] / 8192.0)), 3) for i in range(64) if i * step < len(frame)] + \
        [0.0] * max(0, 64 - len(range(0, len(frame), step)))


def write_wav(samples: list[int], path: str | Path, rate: int = SAMPLE_RATE) -> Path:
    p = Path(path)
    with wave.open(str(p), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(array.array("h", samples).tobytes())
    return p


class SoundDeviceCapture:
    """Real microphone through PortAudio (pip install sounddevice). Opens only between begin() and end()."""
    def __init__(self, device: int | str | None = None):
        import sounddevice  # type: ignore  # noqa: F401
        self.device = device
        self._buf: list[int] = []
        self._stream = None
        self._lock = threading.Lock()

    def begin(self, on_frame: FrameCb | None = None) -> None:
        import sounddevice as sd  # type: ignore
        self._buf = []

        def cb(indata, frames, t, status):
            f = indata[:, 0].tolist()
            with self._lock:
                self._buf.extend(f)
            if on_frame:
                on_frame(f)
        self._stream = sd.InputStream(samplerate=SAMPLE_RATE, channels=1, dtype="int16", blocksize=FRAME,
                                      device=self.device, callback=cb)
        self._stream.start()

    def end(self) -> list[int]:
        if self._stream is not None:
            self._stream.stop()
            self._stream.close()
            self._stream = None
        with self._lock:
            out, self._buf = self._buf, []
        return out


class FakeCapture:
    """Test mic: each begin() plays the next scripted utterance into on_frame at real-time pace."""
    def __init__(self, script: list[list[int]] | None = None, speed: float = 8.0):
        self.script = list(script or [])
        self.speed = speed
        self._buf: list[int] = []
        self._stop = threading.Event()
        self._t: threading.Thread | None = None
        self.opened = 0

    def begin(self, on_frame: FrameCb | None = None) -> None:
        self.opened += 1
        self._buf, self._stop = [], threading.Event()
        samples = self.script.pop(0) if self.script else []

        def feed():
            i = 0
            while not self._stop.is_set():
                f = samples[i:i + FRAME] if i < len(samples) else [0] * FRAME
                i += FRAME
                self._buf.extend(f)
                if on_frame:
                    on_frame(f)
                time.sleep(FRAME / SAMPLE_RATE / self.speed)
        self._t = threading.Thread(target=feed, daemon=True)
        self._t.start()

    def end(self) -> list[int]:
        self._stop.set()
        if self._t:
            self._t.join(1)
        out, self._buf = self._buf, []
        return out


def loud(seconds: float = 0.6, amp: int = 8000) -> list[int]:
    return [int(amp * math.sin(i * 0.3)) for i in range(int(seconds * SAMPLE_RATE))]


def quiet(seconds: float = 1.0) -> list[int]:
    return [0] * int(seconds * SAMPLE_RATE)


class Player:
    """afplay on macOS, ffplay/aplay elsewhere. play() blocks until done or stop() is called.
    For WAV files it also feeds a loudness envelope and waveform to `level_cb` so the face moves with the voice."""
    def __init__(self):
        self._proc: subprocess.Popen | None = None
        self._lock = threading.Lock()

    def _cmd(self, path: str) -> list[str] | None:
        if shutil.which("afplay"):
            return ["afplay", path]
        if shutil.which("ffplay"):
            return ["ffplay", "-nodisp", "-autoexit", "-loglevel", "quiet", path]
        if shutil.which("aplay") and path.endswith(".wav"):
            return ["aplay", "-q", path]
        return None

    def play(self, path: str | Path, level_cb: Callable[[float, list[float]], None] | None = None) -> bool:
        """Returns False if interrupted."""
        cmd = self._cmd(str(path))
        if not cmd:
            return True
        env = _envelope(Path(path)) if level_cb else []
        with self._lock:
            self._proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            proc = self._proc
        t0 = time.time()
        while proc.poll() is None:
            if level_cb:
                if env:
                    i = int((time.time() - t0) * 30)
                    lvl, wf = env[i] if i < len(env) else (0.0, [0.0] * 64)
                else:
                    lvl, wf = 0.5 + 0.2 * math.sin((time.time() - t0) * 9), [0.0] * 64
                level_cb(lvl, wf)
            time.sleep(1 / 30)
        if level_cb:
            level_cb(0.0, [0.0] * 64)
        return proc.returncode == 0

    def stop(self) -> None:
        with self._lock:
            if self._proc and self._proc.poll() is None:
                self._proc.kill()


def _envelope(path: Path) -> list[tuple[float, list[float]]]:
    if path.suffix.lower() != ".wav":
        return []
    try:
        with wave.open(str(path), "rb") as w:
            rate, n, width = w.getframerate(), w.getnframes(), w.getsampwidth()
            data = w.readframes(n)
        if width != 2:
            return []
        s = array.array("h", data).tolist()
        hop = max(1, rate // 30)
        return [(rms(s[i:i + hop]), waveform64(s[i:i + hop])) for i in range(0, len(s), hop)]
    except (OSError, wave.Error, EOFError):
        return []
