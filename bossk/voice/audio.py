"""Microphone capture and playback for the voice daemon.

Capture uses pvrecorder (Picovoice's recorder, 16 kHz mono int16 frames) because Porcupine wants
exactly that stream, and the same stream is reused to record the answer after the wake word.
Playback shells out to afplay on macOS (ffplay elsewhere). Both are behind small classes so tests
can swap in fakes.
"""
from __future__ import annotations

import array
import math
import shutil
import struct
import subprocess
import tempfile
import time
import wave
from pathlib import Path
from typing import Iterator, Protocol

SAMPLE_RATE = 16000
FRAME_LENGTH = 512          # Porcupine's frame length at 16 kHz


class Recorder(Protocol):
    def start(self) -> None: ...
    def stop(self) -> None: ...
    def read(self) -> list[int]: ...          # one frame of FRAME_LENGTH int16 samples


class PvRecorder:
    def __init__(self, device_index: int = -1):
        from pvrecorder import PvRecorder as _R  # type: ignore
        self._r = _R(frame_length=FRAME_LENGTH, device_index=device_index)

    def start(self) -> None:
        self._r.start()

    def stop(self) -> None:
        self._r.stop()

    def read(self) -> list[int]:
        return list(self._r.read())


def rms(frame: list[int]) -> float:
    """0..1 loudness of one frame, used for end-of-speech detection and the expressions level."""
    if not frame:
        return 0.0
    return min(1.0, math.sqrt(sum(s * s for s in frame) / len(frame)) / 32768.0 * 4)


def record_utterance(rec: Recorder, *, max_seconds: float = 8.0, silence_seconds: float = 0.9, start_threshold: float = 0.04,
                     level_cb=None, already_started: bool = True) -> list[int]:
    """Read frames until `silence_seconds` of quiet after speech, or `max_seconds`. Returns int16 samples."""
    frames_per_s = SAMPLE_RATE / FRAME_LENGTH
    max_frames = int(max_seconds * frames_per_s)
    silence_frames = int(silence_seconds * frames_per_s)
    out: list[int] = []
    quiet, spoke, n = 0, False, 0
    while n < max_frames:
        f = rec.read()
        n += 1
        out.extend(f)
        lvl = rms(f)
        if level_cb:
            level_cb(lvl)
        if lvl >= start_threshold:
            spoke, quiet = True, 0
        elif spoke:
            quiet += 1
            if quiet >= silence_frames:
                break
        elif n > frames_per_s * 3 and not spoke:   # 3 s of nothing: he isn't answering
            break
    return out


def write_wav(samples: list[int], path: str | Path) -> Path:
    p = Path(path)
    with wave.open(str(p), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(SAMPLE_RATE)
        w.writeframes(array.array("h", samples).tobytes())
    return p


class Player:
    def play(self, audio_path: str | Path) -> None:
        if shutil.which("afplay"):
            subprocess.run(["afplay", str(audio_path)], check=False)
        elif shutil.which("ffplay"):
            subprocess.run(["ffplay", "-nodisp", "-autoexit", "-loglevel", "quiet", str(audio_path)], check=False)
        elif shutil.which("aplay"):
            subprocess.run(["aplay", "-q", str(audio_path)], check=False)

    def duration_s(self, audio_path: str | Path) -> float:
        if shutil.which("ffprobe"):
            r = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(audio_path)],
                               capture_output=True, text=True)
            try:
                return float(r.stdout.strip())
            except ValueError:
                pass
        return 0.0


class FakeRecorder:
    """Feeds scripted utterances: each item is a list of samples; a loud burst followed by silence."""
    def __init__(self, utterances: list[list[int]] | None = None):
        self.queue = list(utterances or [])
        self.started = False

    def start(self) -> None:
        self.started = True

    def stop(self) -> None:
        self.started = False

    def read(self) -> list[int]:
        if not self.queue:
            return [0] * FRAME_LENGTH
        cur = self.queue[0]
        frame, rest = cur[:FRAME_LENGTH], cur[FRAME_LENGTH:]
        if rest:
            self.queue[0] = rest
        else:
            self.queue.pop(0)
        return frame + [0] * (FRAME_LENGTH - len(frame))


def loud(seconds: float = 0.6, amp: int = 8000) -> list[int]:
    n = int(seconds * SAMPLE_RATE)
    return [int(amp * math.sin(i * 0.3)) for i in range(n)]


def quiet(seconds: float = 1.2) -> list[int]:
    return [0] * int(seconds * SAMPLE_RATE)
