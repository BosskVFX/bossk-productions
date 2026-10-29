"""The reviewer agent: a second model that reads a tier 2 request and the session's recent
context, then allows, denies, or escalates. It also writes the plain-language ask for tier 3.

The reviewer never sits in the tier 1 path. When the Anthropic SDK or the key is missing it
escalates everything, so BOSSK degrades to "ask Jason", never to "allow".
"""
from __future__ import annotations

import json
import re
from typing import Any

try:
    import anthropic  # type: ignore
except ImportError:  # pragma: no cover
    anthropic = None

REVIEW_SYSTEM = """You are the reviewer inside BOSSK, a supervisor for Jason Miller's Claude Code sessions.
A session wants to run a tool call that the rulebook did not classify. Decide: allow, deny, or escalate to Jason.

Allow only when the call is clearly routine for the session's stated task, reversible, inside the project,
and spends no money, sends nothing to a person, and deletes nothing outside build caches.
Deny only when it is clearly harmful or clearly outside the task. Escalate everything else.

Reply with JSON only: {"verdict":"allow"|"deny"|"escalate","reason":"<one sentence>",
"ask":"<if escalate: one spoken sentence for Jason stating exactly what, where, how much>"}"""

ASK_SYSTEM = """You write the spoken question BOSSK asks Jason before a risky action. One or two sentences,
plain words, no code. State exactly what will happen: the count, size, path, recipient or dollar amount.
If a confirm step is needed, also write what BOSSK repeats before asking him to say confirm.
Reply with JSON only: {"ask":"...","confirm_text":"..."}"""


def _describe(tool: str, tool_input: dict[str, Any]) -> str:
    if tool_input.get("command"):
        return f"shell: {tool_input['command']}"
    if tool_input.get("file_path"):
        return f"{tool}: {tool_input['file_path']}"
    return f"{tool}: {json.dumps(tool_input)[:400]}"


def template_ask(tool: str, tool_input: dict[str, Any], *, machine: str, session_name: str, reason: str,
                 context: dict[str, Any] | None = None) -> tuple[str, str]:
    """Deterministic fallback for the ask text when the reviewer is unavailable."""
    ctx = context or {}
    if ctx.get("summary"):
        ask = ctx["summary"]
    else:
        ask = f"Excuse me Jason. The {session_name or 'Claude Code'} session on {machine} wants to run {_describe(tool, tool_input)}. The rule says {reason}. Yes or no?"
    confirm = ctx.get("confirm_text") or f"To be clear, that is {_describe(tool, tool_input)}. Say confirm to go ahead."
    return ask, confirm


class Reviewer:
    def __init__(self, api_key: str | None, model: str = "claude-sonnet-5-5", enabled: bool = True):
        self.model = model
        self.client = anthropic.Anthropic(api_key=api_key) if (enabled and anthropic and api_key) else None

    @property
    def available(self) -> bool:
        return self.client is not None

    def _ask_model(self, system: str, user: str, max_tokens: int = 400) -> dict[str, Any] | None:
        if not self.client:
            return None
        try:
            msg = self.client.messages.create(model=self.model, max_tokens=max_tokens, system=system,
                                              messages=[{"role": "user", "content": user}])
            text = "".join(getattr(b, "text", "") for b in msg.content)
            m = re.search(r"\{.*\}", text, re.S)
            return json.loads(m.group(0)) if m else None
        except Exception:  # noqa: BLE001
            return None

    def review(self, *, tool: str, tool_input: dict[str, Any], cwd: str | None, machine: str,
               session_name: str, transcript_tail: str) -> dict[str, Any]:
        user = (f"Machine: {machine}\nSession: {session_name}\nWorking directory: {cwd}\n"
                f"Requested call: {_describe(tool, tool_input)}\n\nRecent session context:\n{transcript_tail[-6000:]}")
        out = self._ask_model(REVIEW_SYSTEM, user)
        if not out or out.get("verdict") not in ("allow", "deny", "escalate"):
            return {"verdict": "escalate", "reason": "reviewer unavailable or unclear", "ask": ""}
        return {"verdict": out["verdict"], "reason": str(out.get("reason", ""))[:300], "ask": str(out.get("ask", ""))}

    def write_ask(self, *, tool: str, tool_input: dict[str, Any], machine: str, session_name: str, reason: str,
                  confirm: bool, context: dict[str, Any] | None, transcript_tail: str = "") -> tuple[str, str]:
        if context and context.get("summary"):
            return template_ask(tool, tool_input, machine=machine, session_name=session_name, reason=reason, context=context)
        user = (f"Machine: {machine}\nSession: {session_name}\nRule reason: {reason}\nConfirm step: {confirm}\n"
                f"Requested call: {_describe(tool, tool_input)}\nExtra context: {json.dumps(context or {})[:800]}\n"
                f"Recent session context:\n{transcript_tail[-3000:]}")
        out = self._ask_model(ASK_SYSTEM, user, max_tokens=300)
        if out and out.get("ask"):
            return str(out["ask"]), str(out.get("confirm_text") or "")
        return template_ask(tool, tool_input, machine=machine, session_name=session_name, reason=reason, context=context)
