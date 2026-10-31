"""Push-to-talk voice daemon against a live core and a fake `claude`, with a fake mic, speech-to-text, voice and speaker."""
from __future__ import annotations

import json
import os
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
from voice.audio import FakeCapture, loud, quiet, waveform64  # noqa: E402
from voice.daemon import CoreClient, VoiceDaemon, VoiceState, details_text  # noqa: E402
from voice.expressions.server import start as start_face  # noqa: E402
from voice.stt import FakeSTT  # noqa: E402

FAKE_CLAUDE = str(HERE / "bin" / "claude")


class FakeTTS:
    """Returns a path so the player runs; records what was said."""
    def __init__(self):
        self.spoken: list[str] = []

    def synth(self, text):
        self.spoken.append(text)
        return Path("/dev/null")


class FakePlayer:
    """Each sentence 'plays' for `secs` unless stopped."""
    def __init__(self, secs=0.05):
        self.secs = secs
        self._stop = threading.Event()
        self.stopped = 0

    def play(self, path, level_cb=None):
        self._stop.clear()
        end = time.time() + self.secs
        while time.time() < end:
            if self._stop.is_set():
                return False
            if level_cb:
                level_cb(0.5, [0.1] * 64)
            time.sleep(0.01)
        return True

    def stop(self):
        self.stopped += 1
        self._stop.set()


def wait_for(cond, timeout=10.0):
    end = time.time() + timeout
    while time.time() < end:
        if cond():
            return True
        time.sleep(0.02)
    return False


class VoiceTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = Path(tempfile.mkdtemp(prefix="bossk-voice-"))
        (cls.tmp / "secrets.env").write_text("CLAUDE_CODE_OAUTH_TOKEN=plan-token\n")
        cfg = CoreConfig(host="127.0.0.1", port=0, data_dir=str(cls.tmp), rulebook=str(HERE.parent / "rulebook" / "rulebook.yml"),
                         max_wait_seconds=20, voice_grace_seconds=600, claude_binary=FAKE_CLAUDE)
        cls.core = Core(cfg, token="tok", secrets=Secrets(file=cls.tmp / "secrets.env"), reviewer=FakeReviewer(),
                        notify_fn=lambda *a, **k: {"ntfy": "ok"})
        cls.httpd = serve(cls.core)
        cls.url = f"http://127.0.0.1:{cls.httpd.server_address[1]}"
        threading.Thread(target=cls.httpd.serve_forever, daemon=True).start()
        cls.client = CoreClient(cls.url, "tok", "mini")

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()

    def setUp(self):
        os.environ.pop("FAKE_CLAUDE_DELAY", None)
        for a in self.core.asks.pending():
            self.core.asks.cancel(a.id)
        self.core.paused = None
        self.client.call("POST", "/idle", {"machine": "mini", "idle_seconds": 1, "can_speak": True})

    def make(self, utterances, heard, play_secs=0.05, start=True):
        self.tts, self.player = FakeTTS(), FakePlayer(play_secs)
        self.stt = FakeSTT(heard)
        d = VoiceDaemon(core=self.client, capture=FakeCapture(utterances), stt=self.stt, tts=self.tts, player=self.player,
                        state=VoiceState(self.tmp / "state.json"), min_press_s=0.1, toggle_silence_s=0.4)
        if start:
            d.start()
            self.addCleanup(d.stop)
        return d

    def talk(self, d, hold=0.3):
        """Hold the button, 'speak', let go."""
        d.post("down")
        time.sleep(hold)
        d.post("up")

    def decide_async(self, tool, tool_input):
        result = {}
        body = {"machine": "pc", "session_id": "s1", "session_name": "frontdesk", "tool_name": tool, "tool_input": tool_input,
                "cwd": "/x", "wait_seconds": 20}
        t = threading.Thread(target=lambda: result.update(self.client.call("POST", "/decide", body, timeout=40)))
        t.start()
        return t, result

    # ------------------------------------------------------------------ asks
    def test_ask_is_spoken_and_answered_by_holding_the_button(self):
        d = self.make([loud(0.4)], ["yes go ahead"])
        t, result = self.decide_async("Bash", {"command": "git push origin main"})
        self.assertTrue(wait_for(lambda: any("Yes or no" in s for s in self.tts.spoken)))
        self.assertTrue(wait_for(lambda: d.state.snapshot()["state"] == "asking"))   # waiting, not walking away
        self.talk(d)
        t.join(20)
        self.assertEqual(result["behavior"], "allow")
        self.assertTrue(wait_for(lambda: "Done." in self.tts.spoken))
        self.assertTrue(wait_for(lambda: d.state.snapshot()["state"] == "idle"))

    def test_ask_never_gives_up(self):
        d = self.make([loud(0.4)] * 3, ["what's this about", "hmm", "no"])
        t, result = self.decide_async("Bash", {"command": "git push origin main"})
        self.assertTrue(wait_for(lambda: d.asks))
        self.talk(d)                       # not an answer -> goes to chat; ask stays open
        self.assertTrue(wait_for(lambda: "All quiet on Mother." in self.tts.spoken))
        self.assertEqual(len(d.asks), 1)
        time.sleep(1.0)
        self.assertEqual(self.core.asks.pending()[0].status, "pending")
        self.talk(d)                       # "hmm" -> chat again
        self.assertTrue(wait_for(lambda: self.stt.calls == 2))
        self.talk(d)                       # "no" -> answers it
        t.join(20)
        self.assertEqual(result["behavior"], "deny")

    def test_details_and_repeat(self):
        d = self.make([loud(0.4)] * 3, ["details", "say that again", "yes"])
        t, result = self.decide_async("Bash", {"command": "git push origin main"})
        self.assertTrue(wait_for(lambda: d.asks))
        self.talk(d)
        self.assertTrue(wait_for(lambda: any("wants to run: git push origin main" in s for s in self.tts.spoken)))
        n = sum(1 for s in self.tts.spoken if "Yes or no" in s and "wants to run" not in s)
        self.talk(d)
        self.assertTrue(wait_for(lambda: sum(1 for s in self.tts.spoken if "Yes or no" in s and "wants to run" not in s) == n + 1))
        self.talk(d)
        t.join(20)
        self.assertEqual(result["behavior"], "allow")

    def test_confirm_step(self):
        d = self.make([loud(0.4)] * 2, ["yes", "confirm"])
        t, result = self.decide_async("mcp__Gmail__send_message", {"to": "client@x.com"})
        self.assertTrue(wait_for(lambda: d.asks))
        self.talk(d)
        self.assertTrue(wait_for(lambda: any("confirm" in s.lower() for s in self.tts.spoken[1:])))
        self.talk(d)
        t.join(20)
        self.assertEqual(result["behavior"], "allow")

    def test_answered_on_phone_is_noticed(self):
        d = self.make([], [])
        t, result = self.decide_async("Bash", {"command": "git push origin main"})
        self.assertTrue(wait_for(lambda: d.asks))
        self.core.asks.answer(d.asks[0]["id"], "no", by="phone")
        t.join(20)
        self.assertTrue(wait_for(lambda: "That ask was answered on your phone." in self.tts.spoken, 30))
        self.assertTrue(wait_for(lambda: not d.asks))

    # ------------------------------------------------------------------ interrupt
    def test_button_interrupts_mid_sentence(self):
        os.environ["FAKE_CLAUDE_DELAY"] = "0.05"
        d = self.make([loud(0.4), loud(0.4)], ["tell me everything", "stop everything"], play_secs=2.0)
        self.talk(d)
        self.assertTrue(wait_for(lambda: d.speaker.busy.is_set() and self.tts.spoken))
        stops_before = self.player.stopped
        self.talk(d)                       # pressing again cuts BOSSK off and listens
        self.assertTrue(wait_for(lambda: self.player.stopped > stops_before))
        self.assertTrue(wait_for(lambda: self.core.paused is not None))
        self.assertNotIn("All quiet on Mother.", self.tts.spoken)   # the rest of the first answer was dropped
        self.assertTrue(wait_for(lambda: self.tts.spoken[-1].startswith("Paused.")))

    def test_tap_only_interrupts(self):
        d = self.make([[]], [], play_secs=2.0)
        d.speaker.say("A long sentence that should be cut.")
        self.assertTrue(wait_for(lambda: d.speaker.busy.is_set()))
        d.post("down"); time.sleep(0.03); d.post("up")
        self.assertTrue(wait_for(lambda: not d.speaker.busy.is_set()))
        time.sleep(0.2)
        self.assertEqual(self.stt.calls, 0)  # nothing transcribed

    def test_toggle_mode_stops_after_pause(self):
        d = self.make([loud(0.4) + quiet(2.0)], ["status"])
        d.post("toggle")
        self.assertTrue(wait_for(lambda: any("session" in s for s in self.tts.spoken)))

    # ------------------------------------------------------------------ commands
    def test_pause_resume_status_nevermind(self):
        d = self.make([loud(0.4)] * 4, ["stop everything", "resume", "status", "never mind"])
        self.talk(d)
        self.assertTrue(wait_for(lambda: self.core.paused))
        self.talk(d)
        self.assertTrue(wait_for(lambda: self.core.paused is None))
        self.talk(d)
        self.assertTrue(wait_for(lambda: any("nothing pending" in s for s in self.tts.spoken)))
        self.talk(d)
        self.assertTrue(wait_for(lambda: self.tts.spoken[-1] == "Okay."))

    def test_chat_streams_from_brain_on_plan(self):
        d = self.make([loud(0.4)], ["how are things"])
        self.talk(d)
        self.assertTrue(wait_for(lambda: "All quiet on Mother." in self.tts.spoken))
        self.assertIn("Two sessions are running.", self.tts.spoken)

    def test_silence_is_not_sent(self):
        d = self.make([quiet(0.5)], [])
        self.talk(d)
        self.assertTrue(wait_for(lambda: "I didn't catch that." in self.tts.spoken))
        self.assertEqual(self.stt.calls, 0)

    # ------------------------------------------------------------------ face + http triggers
    def test_face_and_stream_deck_buttons(self):
        d = self.make([loud(0.4)], ["status"])
        httpd = start_face(d.state, port=0, video_dir=self.tmp, on_ptt=d.post)
        try:
            base = f"http://127.0.0.1:{httpd.server_address[1]}"
            post = lambda p: urllib.request.urlopen(urllib.request.Request(base + p, data=b"", method="POST")).read()
            post("/ptt/down")
            self.assertTrue(wait_for(lambda: json.loads(urllib.request.urlopen(base + "/state.json").read())["state"] == "listening"))
            st = json.loads(urllib.request.urlopen(base + "/state.json").read())
            self.assertEqual(len(st["waveform"]), 64)
            time.sleep(0.2)
            post("/ptt/up")
            self.assertTrue(wait_for(lambda: any("session" in s for s in self.tts.spoken)))
            self.assertIn(b"ring", urllib.request.urlopen(base + "/").read())
        finally:
            httpd.shutdown()

    def test_helpers(self):
        self.assertEqual(len(waveform64(loud(0.1))), 64)
        txt = details_text({"machine": "pc", "tool_input": {"command": "rm -rf /mnt/x"}, "context": {"session_name": "nas"}})
        self.assertEqual(txt, "The nas session on pc wants to run: rm -rf /mnt/x. Yes or no?")


if __name__ == "__main__":
    unittest.main()
