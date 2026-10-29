"""Speech-to-text backends.

WhisperCpp: shells out to whisper-cli (brew install whisper-cpp) with a ggml model. On the M4 with the
Core ML encoder, large-v3-turbo transcribes a short answer in well under a second.
AppleSpeech: macOS on-device recognizer through a tiny Swift helper (not shipped yet); WhisperCpp is the default.
"""
from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path
from typing import Protocol


class STT(Protocol):
    def transcribe(self, wav_path: str | Path) -> str: ...


class WhisperCpp:
    def __init__(self, model_path: str, binary: str | None = None, language: str = "en", threads: int = 6):
        self.model_path = str(Path(model_path).expanduser())
        self.binary = binary or shutil.which("whisper-cli") or shutil.which("whisper-cpp") or shutil.which("main") or "whisper-cli"
        self.language = language
        self.threads = threads

    def transcribe(self, wav_path: str | Path) -> str:
        cmd = [self.binary, "-m", self.model_path, "-f", str(wav_path), "-l", self.language, "-t", str(self.threads),
               "-nt", "-np", "--no-prints"] if "whisper-cli" in self.binary else \
              [self.binary, "-m", self.model_path, "-f", str(wav_path), "-l", self.language, "-t", str(self.threads), "-nt", "-np"]
        try:
            r = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
        except (OSError, subprocess.TimeoutExpired) as e:
            raise RuntimeError(f"whisper failed: {e}") from None
        text = " ".join(line.strip() for line in r.stdout.splitlines() if line.strip() and not line.startswith("["))
        return re.sub(r"\s+", " ", text).strip()


class FakeSTT:
    def __init__(self, answers: list[str] | None = None):
        self.answers = list(answers or [])
        self.calls = 0

    def transcribe(self, wav_path: str | Path) -> str:
        self.calls += 1
        return self.answers.pop(0) if self.answers else ""
