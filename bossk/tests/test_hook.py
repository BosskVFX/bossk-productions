"""Runs agents/hooks/bossk_hook.py as Claude Code would: JSON on stdin, decision JSON on stdout."""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import threading
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
from core.config import CoreConfig  # noqa: E402
from core.secrets import Secrets  # noqa: E402
from core.server import Core, serve  # noqa: E402
from tests.test_core_server import FakeReviewer  # noqa: E402

HOOK = HERE.parent / "agents" / "hooks" / "bossk_hook.py"


class HookTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = Path(tempfile.mkdtemp(prefix="bossk-hook-"))
        cfg = CoreConfig(host="127.0.0.1", port=0, data_dir=str(cls.tmp), rulebook=str(HERE.parent / "rulebook" / "rulebook.yml"),
                         max_wait_seconds=2)
        cls.core = Core(cfg, token="tok", secrets=Secrets(file=cls.tmp / "none.env"), reviewer=FakeReviewer(),
                        notify_fn=lambda *a, **k: {"ntfy": "ok"})
        cls.httpd = serve(cls.core)
        cls.port = cls.httpd.server_address[1]
        threading.Thread(target=cls.httpd.serve_forever, daemon=True).start()
        cls.agent_file = cls.tmp / "agent.json"
        cls.settings = cls.tmp / "settings.json"
        cls.settings.write_text(json.dumps({"permissions": {"allow": ["Read"]}, "hooks": {"Stop": [{"hooks": [{"type": "command", "command": "echo hi"}]}]}}))
        env = {**os.environ, "BOSSK_AGENT_FILE": str(cls.agent_file)}
        r = subprocess.run([sys.executable, str(HOOK), "install", "--core", f"http://127.0.0.1:{cls.port}", "--token", "tok",
                            "--machine", "testbox", "--settings", str(cls.settings)], capture_output=True, text=True, env=env)
        assert r.returncode == 0, r.stderr

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()

    def run_hook(self, event: dict, agent_file: Path | None = None) -> dict | None:
        env = {**os.environ, "BOSSK_AGENT_FILE": str(agent_file or self.agent_file)}
        r = subprocess.run([sys.executable, str(HOOK)], input=json.dumps(event), capture_output=True, text=True, env=env, timeout=60)
        self.assertEqual(r.returncode, 0, r.stderr)
        return json.loads(r.stdout) if r.stdout.strip() else None

    def test_install_merges_settings(self):
        s = json.loads(self.settings.read_text())
        self.assertEqual(s["permissions"], {"allow": ["Read"]})          # untouched
        self.assertEqual(len(s["hooks"]["Stop"]), 2)                      # existing Stop hook kept
        self.assertEqual(s["hooks"]["PermissionRequest"][0]["matcher"], "*")
        self.assertEqual(s["hooks"]["PermissionRequest"][0]["hooks"][0]["timeout"], 600)
        self.assertIn("bossk_hook.py", s["hooks"]["PreToolUse"][0]["hooks"][0]["command"])
        agent = json.loads(self.agent_file.read_text())
        self.assertEqual(agent["machine"], "testbox")

    def test_permission_allow(self):
        out = self.run_hook({"hook_event_name": "PermissionRequest", "session_id": "abc", "cwd": "/Users/jason/code/x",
                             "tool_name": "Bash", "tool_input": {"command": "npm test"}})
        self.assertEqual(out["hookSpecificOutput"]["decision"]["behavior"], "allow")
        self.assertEqual(out["hookSpecificOutput"]["decision"]["updatedInput"], {"command": "npm test"})

    def test_permission_deny_tier0(self):
        out = self.run_hook({"hook_event_name": "PermissionRequest", "session_id": "abc", "cwd": "/x",
                             "tool_name": "Bash", "tool_input": {"command": "rm -rf /"}})
        self.assertEqual(out["hookSpecificOutput"]["decision"]["behavior"], "deny")
        self.assertIn("never", out["hookSpecificOutput"]["decision"]["message"])

    def test_permission_pending_falls_through(self):
        # nobody answers within the core's 2 s wait -> no JSON -> Claude Code shows its own prompt
        out = self.run_hook({"hook_event_name": "PermissionRequest", "session_id": "abc", "cwd": "/x",
                             "tool_name": "Bash", "tool_input": {"command": "git push origin main"}})
        self.assertIsNone(out)
        for a in self.core.asks.pending():
            self.core.asks.cancel(a.id)

    def test_core_unreachable_denies(self):
        bad = self.tmp / "bad.json"
        bad.write_text(json.dumps({"core": "http://127.0.0.1:9", "token": "x", "machine": "t"}))
        out = self.run_hook({"hook_event_name": "PermissionRequest", "tool_name": "Bash", "tool_input": {"command": "npm test"}}, bad)
        self.assertEqual(out["hookSpecificOutput"]["decision"]["behavior"], "deny")
        self.assertIn("unreachable", out["hookSpecificOutput"]["decision"]["message"])
        bad.write_text(json.dumps({"core": "http://127.0.0.1:9", "token": "x", "machine": "t", "offline": "ask"}))
        self.assertIsNone(self.run_hook({"hook_event_name": "PermissionRequest", "tool_name": "Bash", "tool_input": {"command": "npm test"}}, bad))

    def test_pre_tool_use_and_events_logged(self):
        self.assertIsNone(self.run_hook({"hook_event_name": "PreToolUse", "session_id": "s9", "tool_name": "Read", "tool_input": {"file_path": "/a"}}))
        self.assertIsNone(self.run_hook({"hook_event_name": "Notification", "session_id": "s9", "notification_type": "permission_prompt", "message": "needs you"}))
        kinds = {(e["kind"], e["outcome"]) for e in self.core.ledger.query(session_id="s9")}
        self.assertIn(("tool", None), kinds)
        self.assertIn(("session", "Notification"), kinds)

    def test_install_links_shared_memory(self):
        from core.knowledge import seed
        k = self.tmp / "knowledge"
        seed(k)
        cmd_md = self.tmp / "CLAUDE.md"
        cmd_md.write_text("# my own notes\n")
        settings = self.tmp / "settings2.json"
        env = {**os.environ, "BOSSK_AGENT_FILE": str(self.tmp / "agent2.json")}
        for _ in range(2):   # idempotent
            r = subprocess.run([sys.executable, str(HOOK), "install", "--core", "http://x:8750", "--token", "t", "--machine", "mini",
                                "--settings", str(settings), "--knowledge", str(k), "--claude-md", str(cmd_md)],
                               capture_output=True, text=True, env=env)
            self.assertEqual(r.returncode, 0, r.stderr)
        text = cmd_md.read_text()
        self.assertTrue(text.startswith("# my own notes"))
        self.assertEqual(text.count(f"@{k}/CLAUDE.md"), 1)
        s = json.loads(settings.read_text())
        self.assertEqual(s["autoMemoryDirectory"], str(k / "machines" / "mini" / "memory"))
        self.assertTrue((k / "machines" / "mini" / "memory").is_dir())
        r = subprocess.run([sys.executable, str(HOOK), "install", "--core", "http://x", "--token", "t", "--settings", str(settings),
                            "--knowledge", str(self.tmp / "not-mounted")], capture_output=True, text=True, env=env)
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("mount", r.stderr + r.stdout)

    def test_transcript_tail(self):
        sys.path.insert(0, str(HOOK.parent))
        import bossk_hook  # noqa: E402
        t = self.tmp / "t.jsonl"
        t.write_text("\n".join([
            json.dumps({"type": "user", "message": {"role": "user", "content": "fix the login bug"}}),
            json.dumps({"type": "assistant", "message": {"role": "assistant", "content": [{"type": "text", "text": "Looking."},
                                                                                           {"type": "tool_use", "name": "Bash", "input": {"command": "ls"}}]}}),
            "not json",
        ]))
        tail = bossk_hook.transcript_tail(str(t))
        self.assertIn("user: fix the login bug", tail)
        self.assertIn("[tool Bash", tail)


if __name__ == "__main__":
    unittest.main()
