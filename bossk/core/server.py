"""BOSSK core HTTP API. Standard library only, so the TrueNAS container is python + ffmpeg.

Auth: every route needs `Authorization: Bearer <BOSSK_TOKEN>` except the ntfy button callbacks
under /a/<ask id>/<one-time token>/<answer>, which carry their own token.

Routes
  GET  /health                         state summary
  POST /decide                         a permission request from a machine agent (blocks up to wait_seconds)
  POST /log                            non-blocking record from a PreToolUse hook
  POST /idle                           machine idle report + live sessions
  POST /events                         Notification / Stop / SessionStart hook events
  GET  /asks?channel=voice:mini&wait=25   pending asks (long-poll)
  POST /asks/<id>/answer  {text, by}   answer from the voice daemon or the app
  POST /a/<id>/<token>/<yes|no|confirm>   ntfy button callback
  POST /pause {reason} · POST /unpause global pause
  GET  /sessions                       every live session across machines
  GET  /ledger?since=<epoch>&kind=&limit=
  POST /notify {title, body, priority} send to the phone through the core
  POST /rulebook/replay {rules:<yaml text>, days:30}
  POST /rulebook/reload
  POST /chat {text, machine, stream?}   spoken answer (Claude Code on the subscription, live state + knowledge folder);
                                        stream=true returns NDJSON lines {"text": sentence} ... {"done": true}
  POST /tts  {text, voice?}             ElevenLabs audio (audio/mpeg) or 204 when no key, so the mini falls back to `say`
"""
from __future__ import annotations

import json
import logging
import threading
import time
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

import yaml

from .asks import AskQueue, Ask
from .assistant import Assistant, ElevenLabsTTS
from .claude_cli import ClaudeCLI
from .config import CoreConfig
from .ledger import Ledger
from .notify import Action, NotifyConfig, notify
from .presence import Presence
from .reviewer import Reviewer
from .rulebook import Decision, Rulebook
from .secrets import Secrets

log = logging.getLogger("bossk.core")


