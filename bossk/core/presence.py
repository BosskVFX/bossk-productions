"""Where is Jason? Each machine agent posts its keyboard/mouse idle time every ~30 s.

At the desk  = any machine idle for less than `away_minutes` (Synergy makes the PC and the
               Mac mini one workstation, so the shorter idle of the two wins).
Voice machine = the machine with the shortest idle that can speak (has the voice daemon).
Away         = every machine idle longer than `away_minutes`, or no report in `stale_seconds`.
"""
from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field


@dataclass
class MachineState:
    name: str
    idle_seconds: float
    can_speak: bool
    reported_at: float
    sessions: list[dict] = field(default_factory=list)
    extra: dict = field(default_factory=dict)


class Presence:
    def __init__(self, away_minutes: float = 10, stale_seconds: float = 120):
        self.away_minutes = away_minutes
        self.stale_seconds = stale_seconds
        self._m: dict[str, MachineState] = {}
        self._lock = threading.Lock()

    def report(self, name: str, idle_seconds: float, *, can_speak: bool = False, sessions: list[dict] | None = None,
               extra: dict | None = None) -> None:
        with self._lock:
            self._m[name] = MachineState(name, float(idle_seconds), bool(can_speak), time.time(),
                                         sessions or [], extra or {})

    def _fresh(self) -> list[MachineState]:
        now = time.time()
        return [m for m in self._m.values() if now - m.reported_at <= self.stale_seconds]

    def effective_idle(self, m: MachineState) -> float:
        """Idle as of now, not as of the report."""
        return m.idle_seconds + (time.time() - m.reported_at)

    def at_desk(self) -> bool:
        with self._lock:
            return any(self.effective_idle(m) < self.away_minutes * 60 for m in self._fresh())

    def voice_machine(self) -> str | None:
        """The speaking machine Jason touched last, or None when he is away."""
        with self._lock:
            fresh = self._fresh()
            if not any(self.effective_idle(m) < self.away_minutes * 60 for m in fresh):
                return None
            speakers = [m for m in fresh if m.can_speak]
            if not speakers:
                return None
            return min(speakers, key=self.effective_idle).name

    def snapshot(self) -> dict:
        with self._lock:
            return {
                "at_desk": any(self.effective_idle(m) < self.away_minutes * 60 for m in self._fresh()),
                "voice_machine": None,
                "machines": {m.name: {"idle_seconds": round(self.effective_idle(m)), "can_speak": m.can_speak,
                                      "stale": time.time() - m.reported_at > self.stale_seconds,
                                      "sessions": m.sessions, **m.extra} for m in self._m.values()},
            } | {"voice_machine": self._voice_machine_unlocked()}

    def _voice_machine_unlocked(self) -> str | None:
        fresh = self._fresh()
        if not any(self.effective_idle(m) < self.away_minutes * 60 for m in fresh):
            return None
        speakers = [m for m in fresh if m.can_speak]
        return min(speakers, key=self.effective_idle).name if speakers else None

    def sessions(self) -> list[dict]:
        with self._lock:
            out = []
            for m in self._m.values():
                for s in m.sessions:
                    out.append({"machine": m.name, **s})
            return out
