"""End-to-end test of the core over real HTTP on a random port."""
from __future__ import annotations

import json
import sys
import tempfile
import threading
import time
import unittest
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
from core.config import CoreConfig  # noqa: E402
from core.server import Core, serve  # noqa: E402
from core.secrets import Secrets  # noqa: E402

TOKEN = "test-token"


class FakeReviewer:
    available = True

    def __init__(self):
        self.verdict = "escalate"

    def review(self, **kw):
        return {"verdict": self.verdict, "reason": "fake", "ask": "Can the session install a package?"}

    def write_ask(self, *, tool, tool_input, machine, session_name, reason, confirm, context, transcript_tail=""):
        if context and context.get("summary"):
            return context["summary"], context.get("confirm_text", "")
        return f"Excuse me Jason, {session_name} on {machine} wants {tool_input.get('command', tool)}. Yes or no?", "Say confirm."


class CoreServerTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp(prefix="bossk-core-")
        cfg = CoreConfig(host="127.0.0.1", port=0, data_dir=cls.tmp, rulebook=str(HERE.parent / "rulebook" / "rulebook.yml"),
                         public_url="http://core.test", voice_grace_seconds=1, max_wait_seconds=5)
        cls.sent = []
        cls.reviewer = FakeReviewer()
        cls.core = Core(cfg, token=TOKEN, secrets=Secrets(file=Path(cls.tmp) / "none.env"), reviewer=cls.reviewer,
                        notify_fn=lambda title, body, **kw: (cls.sent.append({"title": title, "body": body, **kw}) or {"ntfy": "ok"}))
        cls.httpd = serve(cls.core)
        cls.port = cls.httpd.server_address[1]
        threading.Thread(target=cls.httpd.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()

    def call(self, method, path, body=None, auth=True):
        req = urllib.request.Request(f"http://127.0.0.1:{self.port}{path}", method=method,
                                     data=json.dumps(body).encode() if body is not None else None,
                                     headers={"Content-Type": "application/json", **({"Authorization": f"Bearer {TOKEN}"} if auth else {})})
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                return r.status, json.loads(r.read())
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read() or b"{}")

    def decide(self, tool, tool_input, wait=5, **extra):
        return self.call("POST", "/decide", {"machine": "pc", "session_id": "s1", "session_name": "frontdesk",
                                             "tool_name": tool, "tool_input": tool_input, "cwd": "/Users/jason/code/frontdesk",
                                             "wait_seconds": wait, **extra})[1]

    def test_auth_required(self):
        self.assertEqual(self.call("GET", "/health", auth=False)[0], 401)
        self.assertEqual(self.call("GET", "/health")[0], 200)

    def test_tier1_allow_and_tier0_deny(self):
        self.assertEqual(self.decide("Bash", {"command": "git status"})["behavior"], "allow")
        d = self.decide("Bash", {"command": "rm -rf /"})
        self.assertEqual(d["behavior"], "deny")
        self.assertEqual(d["tier"], 0)

    def test_tier2_reviewer_allow(self):
        self.reviewer.verdict = "allow"
        try:
            d = self.decide("Bash", {"command": "pip install requests"})
            self.assertEqual(d["behavior"], "allow")
            self.assertIn("reviewer", d["reason"])
        finally:
            self.reviewer.verdict = "escalate"

    def test_voice_ask_answered_by_daemon(self):
        self.sent.clear()
        # Jason is at the desk on the mini, which can speak
        self.call("POST", "/idle", {"machine": "mini", "idle_seconds": 5, "can_speak": True})
        self.call("POST", "/idle", {"machine": "pc", "idle_seconds": 2000, "can_speak": False})
        result = {}

        def session():
            result.update(self.decide("Bash", {"command": "git push origin main"}, wait=10))

        t = threading.Thread(target=session); t.start()
        # the voice daemon long-polls and gets the ask
        _, got = self.call("GET", "/asks?channel=voice:mini&wait=5")
        self.assertEqual(len(got["asks"]), 1)
        ask = got["asks"][0]
        self.assertEqual(ask["channel"], "voice:mini")
        self.assertIn("Yes or no", ask["text"])
        _, ans = self.call("POST", f"/asks/{ask['id']}/answer", {"text": "yeah go ahead", "by": "voice"})
        self.assertEqual(ans["result"], "yes")
        t.join(10)
        self.assertEqual(result["behavior"], "allow")
        self.assertEqual(self.sent, [])  # phone never pinged

    def test_phone_when_away_and_button_callback(self):
        self.sent.clear()
        self.call("POST", "/idle", {"machine": "mini", "idle_seconds": 3600, "can_speak": True})
        self.call("POST", "/idle", {"machine": "pc", "idle_seconds": 3600, "can_speak": False})
        result = {}

        def session():
            result.update(self.decide("Bash", {"command": "rm -rf /mnt/tank/video/originals/x"}, wait=10))

        t = threading.Thread(target=session); t.start()
        for _ in range(50):
            if self.sent:
                break
            time.sleep(0.1)
        self.assertEqual(len(self.sent), 1)
        actions = self.sent[0]["actions"]
        no_btn = next(a for a in actions if a.label == "No")
        # the ntfy button POSTs to /a/<id>/<token>/no with no bearer
        path = no_btn.url.replace("http://core.test", "")
        code, body = self.call("POST", path, auth=False)
        self.assertEqual((code, body["result"]), (200, "no"))
        t.join(10)
        self.assertEqual(result["behavior"], "deny")
        # a replayed button press is rejected
        self.assertEqual(self.call("POST", path, auth=False)[1]["result"], "closed")
        self.assertEqual(self.call("POST", path.replace(no_btn.url.split("/")[-2], "wrongtoken"), auth=False)[1]["result"], "bad_token")

    def test_confirm_tier(self):
        self.call("POST", "/idle", {"machine": "mini", "idle_seconds": 1, "can_speak": True})
        result = {}

        def session():
            result.update(self.decide("mcp__Gmail__send_message", {"to": "client@x.com", "body": "hi"}, wait=10))

        t = threading.Thread(target=session); t.start()
        _, got = self.call("GET", "/asks?channel=voice:mini&wait=5")
        ask = got["asks"][0]
        self.assertTrue(ask["confirm"])
        _, a1 = self.call("POST", f"/asks/{ask['id']}/answer", {"text": "yes", "by": "voice"})
        self.assertEqual(a1["result"], "awaiting_confirm")
        _, a2 = self.call("POST", f"/asks/{ask['id']}/answer", {"text": "yes", "by": "voice"})
        self.assertEqual(a2["result"], "awaiting_confirm")   # a second yes is not a confirm
        _, a3 = self.call("POST", f"/asks/{ask['id']}/answer", {"text": "confirm", "by": "voice"})
        self.assertEqual(a3["result"], "yes")
        t.join(10)
        self.assertEqual(result["behavior"], "allow")

    def test_pending_when_nobody_answers(self):
        self.call("POST", "/idle", {"machine": "mini", "idle_seconds": 1, "can_speak": True})
        d = self.decide("Bash", {"command": "git push origin main"}, wait=1)
        self.assertEqual(d["behavior"], "pending")
        self.assertTrue(d["ask_id"])
        self.core.asks.cancel(d["ask_id"])

    def test_pause_routes_everything_to_jason(self):
        self.call("POST", "/pause", {"reason": "Jason said stop"})
        try:
            d = self.decide("Bash", {"command": "git status"}, wait=0)
            self.assertEqual(d["behavior"], "pending")
            self.core.asks.cancel(d["ask_id"])
        finally:
            self.call("POST", "/unpause")
        self.assertEqual(self.decide("Bash", {"command": "git status"})["behavior"], "allow")

    def test_ledger_and_replay(self):
        self.call("POST", "/idle", {"machine": "mini", "idle_seconds": 1, "can_speak": True})
        d = self.decide("Bash", {"command": "git push origin main"}, wait=0)
        self.core.asks.cancel(d["ask_id"])
        _, led = self.call("GET", "/ledger?kind=prompt&limit=5")
        self.assertTrue(led["events"])
        rules = "rules:\n  - id: allow-push-main\n    tier: 1\n    match: {tool: Bash, command: 'git push origin main'}\n"
        _, rep = self.call("POST", "/rulebook/replay", {"rules": rules, "days": 1})
        self.assertTrue(any(e["to"] == 1 and "push origin main" in e["input"] for e in rep["newly_allowed"]))


if __name__ == "__main__":
    unittest.main()