class Core:
    def __init__(self, cfg: CoreConfig, *, token: str, secrets: Secrets | None = None, reviewer: Reviewer | None = None,
                 notify_fn=notify, assistant: Assistant | None = None):
        self.cfg = cfg
        self.token = token
        self.secrets = secrets or Secrets()
        Path(cfg.data_dir).mkdir(parents=True, exist_ok=True)
        self.ledger = Ledger(cfg.ledger)
        self.rulebook = Rulebook.load(cfg.rulebook)
        self.presence = Presence(away_minutes=float(self.rulebook.defaults.get("idle_minutes_away", 10)),
                                 stale_seconds=cfg.stale_machine_seconds)
        self.asks = AskQueue()
        self.cli = ClaudeCLI(binary=cfg.claude_binary or None, oauth_token=self.secrets.get("CLAUDE_CODE_OAUTH_TOKEN"),
                             model=cfg.reviewer_model, cwd=cfg.knowledge_dir or None)
        self.reviewer = reviewer or Reviewer(self.cli, cfg.reviewer_model, cfg.reviewer_enabled)
        self.notify_cfg = NotifyConfig.from_dict({k: (self.secrets.get(v[1:]) if isinstance(v, str) and v.startswith("$") else v)
                                                  for k, v in cfg.notify.items()})
        self._notify = notify_fn
        self.paused: str | None = None
        self.assistant = assistant or Assistant(self.cli, cfg.voice_model)
        self.tts = ElevenLabsTTS(self.secrets.get("ELEVENLABS_API_KEY"), self.secrets.get("ELEVENLABS_VOICE_ID") or cfg.voice_id,
                                 cache_dir=Path(cfg.data_dir) / "tts_cache")
        self._lock = threading.Lock()
        self.asks.on_change.append(self._on_ask_change)

    # ------------------------------------------------------------------ decisions
    def decide(self, body: dict[str, Any]) -> dict[str, Any]:
        machine = body.get("machine") or "unknown"
        session_id = body.get("session_id") or ""
        session_name = body.get("session_name") or body.get("cwd", "").rstrip("/").split("/")[-1] or session_id[:8]
        tool = body.get("tool_name") or ""
        tool_input = body.get("tool_input") or {}
        cwd = body.get("cwd")
        tail = body.get("transcript_tail") or ""
        wait = min(float(body.get("wait_seconds") or self.cfg.max_wait_seconds), float(self.cfg.max_wait_seconds))
        context = body.get("context") or {}

        d = self.rulebook.decide(tool, tool_input, cwd)
        if body.get("force_tier") == 3:        # jobs that want a deliberate ask (the NAS purge) regardless of rules
            d = Decision(tier=3, rule_id="forced", reason=str(body.get("reason") or "job requested an ask"),
                         confirm=bool(body.get("confirm")))
        self.ledger.record("prompt", machine=machine, session_id=session_id, tool=tool, tier=d.tier, rule_id=d.rule_id,
                           summary=_short(tool, tool_input),
                           data={"tool_name": tool, "tool_input": tool_input, "cwd": cwd, "session_name": session_name})

        if self.paused:
            return self._escalate(machine, session_id, session_name, tool, tool_input, 3, "paused",
                                  f"BOSSK is paused: {self.paused}", False, context, tail, wait)
        if d.tier == 0:
            return self._finish(machine, session_id, tool, d.tier, d.rule_id, "deny", f"never: {d.reason}")
        if d.tier == 1:
            return self._finish(machine, session_id, tool, d.tier, d.rule_id, "allow", d.reason)
        if d.tier == 2:
            r = self.reviewer.review(tool=tool, tool_input=tool_input, cwd=cwd, machine=machine,
                                     session_name=session_name, transcript_tail=tail)
            self.ledger.record("review", machine=machine, session_id=session_id, tool=tool, tier=2,
                               outcome=r["verdict"], summary=r["reason"], data=r)
            if r["verdict"] in ("allow", "deny"):
                return self._finish(machine, session_id, tool, 2, d.rule_id, r["verdict"], f"reviewer: {r['reason']}")
            if r.get("ask") and not context.get("summary"):
                context = {**context, "summary": r["ask"]}
            return self._escalate(machine, session_id, session_name, tool, tool_input, 2, d.rule_id,
                                  f"reviewer: {r['reason']}", False, context, tail, wait)
        return self._escalate(machine, session_id, session_name, tool, tool_input, 3, d.rule_id, d.reason, d.confirm,
                              context, tail, wait)

    def _finish(self, machine, session_id, tool, tier, rule_id, behavior, reason) -> dict[str, Any]:
        self.ledger.record("decision", machine=machine, session_id=session_id, tool=tool, tier=tier, rule_id=rule_id,
                           outcome=behavior, summary=reason)
        return {"behavior": behavior, "tier": tier, "rule_id": rule_id, "reason": reason}

    def _escalate(self, machine, session_id, session_name, tool, tool_input, tier, rule_id, reason, confirm,
                  context, tail, wait) -> dict[str, Any]:
        ask_text, confirm_text = self.reviewer.write_ask(tool=tool, tool_input=tool_input, machine=machine,
                                                         session_name=session_name, reason=reason, confirm=confirm,
                                                         context=context, transcript_tail=tail)
        ask = self.asks.create(machine=machine, session_id=session_id, tool=tool, tool_input=tool_input, tier=tier,
                               rule_id=rule_id, text=ask_text, confirm=confirm, confirm_text=confirm_text,
                               context={**context, "session_name": session_name, "reason": reason})
        self.ledger.record("ask", machine=machine, session_id=session_id, tool=tool, tier=tier, rule_id=rule_id,
                           outcome="pending", summary=ask_text, data={"ask_id": ask.id, "confirm": confirm})
        self._dispatch(ask)
        status = self.asks.wait(ask.id, wait) if wait > 0 else ask.status
        if status == "yes":
            return {**self._finish(machine, session_id, tool, tier, rule_id, "allow", f"Jason said yes ({ask.answered_by})"), "ask_id": ask.id}
        if status == "no":
            return {**self._finish(machine, session_id, tool, tier, rule_id, "deny", f"Jason said no ({ask.answered_by})"), "ask_id": ask.id}
        return {"behavior": "pending", "tier": tier, "rule_id": rule_id, "reason": reason, "ask_id": ask.id, "ask": ask_text}

    # ------------------------------------------------------------------ dispatch
    def _dispatch(self, ask: Ask) -> None:
        vm = self.presence.voice_machine()
        if vm:
            ask.channel = f"voice:{vm}"
            self.ledger.record("dispatch", machine=vm, session_id=ask.session_id, outcome="voice", summary=ask.text,
                               data={"ask_id": ask.id})
            threading.Timer(self.cfg.voice_grace_seconds, self._phone_fallback, args=(ask.id,)).start()
        else:
            self._send_phone(ask)

    def _phone_fallback(self, ask_id: str) -> None:
        ask = self.asks.get(ask_id)
        if ask and ask.status in ("pending", "awaiting_confirm") and not ask.channel.startswith("phone"):
            self._send_phone(ask)

    def _send_phone(self, ask: Ask) -> None:
        ask.channel = "phone" if not ask.channel else ask.channel + "+phone"
        base = f"{self.cfg.public_url.rstrip('/')}/a/{ask.id}/{ask.answer_token}"
        actions = [Action("Yes", base + "/yes"), Action("No", base + "/no")]
        if ask.confirm:
            actions.append(Action("Confirm", base + "/confirm"))
        res = self._notify("BOSSK needs you", ask.text, priority=4, tags=["question"], actions=actions,
                           config=self.notify_cfg)
        self.ledger.record("dispatch", machine=ask.machine, session_id=ask.session_id, outcome="phone",
                           summary=ask.text, data={"ask_id": ask.id, "notify": res})

    def _on_ask_change(self, ask: Ask) -> None:
        if ask.status in ("yes", "no"):
            self.ledger.record("answer", machine=ask.machine, session_id=ask.session_id, tool=ask.tool, tier=ask.tier,
                               rule_id=ask.rule_id, outcome=ask.status, summary=ask.text,
                               data={"ask_id": ask.id, "by": ask.answered_by})

    # ------------------------------------------------------------------ misc
    def pause(self, reason: str) -> None:
        self.paused = reason or "paused"
        self.ledger.record("pause", outcome="on", summary=self.paused)

    def unpause(self) -> None:
        self.ledger.record("pause", outcome="off", summary=self.paused or "")
        self.paused = None

    def reload_rulebook(self) -> int:
        self.rulebook = Rulebook.load(self.cfg.rulebook)
        self.ledger.record("rulebook", outcome="reload", summary=self.cfg.rulebook)
        return len(self.rulebook.rules)

    def replay(self, rules_yaml: str, days: int) -> dict[str, Any]:
        candidate = Rulebook.from_dict(yaml.safe_load(rules_yaml) or {})
        prompts = list(self.ledger.prompts(time.time() - days * 86400))
        return candidate.replay(prompts, self.rulebook)

    def health(self) -> dict[str, Any]:
        return {"ok": True, "paused": self.paused, "presence": self.presence.snapshot(),
                "pending_asks": [a.public() for a in self.asks.pending()],
                "reviewer": self.reviewer.available, "notify": self.notify_cfg.enabled(),
                "rules": len(self.rulebook.rules), "time": time.time()}


