"""Speech-to-text, all local and free. Each loads its model once.

MLXWhisper  : Apple-silicon GPU (pip install mlx-whisper). Default on the Mac mini; large-v3-turbo is fast on the M4.
FasterWhisper: CTranslate2 on CPU (pip install faster-whisper). Works anywhere; what Jared's backtalk uses.
WhisperCpp  : the whisper-cli binary (brew install whisper-cpp) with a ggml model file.
"""
from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path
from typing import Protocol

PROMPT = "BOSSK, Loadout, Mother, Mac mini, ComfyUI, prompt-master, Seedance, Kling, Higgsfield. Yes. No. Confirm. Details."


class STT(Protocol):
    def transcribe(self, wav_path: str | Path) -> str: ...


def _clean(t: str) -> str:
    return re.sub(r"\s+", " ", t).strip()


class MLXWhisper:
    def __init__(self, model: str = "mlx-community/whisper-large-v3-turbo"):
        import mlx_whisper  # type: ignore
        self._m = mlx_whisper
        self.model = model

    def transcribe(self, wav_path: str | Path) -> str:
        r = self._m.transcribe(str(wav_path), path_or_hf_repo=self.model, language="en", initial_prompt=PROMPT)
        return _clean(r.get("text", ""))


class FasterWhisper:
    def __init__(self, model: str = "large-v3-turbo", compute_type: str = "int8"):
        from faster_whisper import WhisperModel  # type: ignore
        self._m = WhisperModel(model, device="auto", compute_type=compute_type)

    def transcribe(self, wav_path: str | Path) -> str:
        segments, _ = self._m.transcribe(str(wav_path), language="en", vad_filter=True, initial_prompt=PROMPT)
        return _clean(" ".join(s.text for s in segments))


class WhisperCpp:
    def __init__(self, model_path: str, binary: str | None = None, threads: int = 6):
        self.model_path = str(Path(model_path).expanduser())
        self.binary = binary or shutil.which("whisper-cli") or "whisper-cli"
        self.threads = threads

    def transcribe(self, wav_path: str | Path) -> str:
        r = subprocess.run([self.binary, "-m", self.model_path, "-f", str(wav_path), "-l", "en", "-t", str(self.threads),
                            "-nt", "-np", "--prompt", PROMPT], capture_output=True, text=True, timeout=60)
        return _clean(" ".join(l for l in r.stdout.splitlines() if l.strip() and not l.startswith("[")))


class FakeSTT:
    def __init__(self, answers: list[str] | None = None):
        self.answers = list(answers or [])
        self.calls = 0

    def transcribe(self, wav_path: str | Path) -> str:
        self.calls += 1
        return self.answers.pop(0) if self.answers else ""


def build(kind: str, model: str = "") -> STT:
    if kind == "mlx":
        return MLXWhisper(model or "mlx-community/whisper-large-v3-turbo")
    if kind == "faster-whisper":
        return FasterWhisper(model or "large-v3-turbo")
    if kind == "whisper-cpp":
        return WhisperCpp(model or "~/.bossk/models/ggml-large-v3-turbo.bin")
    raise ValueError(f"unknown stt: {kind}")
