"""Push-to-talk triggers. All of them just post events to the daemon:
  down / up   : hold-to-talk (a key or a foot pedal held down)
  toggle      : tap to start, tap to stop (Stream Deck); stops itself after a pause in speech
  stop        : shut BOSSK up

KeyTrigger listens for one key on the Mac mini (pip install pynput; grant Input Monitoring and
Accessibility to the Python that runs it). Use a key nothing else uses, like F19 from a Stream Deck,
a foot pedal, or a spare macro key. Synergy note: plug the button into the Mac mini itself, so it works
wherever the shared mouse happens to be.
HTTP triggers are served by the expressions server on 127.0.0.1:8751 (POST /ptt/down, /ptt/up, /ptt/toggle, /ptt/stop),
for Stream Deck HTTP-request buttons.
"""
from __future__ import annotations

from typing import Callable

Post = Callable[[str], None]


class KeyTrigger:
    def __init__(self, key: str, post: Post, mode: str = "hold"):
        from pynput import keyboard  # type: ignore
        self._kb = keyboard
        self.target = self._parse(key)
        self.post, self.mode = post, mode
        self.held = False
        self._listener = keyboard.Listener(on_press=self._press, on_release=self._release)

    def _parse(self, key: str):
        k = key.lower()
        if hasattr(self._kb.Key, k):
            return getattr(self._kb.Key, k)
        return self._kb.KeyCode.from_char(k)

    def _match(self, key) -> bool:
        return key == self.target or getattr(key, "char", None) == getattr(self.target, "char", object())

    def _press(self, key):
        if not self._match(key) or self.held:
            return
        self.held = True
        self.post("toggle" if self.mode == "toggle" else "down")

    def _release(self, key):
        if not self._match(key):
            return
        self.held = False
        if self.mode != "toggle":
            self.post("up")

    def start(self) -> None:
        self._listener.start()

    def stop(self) -> None:
        self._listener.stop()