def _short(tool: str, ti: dict[str, Any]) -> str:
    for k in ("command", "file_path", "url", "path"):
        if ti.get(k):
            return f"{tool} {str(ti[k])[:200]}"
    return tool


# ---------------------------------------------------------------------- HTTP layer
class Handler(BaseHTTPRequestHandler):
    core: Core  # set on the class by serve()

    def log_message(self, fmt, *args):  # quieter
        log.debug("%s " + fmt, self.address_string(), *args)

    def _json(self, code: int, obj: Any) -> None:
        data = json.dumps(obj, default=str).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _body(self) -> dict[str, Any]:
        n = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(n) if n else b""
        if not raw:
            return {}
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            return {"_raw": raw.decode(errors="replace")}

    def _authed(self) -> bool:
        auth = self.headers.get("Authorization", "")
        return auth == f"Bearer {self.core.token}"

    def do_GET(self):
        url = urllib.parse.urlparse(self.path)
        q = {k: v[0] for k, v in urllib.parse.parse_qs(url.query).items()}
        if not self._authed():
            return self._json(401, {"error": "unauthorized"})
        if url.path == "/health":
            return self._json(200, self.core.health())
        if url.path == "/asks":
            channel = q.get("channel", "all")
            wait = min(float(q.get("wait", 0)), 60)
            deadline = time.time() + wait
            while True:
                pending = self.core.asks.pending(channel_prefix=None if channel == "all" else channel)
                if pending or time.time() >= deadline:
                    break
                time.sleep(0.5)
            return self._json(200, {"asks": [a.public() for a in pending]})
        if url.path == "/sessions":
            return self._json(200, {"sessions": self.core.presence.sessions()})
        if url.path == "/ledger":
            since = float(q["since"]) if q.get("since") else time.time() - 86400
            return self._json(200, {"events": self.core.ledger.query(since=since, kind=q.get("kind"),
                                                                     limit=int(q.get("limit", 200)))})
        return self._json(404, {"error": "not found"})

    def do_POST(self):
        url = urllib.parse.urlparse(self.path)
        parts = [p for p in url.path.split("/") if p]
        # ntfy button callback: /a/<ask id>/<token>/<answer>, no bearer
        if len(parts) == 4 and parts[0] == "a":
            result, _ = self.core.asks.answer(parts[1], parts[3], by="phone", token=parts[2])
            return self._json(200 if result in ("yes", "no", "awaiting_confirm") else 400, {"result": result})
        if not self._authed():
            return self._json(401, {"error": "unauthorized"})
        body = self._body()
        try:
            if url.path == "/decide":
                return self._json(200, self.core.decide(body))
            if url.path == "/log":
                self.core.ledger.record(body.get("kind", "log"), machine=body.get("machine"), session_id=body.get("session_id"),
                                        tool=body.get("tool_name"), summary=_short(body.get("tool_name", ""), body.get("tool_input") or {}),
                                        data=body)
                return self._json(200, {"ok": True})
            if url.path == "/idle":
                self.core.presence.report(body.get("machine", "unknown"), float(body.get("idle_seconds", 0)),
                                          can_speak=bool(body.get("can_speak")), sessions=body.get("sessions") or [],
                                          extra=body.get("extra") or {})
                return self._json(200, {"ok": True, "at_desk": self.core.presence.at_desk(),
                                        "voice_machine": self.core.presence.voice_machine()})
            if url.path == "/events":
                self.core.ledger.record("session", machine=body.get("machine"), session_id=body.get("session_id"),
                                        outcome=body.get("event"), summary=str(body.get("message", ""))[:300], data=body)
                return self._json(200, {"ok": True})
            if len(parts) == 3 and parts[0] == "asks" and parts[2] == "answer":
                result, ask = self.core.asks.answer(parts[1], str(body.get("text", "")), by=str(body.get("by", "app")))
                return self._json(200, {"result": result, "ask": ask.public() if ask else None})
            if url.path == "/pause":
                self.core.pause(str(body.get("reason", "paused")))
                return self._json(200, {"paused": self.core.paused})
            if url.path == "/unpause":
                self.core.unpause()
                return self._json(200, {"paused": None})
            if url.path == "/notify":
                res = self.core._notify(str(body.get("title", "BOSSK")), str(body.get("body", "")),
                                        priority=int(body.get("priority", 3)), tags=body.get("tags") or [],
                                        config=self.core.notify_cfg)
                self.core.ledger.record("notify", summary=str(body.get("title", "")), data={"result": res})
                return self._json(200, {"result": res})
            if url.path == "/rulebook/replay":
                return self._json(200, self.core.replay(str(body.get("rules", "")), int(body.get("days", 30))))
            if url.path == "/rulebook/reload":
                return self._json(200, {"rules": self.core.reload_rulebook()})
            if url.path == "/chat":
                text = str(body.get("text", ""))[:2000]
                machine = str(body.get("machine", ""))
                state, recent = self.core.health(), self.core.ledger.query(limit=25)
                if not body.get("stream"):
                    reply = self.core.assistant.reply(text, state, recent, machine=machine)
                    self.core.ledger.record("chat", machine=machine, summary=text, data={"reply": reply})
                    return self._json(200, {"reply": reply})
                cancel = threading.Event()
                self.send_response(200)
                self.send_header("Content-Type", "application/x-ndjson")
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                said = []
                try:
                    for sentence in self.core.assistant.stream(text, state, recent, machine=machine, cancel=cancel):
                        said.append(sentence)
                        self.wfile.write((json.dumps({"text": sentence}) + "\n").encode())
                        self.wfile.flush()
                    self.wfile.write(b'{"done": true}\n')
                    self.wfile.flush()
                except (BrokenPipeError, ConnectionResetError):
                    cancel.set()     # the mini interrupted: stop Claude mid-answer
                self.core.ledger.record("chat", machine=machine, summary=text,
                                        data={"reply": " ".join(said), "interrupted": cancel.is_set()})
                self.close_connection = True
                return None
            if url.path == "/tts":
                audio = self.core.tts.synth(str(body.get("text", ""))[:1000], str(body.get("voice", "")))
                if not audio:
                    self.send_response(204); self.send_header("Content-Length", "0"); self.end_headers()
                    return None
                self.send_response(200)
                self.send_header("Content-Type", "audio/mpeg")
                self.send_header("Content-Length", str(len(audio)))
                self.end_headers()
                self.wfile.write(audio)
                return None
        except Exception as e:  # noqa: BLE001
            log.exception("request failed")
            return self._json(500, {"error": str(e)})
        return self._json(404, {"error": "not found"})


def serve(core: Core) -> ThreadingHTTPServer:
    Handler.core = core
    httpd = ThreadingHTTPServer((core.cfg.host, core.cfg.port), Handler)
    httpd.daemon_threads = True
    return httpd
