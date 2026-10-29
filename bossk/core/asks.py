"""Pending asks: the tier 3 questions waiting on Jason.

An ask is created by the server when a decision needs a human. It is dispatched to the
voice machine (the voice daemon long-polls for asks addressed to it) or to the phone (ntfy
with Yes / No buttons that POST back here). Whoever answers first wins, the other channel is
told to drop it. Nothing ever times out into a yes.

Confirm tier: for money and outbound messages the first "yes" moves the ask to
`awaiting_confirm`; BOSSK repeats the figure or recipient and only "confirm" completes it.
"""
from __future__ import annotations

import secrets
import threading
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Callable

YES = {"yes", "y", "yeah", "yep", "yup", "approve", "approved", "go", "go ahead", "do it", "ok", "okay", "sure"}
NO = {"no", "n", "nope", "deny", "denied", "stop", "don't", "dont", "cancel", "negative", "nah"}
CONFIRM = {"confirm", "confirmed", "i confirm", "yes confirm", "confirm it"}


def classify(text: str) -> str:
    """yes | no | confirm | unclear, from a spoken or typed answer. Leads with the first word,
    so "yeah go ahead" is a yes and "no, cancel it" is a no. "confirm" anywhere (not after a no) confirms."""
    t = " ".join(text.lower().replace(",", " ").replace(".", " ").replace("!", " ").split())
    if not t:
        return "unclear"
    words = t.split()
    first = words[0]
    if first in NO:
        return "no"
    if "confirm" in words or "confirmed" in words:
        return "confirm"
    if first in YES or t in YES or t.startswith("go ahead"):
        return "yes"
    if t in NO:
        return "no"
    return "unclear"


@dataclass
class Ask:
    id: str
    created_at: float
    machine: str
    session_id: str
    tool: str
    tool_input: dict[str, Any]
    tier: int
    rule_id: str | None
    text: str                               # what BOSSK says / shows
    confirm: bool                           # needs "confirm" after "yes"
    confirm_text: str = ""                  # what BOSSK repeats before asking for confirm
    status: str = "pending"                 # pending | awaiting_confirm | yes | no | expired | cancelled
    channel: str = ""                       # voice:<machine> | phone
    answer_token: str = ""                  # one-time token baked into the phone buttons
    answered_by: str = ""
    answered_at: float | None = None
    context: dict[str, Any] = field(default_factory=dict)
    cond: threading.Condition = field(default_factory=threading.Condition, repr=False, compare=False)

    def public(self) -> dict:
        return {k: getattr(self, k) for k in ("id", "created_at", "machine", "session_id", "tool", "tool_input", "tier",
                                              "rule_id", "text", "confirm", "confirm_text", "status", "channel",
                                              "answered_by", "answered_at", "context")}


class AskQueue:
    def __init__(self):
        self._asks: dict[str, Ask] = {}
        self._lock = threading.Lock()
        self.on_change: list[Callable[[Ask], None]] = []

    def create(self, *, machine: str, session_id: str, tool: str, tool_input: dict, tier: int, rule_id: str | None,
               text: str, confirm: bool, confirm_text: str = "", channel: str = "", context: dict | None = None) -> Ask:
        ask = Ask(id=uuid.uuid4().hex[:12], created_at=time.time(), machine=machine, session_id=session_id,
                  tool=tool, tool_input=tool_input, tier=tier, rule_id=rule_id, text=text, confirm=confirm,
                  confirm_text=confirm_text, channel=channel, answer_token=secrets.token_urlsafe(24),
                  context=context or {})
        with self._lock:
            self._asks[ask.id] = ask
        return ask

    def get(self, ask_id: str) -> Ask | None:
        with self._lock:
            return self._asks.get(ask_id)

    def pending(self, *, channel_prefix: str | None = None) -> list[Ask]:
        with self._lock:
            out = [a for a in self._asks.values() if a.status in ("pending", "awaiting_confirm")]
        if channel_prefix:
            out = [a for a in out if a.channel.startswith(channel_prefix) or not a.channel]
        return sorted(out, key=lambda a: a.created_at)

    def answer(self, ask_id: str, text: str, *, by: str, token: str | None = None) -> tuple[str, Ask | None]:
        """Returns (result, ask). result: yes | no | awaiting_confirm | unclear | not_found | closed | bad_token."""
        ask = self.get(ask_id)
        if not ask:
            return "not_found", None
        if token is not None and token != ask.answer_token:
            return "bad_token", ask
        kind = classify(text)
        with ask.cond:
            if ask.status not in ("pending", "awaiting_confirm"):
                return "closed", ask
            if kind == "no":
                ask.status, ask.answered_by, ask.answered_at = "no", by, time.time()
                ask.cond.notify_all()
                result = "no"
            elif kind == "yes":
                if ask.confirm and ask.status == "pending":
                    ask.status = "awaiting_confirm"
                    result = "awaiting_confirm"
                elif ask.confirm and ask.status == "awaiting_confirm":
                    result = "awaiting_confirm"   # a second plain yes is not a confirm
                else:
                    ask.status, ask.answered_by, ask.answered_at = "yes", by, time.time()
                    ask.cond.notify_all()
                    result = "yes"
            elif kind == "confirm":
                if ask.confirm and ask.status == "awaiting_confirm":
                    ask.status, ask.answered_by, ask.answered_at = "yes", by, time.time()
                    ask.cond.notify_all()
                    result = "yes"
                elif not ask.confirm:
                    ask.status, ask.answered_by, ask.answered_at = "yes", by, time.time()
                    ask.cond.notify_all()
                    result = "yes"
                else:
                    result = "awaiting_confirm"
            else:
                result = "unclear"
        for cb in self.on_change:
            try:
                cb(ask)
            except Exception:  # noqa: BLE001
                pass
        return result, ask

    def wait(self, ask_id: str, timeout: float) -> str:
        """Block until the ask is answered or `timeout` seconds pass. Returns its status."""
        ask = self.get(ask_id)
        if not ask:
            return "not_found"
        deadline = time.time() + timeout
        with ask.cond:
            while ask.status in ("pending", "awaiting_confirm"):
                remaining = deadline - time.time()
                if remaining <= 0:
                    break
                ask.cond.wait(remaining)
            return ask.status

    def cancel(self, ask_id: str, reason: str = "cancelled") -> bool:
        ask = self.get(ask_id)
        if not ask:
            return False
        with ask.cond:
            if ask.status in ("pending", "awaiting_confirm"):
                ask.status = reason
                ask.cond.notify_all()
                return True
        return False

    def expire(self, older_than_seconds: float) -> list[Ask]:
        cutoff = time.time() - older_than_seconds
        out = []
        for a in self.pending():
            if a.created_at < cutoff and self.cancel(a.id, "expired"):
                out.append(a)
        return out
