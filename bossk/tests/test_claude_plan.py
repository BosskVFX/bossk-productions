"""BOSSK's brain bills the Claude plan, never the API: every call goes through `claude -p` with API-billing
variables stripped, and the plan's OAuth token passed in."""
from __future__ import annotations

import json
import os
import sys
import tempfile
import threading
import unittest
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
from core.assistant import Assistant  # noqa: E402
from core.claude_cli import ClaudeCLI, sentences  # noqa: E402
from core.config import CoreConfig  # noqa: E402
from core.knowledge import daily_log, seed, write_daily_log  # noqa: E402
from core.reviewer import Reviewer  # noqa: E402
from core.secrets import Secrets  # noqa: E402
from core.server import Core, serve  # noqa: E402

FAKE = str(HERE / "bin" / "claude")


class ClaudePlanTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="bossk-plan-"))
        self.logf = self.tmp / "calls.jsonl"
        os.environ["FAKE_CLAUDE_LOG"] = str(self.logf)
        os.environ["ANTHROPIC_API_KEY"] = "sk-ant-should-never-be-used"
        os.environ["ANTHROPIC_AUTH_TOKEN"] = "also-never"

    def tearDown(self):
        for k in ("FAKE_CLAUDE_LOG", "ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "FAKE_CLAUDE_VERDICT", "FAKE_CLAUDE_REPLY", "FAKE_CLAUDE_DELAY"):
            os.environ.pop(k, None)

    def calls(self):
        return [json.loads(l) for l in self.logf.read_text().splitlines()] if self.logf.exists() else []

    def cli(self):
        return ClaudeCLI(binary=FAKE, oauth_token="sk-ant-oat-plan-token", model="sonnet")

    def test_api_key_never_reaches_claude(self):
        r = self.cli().ask("hello")
        self.assertTrue(r.ok, r.error)
        c = self.calls()[-1]
        self.assertFalse(c["api_key"])
        self.assertTrue(c["oauth"])
        self.assertIn("-p", c["args"])
        self.assertNotIn("--bare", c["args"])       # --bare would skip the plan login

    def test_reviewer_on_plan(self):
        os.environ["FAKE_CLAUDE_VERDICT"] = "allow"
        rv = Reviewer(self.cli(), "sonnet")
        out = rv.review(tool="Bash", tool_input={"command": "pip install requests"}, cwd="/x", machine="pc", session_name="frontdesk", transcript_tail="")
        self.assertEqual(out["verdict"], "allow")
        ask, confirm = rv.write_ask(tool="Bash", tool_input={"command": "git push origin main"}, machine="pc", session_name="frontdesk",
                                    reason="pushes to main", confirm=True, context=None)
        self.assertIn("push to main", ask)
        self.assertTrue(all(not c["api_key"] for c in self.calls()))

    def test_stream_sentences_and_resume(self):
        a = Assistant(self.cli(), model="haiku")
        said = list(a.stream("what's going on", {"pending_asks": []}, machine="mini"))
        self.assertEqual(said, ["Two sessions are running.", "Nothing is waiting on you.", "All quiet on Mother."])
        list(a.stream("and the NAS?", {"pending_asks": []}, machine="mini"))
        args = self.calls()[-1]["args"]
        self.assertIn("--resume", args)                 # follow-up continues the conversation
        self.assertEqual(args[args.index("--model") + 1], "haiku")
        self.assertIn("--disallowedTools", args)        # voice brain can read notes, not change anything

    def test_stream_cancel(self):
        os.environ["FAKE_CLAUDE_DELAY"] = "0.2"
        a = Assistant(self.cli())
        cancel = threading.Event()
        got = []
        for s in a.stream("long answer", {}, cancel=cancel):
            got.append(s)
            cancel.set()
        self.assertEqual(len(got), 1)

    def test_no_claude_binary_falls_back(self):
        a = Assistant(ClaudeCLI(binary="/nonexistent/claude"))
        self.assertIn("login is not set up", a.reply("hi", {}))

    def test_sentences(self):
        self.assertEqual(list(sentences(iter(["Hello the", "re. How are", " you? Fine"]))), ["Hello there.", "How are you?", "Fine"])
        self.assertEqual(list(sentences(iter(["It is 3.5 GB. Done."]))), ["It is 3.5 GB.", "Done."])

    def test_core_chat_streams_over_http(self):
        cfg = CoreConfig(host="127.0.0.1", port=0, data_dir=str(self.tmp), rulebook=str(HERE.parent / "rulebook" / "rulebook.yml"),
                         claude_binary=FAKE)
        (self.tmp / "secrets.env").write_text("CLAUDE_CODE_OAUTH_TOKEN=sk-ant-oat-plan\n")
        core = Core(cfg, token="tok", secrets=Secrets(file=self.tmp / "secrets.env"), notify_fn=lambda *a, **k: {})
        httpd = serve(core)
        threading.Thread(target=httpd.serve_forever, daemon=True).start()
        try:
            req = urllib.request.Request(f"http://127.0.0.1:{httpd.server_address[1]}/chat", method="POST",
                                         data=json.dumps({"text": "status?", "machine": "mini", "stream": True}).encode(),
                                         headers={"Content-Type": "application/json", "Authorization": "Bearer tok"})
            lines = [json.loads(l) for l in urllib.request.urlopen(req, timeout=20).read().decode().splitlines() if l.strip()]
            self.assertEqual(lines[0], {"text": "Two sessions are running."})
            self.assertEqual(lines[-1], {"done": True})
            self.assertTrue(core.reviewer.available)
        finally:
            httpd.shutdown()
        self.assertTrue(all(c["oauth"] and not c["api_key"] for c in self.calls()))


class KnowledgeTest(unittest.TestCase):
    def test_seed_never_overwrites(self):
        d = Path(tempfile.mkdtemp())
        created = seed(d)
        self.assertIn("CLAUDE.md", created)
        self.assertTrue((d / "machines").is_dir())
        (d / "CLAUDE.md").write_text("Jason's edits")
        self.assertEqual(seed(d), [])
        self.assertEqual((d / "CLAUDE.md").read_text(), "Jason's edits")

    def test_daily_log(self):
        from core.ledger import Ledger
        led = Ledger(":memory:")
        led.record("decision", tier=1, outcome="allow", summary="Bash npm test")
        led.record("decision", tier=0, outcome="deny", summary="Bash rm -rf /")
        led.record("ask", tier=3, summary="Can I push to main?", data={"ask_id": "a1"})
        led.record("answer", tier=3, outcome="yes", summary="Can I push to main?", data={"ask_id": "a1"})
        led.record("ask", tier=3, summary="Can I delete 50 GB?", data={"ask_id": "a2"})
        text = daily_log(led)
        self.assertIn("Auto-approved 1", text)
        self.assertIn("denied 1", text)
        self.assertIn("yes: Can I push to main?", text)
        self.assertIn("## Still open", text)
        self.assertIn("Can I delete 50 GB?", text)
        p = write_daily_log(tempfile.mkdtemp(), led)
        self.assertTrue(p.name.endswith(".md"))


if __name__ == "__main__":
    unittest.main()
