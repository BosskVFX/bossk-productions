import sys
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from core.asks import AskQueue, classify  # noqa: E402
from core.presence import Presence  # noqa: E402


class ClassifyTest(unittest.TestCase):
    def test_words(self):
        for t in ("yes", "Yes.", "yeah", "go ahead", "approve it", "ok"):
            self.assertEqual(classify(t), "yes", t)
        for t in ("no", "Nope", "cancel that", "stop", "deny"):
            self.assertEqual(classify(t), "no", t)
        for t in ("confirm", "Confirmed", "yes confirm"):
            self.assertEqual(classify(t), "confirm", t)
        self.assertEqual(classify("what is this about"), "unclear")


class PresenceTest(unittest.TestCase):
    def test_last_touched_wins_and_away(self):
        p = Presence(away_minutes=10, stale_seconds=60)
        p.report("mini", 300, can_speak=True)
        p.report("laptop", 20, can_speak=True)
        p.report("pc", 5, can_speak=False)
        self.assertTrue(p.at_desk())
        self.assertEqual(p.voice_machine(), "laptop")   # PC touched last but cannot speak
        p.report("mini", 700, can_speak=True)
        p.report("laptop", 700, can_speak=True)
        p.report("pc", 650, can_speak=False)
        self.assertFalse(p.at_desk())
        self.assertIsNone(p.voice_machine())

    def test_stale_reports_ignored(self):
        p = Presence(away_minutes=10, stale_seconds=0.2)
        p.report("mini", 1, can_speak=True)
        self.assertTrue(p.at_desk())
        time.sleep(0.3)
        self.assertFalse(p.at_desk())


class AskQueueTest(unittest.TestCase):
    def test_wait_and_expire(self):
        q = AskQueue()
        a = q.create(machine="pc", session_id="s", tool="Bash", tool_input={}, tier=3, rule_id="x", text="?", confirm=False)
        self.assertEqual(q.wait(a.id, 0.1), "pending")
        self.assertEqual(q.expire(0), [a])
        self.assertEqual(a.status, "expired")
        self.assertEqual(q.answer(a.id, "yes", by="t")[0], "closed")


if __name__ == "__main__":
    unittest.main()
