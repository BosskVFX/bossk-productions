"""The voice daemon against a live core, with fake mic / wake word / speech-to-text / text-to-speech."""
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
from core.secrets import Secrets  # noqa: E402
from core.server import Core, serve  # noqa: E402
from tests.test_core_server import FakeReviewer  # noqa: E402
from voice.audio import FakeRecorder, loud, quiet, record_utterance, rms  # noqa: E402
from voice.daemon import CoreClient, VoiceDaemon, VoiceState  # noqa: E402
from voice.expressions.server import start as start_expr  # noqa: E402
from voice.stt import FakeSTT  # noqa: E402
from voice.tts import FakeTTS  # noqa: E402
from voice.wake import FakeWake  # noqa: E402


class NoPlayer:
    def play(self, p):
        pass


class VoiceTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = Path(tempfile.mkdtemp(prefix="bossk-voice-"))
        cfg = CoreConfig(host="127.0.0.1", port=0, data_dir=str(cls.tmp), rulebook=str(HERE.parent / "rulebook" / "rulebook.yml"),
                         max_wait_seconds=15, voice_grace_seconds=600)
        cls.sent = []
        cls.core = Core(cfg, token="tok", secrets=Secrets(file=cls.tmp / "none.env"), reviewer=FakeReviewer(),
                        notify_fn=lambda *a, **k: (cls.sent.append(a) or {"ntfy": "ok"}))
        cls.httpd = serve(cls.core)
        cls.url = f"http://127.0.0.1:{cls.httpd.server_address[1]}"
        threading.Thread(target=cls.httpd.serve_forever, daemon=True).start()
        cls.client = CoreClient(cls.url, "tok", "mini")

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()

    def setUp(self):
        self.client._req("POST", "/idle", {"machine": "mini", "idle_seconds": 1, "can_speak": True})
        self.state = VoiceState(self.tmp / "state.json", serve=False)

    def daemon(self, utterances, answers):
        self.tts = FakeTTS()
        self.wake = FakeWake()
        self.stt = FakeSTT(answers)
        d = VoiceDaemon(core=self.client, recorder=FakeRecorder(utterances), wake=self.wake, stt=self.stt, tts=self.tts,
                        player=NoPlayer(), state=self.state, listen_seconds=3, sleep=lambda s: None)
        return d

    def decide_in_thread(self, tool, cmd):
        result = {}
        body = {"machine": "pc", "session_id": "s1", "session_name": "frontdesk", "tool_name": tool, "tool_input": cmd, "cwd": "/x", "wait_seconds": 15}
        t = threading.Thread(target=lambda: result.update(self.client._req("POST", "/decide", body, timeout=40)))
        t.start()
        return t, result

    # ------------------------------------------------------------------ audio helpers
    def test_record_utterance_stops_on_silence(self):
        rec = FakeRecorder([loud(0.5) + quiet(1.5)])
        rec.start()
        samples = record_utterance(rec, max_seconds=5, silence_seconds=0.9)
        self.assertLess(len(samples), 16000 * 2.2)
        self.assertGreater(rms(samples[:512]), 0.04)
        rec2 = FakeRecorder([])   # nothing said -> gives up after ~3 s, not max_seconds
        samples = record_utterance(rec2, max_seconds=8)
        self.assertLess(len(samples), 16000 * 3.5)

    # ------------------------------------------------------------------ ask loop
    def test_ask_spoken_and_answered_yes(self):
        d = self.daemon([loud() + quiet()], ["yeah go ahead"])
        t, result = self.decide_in_thread("Bash", {"command": "git push origin main"})
        asks = self.client.poll_asks(wait=5)
        self.assertEqual(len(asks), 1)
        self.assertEqual(asks[0]["channel"], "voice:mini")
        self.assertEqual(d.handle_ask(asks[0]), "yes")
        t.join(20)
        self.assertEqual(result["behavior"], "allow")
        self.assertIn("Yes or no", self.tts.spoken[0])        # spoke the ask
        self.assertEqual(self.tts.spoken[-1], "Done.")
        self.assertEqual(self.sent, [])                         # phone never pinged

    def test_ask_confirm_tier(self):
        d = self.daemon([loud() + quiet(), loud() + quiet()], ["yes", "confirm"])
        t, result = self.decide_in_thread("mcp__Gmail__send_message", {"to": "client@x.com"})
        ask = self.client.poll_asks(wait=5)[0]
        self.assertTrue(ask["confirm"])
        self.assertEqual(d.handle_ask(ask), "yes")
        t.join(20)
        self.assertEqual(result["behavior"], "allow")
        self.assertIn("confirm", " ".join(self.tts.spoken).lower())
        self.assertEqual(self.stt.calls, 2)

    def test_ask_no(self):
        d = self.daemon([loud() + quiet()], ["no, cancel that"])
        t, result = self.decide_in_thread("Bash", {"command": "rm -rf /mnt/tank/video/originals/x"})
        ask = self.client.poll_asks(wait=5)[0]
        self.assertEqual(d.handle_ask(ask), "no")
        t.join(20)
        self.assertEqual(result["behavior"], "deny")

    def test_ask_unclear_then_gives_up_and_stays_open(self):
        d = self.daemon([loud() + quiet()] * 3, ["what is this about", "maybe later", "hmm"])
        t, result = self.decide_in_thread("Bash", {"command": "git push origin main"})
        ask = self.client.poll_asks(wait=5)[0]
        self.assertEqual(d.handle_ask(ask), "gave_up")
        self.assertIn("leave it on your phone", self.tts.spoken[-1])
        self.assertEqual(self.core.asks.get(ask["id"]).status, "pending")   # still open
        self.core.asks.answer(ask["id"], "no", by="phone")
        t.join(20)
        self.assertEqual(result["behavior"], "deny")

    def test_silence_then_repeat_then_give_up(self):
        d = self.daemon([quiet(0.5), quiet(0.5)], [])
        t, result = self.decide_in_thread("Bash", {"command": "git push origin main"})
        ask = self.client.poll_asks(wait=5)[0]
        self.assertEqual(d.handle_ask(ask), "gave_up")
        self.assertIn("did not hear", self.tts.spoken[1])
        self.core.asks.cancel(ask["id"])
        t.join(20)

    # ------------------------------------------------------------------ wake word
    def test_wake_pause_and_resume(self):
        d = self.daemon([loud() + quiet(), loud() + quiet()], ["BOSSK stop everything", "ok resume"])
        self.assertEqual(d.handle_wake(), "paused")
        self.assertTrue(self.core.paused)
        self.assertEqual(d.handle_wake(), "unpaused")
        self.assertIsNone(self.core.paused)

    def test_wake_status(self):
        d = self.daemon([loud() + quiet()], ["what's running"])
        self.assertEqual(d.handle_wake(), "status")
        self.assertIn("session", self.tts.spoken[-1])
        self.assertIn("nothing pending", self.tts.spoken[-1])

    def test_wake_answers_pending_ask(self):
        d = self.daemon([loud() + quiet()], ["yes"])
        t, result = self.decide_in_thread("Bash", {"command": "git push origin main"})
        time.sleep(0.5)
        self.assertEqual(d.handle_wake(), "answered:yes")
        t.join(20)
        self.assertEqual(result["behavior"], "allow")

    def test_wake_chat_fallback_without_api_key(self):
        d = self.daemon([loud() + quiet()], ["how much did we spend yesterday"])
        self.assertEqual(d.handle_wake(), "chat")
        self.assertIn("API key", self.tts.spoken[-1])

    def test_wake_nothing_heard(self):
        d = self.daemon([quiet(0.5)], [])
        self.assertEqual(d.handle_wake(), "nothing")

    def test_wake_loop_fires_and_mic_is_shared(self):
        d = self.daemon([loud() + quiet()], ["status"])
        self.wake.fire()
        th = threading.Thread(target=d.wake_loop, daemon=True)
        th.start()
        for _ in range(100):
            if any("session" in s for s in self.tts.spoken):
                break
            time.sleep(0.05)
        d.stop()
        th.join(5)
        self.assertTrue(any("session" in s for s in self.tts.spoken))

    # ------------------------------------------------------------------ expressions window + core tts/chat routes
    def test_expressions_server_and_state(self):
        vdir = self.tmp / "expr"; vdir.mkdir(exist_ok=True)
        (vdir / "idle.mp4").write_bytes(b"\x00" * 16)
        httpd = start_expr(self.state, port=0, video_dir=vdir)
        try:
            base = f"http://127.0.0.1:{httpd.server_address[1]}"
            self.state.set(state="speaking", level=0.7, text="hello")
            st = json.loads(urllib.request.urlopen(base + "/state.json").read())
            self.assertEqual((st["state"], st["level"], st["text"]), ("speaking", 0.7, "hello"))
            vids = json.loads(urllib.request.urlopen(base + "/videos.json").read())
            self.assertEqual(vids, {"idle": "/video/idle.mp4"})
            self.assertIn(b"BOSSK", urllib.request.urlopen(base + "/").read())
            self.assertEqual(urllib.request.urlopen(base + "/video/idle.mp4").read(), b"\x00" * 16)
        finally:
            httpd.shutdown()
        self.assertEqual(json.loads((self.tmp / "state.json").read_text())["state"], "speaking")

    def test_core_tts_204_without_key_and_chat_route(self):
        req = urllib.request.Request(self.url + "/tts", data=b'{"text":"hi"}', method="POST",
                                     headers={"Content-Type": "application/json", "Authorization": "Bearer tok"})
        with urllib.request.urlopen(req) as r:
            self.assertEqual(r.status, 204)
        self.assertIn("sessions running", self.client.chat("what happened"))


if __name__ == "__main__":
    unittest.main()
