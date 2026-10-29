"""Wake word backends. Porcupine (on-device, custom "Hey BOSSK" keyword from the Picovoice console)
is the one for the Mac mini. KeyboardWake is for development: press Enter to "say" the wake word."""
from __future__ import annotations

import sys
import threading
from typing import Protocol


class Wake(Protocol):
    def process(self, frame: list[int]) -> bool: ...     # True when the wake word fired on this frame
    def close(self) -> None: ...


class PorcupineWake:
    def __init__(self, access_key: str, keyword_path: str | None = None, keyword: str = "jarvis", sensitivity: float = 0.6):
        import pvporcupine  # type: ignore
        if keyword_path:
            self._p = pvporcupine.create(access_key=access_key, keyword_paths=[keyword_path], sensitivities=[sensitivity])
        else:
            # until the custom "Hey BOSSK" .ppn is trained, a built-in keyword keeps the loop testable on real hardware
            self._p = pvporcupine.create(access_key=access_key, keywords=[keyword], sensitivities=[sensitivity])
        self.frame_length = self._p.frame_length

    def process(self, frame: list[int]) -> bool:
        return self._p.process(frame) >= 0

    def close(self) -> None:
        self._p.delete()


class KeyboardWake:
    """Enter in the terminal = wake word. Runs a reader thread so process() stays non-blocking."""
    def __init__(self):
        self._fired = threading.Event()
        threading.Thread(target=self._reader, daemon=True).start()

    def _reader(self):
        for _ in sys.stdin:
            self._fired.set()

    def process(self, frame: list[int]) -> bool:
        if self._fired.is_set():
            self._fired.clear()
            return True
        return False

    def close(self) -> None:
        pass


class FakeWake:
    def __init__(self):
        self.pending = 0

    def fire(self):
        self.pending += 1

    def process(self, frame: list[int]) -> bool:
        if self.pending:
            self.pending -= 1
            return True
        return False

    def close(self) -> None:
        pass
