"""End-to-end test of the NAS conversion checker with ffmpeg-generated clips, plus the purge-through-core path."""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
from nas import convert_checker as cc  # noqa: E402
from core.config import CoreConfig  # noqa: E402
from core.secrets import Secrets  # noqa: E402
from core.server import Core, serve  # noqa: E402
from tests.test_core_server import FakeReviewer  # noqa: E402


def make_clip(path: Path, seconds: float, codec: str, extra: list[str] | None = None):
    path.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "testsrc2=size=640x360:rate=25", "-t", str(seconds),
                    "-pix_fmt", "yuv420p", "-c:v", codec] + (extra or []) + [str(path)], check=True)


@unittest.skipUnless(shutil.which("ffmpeg") and shutil.which("ffprobe"), "ffmpeg not installed")
class ConvertCheckerTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="bossk-cc-"))
        self.src, self.out, self.hold = self.tmp / "originals", self.tmp / "h264", self.tmp / "_holding"
        for name in ("a/good", "a/truncated", "b/short", "b/tiny", "b/nothing_yet"):
            make_clip(self.src / f"{name}.mov", 4, "mpeg4", ["-q:v", "1"])
        make_clip(self.out / "a" / "good.mp4", 4, "libx264", ["-crf", "23"])
        make_clip(self.out / "a" / "truncated.mp4", 4, "libx264", ["-crf", "23", "-movflags", "+faststart"])
        make_clip(self.out / "b" / "short.mp4", 2, "libx264", ["-crf", "23"])
        make_clip(self.out / "b" / "tiny.mp4", 4, "libx264", ["-crf", "51", "-s", "64x36"])
        t = self.out / "a" / "truncated.mp4"
        t.write_bytes(t.read_bytes()[: int(t.stat().st_size * 0.6)])
        old = time.time() - 3600
        for p in self.out.rglob("*.mp4"):
            os.utime(p, (old, old))
        self.cfg = cc.Config(source_dir=str(self.src), output_dir=str(self.out), holding_dir=str(self.hold),
                             state_file=str(self.tmp / "state.json"), report_dir=str(self.tmp / "reports"), tail_seconds=2)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_dry_run_moves_nothing(self):
        self.assertEqual(cc.run(self.cfg, dry_run=True, recheck=False, send=False), 2)
        self.assertTrue((self.src / "a" / "good.mov").exists())
        self.assertFalse(self.hold.exists())

    def test_verifies_and_moves_only_good(self):
        self.assertEqual(cc.run(self.cfg, dry_run=False, recheck=False, send=False), 2)
        self.assertTrue((self.hold / "a" / "good.mov").exists())
        for kept in ("a/truncated.mov", "b/short.mov", "b/tiny.mov", "b/nothing_yet.mov"):
            self.assertTrue((self.src / kept).exists(), kept)
        state = json.loads((self.tmp / "state.json").read_text())
        reasons = {Path(k).name: " ".join(v.get("reasons", [])) for k, v in state["files"].items()}
        self.assertIn("tail decode failed", reasons["truncated.mov"])
        self.assertIn("duration off", reasons["short.mov"])
        self.assertTrue("bitrate" in reasons["tiny.mov"] or "suspiciously small" in reasons["tiny.mov"])
        self.assertNotIn("nothing_yet.mov", reasons)
        text = sorted((self.tmp / "reports").glob("summary_*.txt"))[-1].read_text()
        self.assertIn("Verified 1, failed 3, pending 1", text)
        time.sleep(1.1)
        self.assertEqual(cc.run(self.cfg, dry_run=False, recheck=False, send=False), 0)   # failed ones skipped next night

    def _age_holding(self, days=8):
        state = json.loads((self.tmp / "state.json").read_text())
        held = self.hold / "a" / "good.mov"
        state["holding"][str(held)]["moved_at"] = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
        cc.save_state(self.cfg.state_file, state)
        return held

    def test_purge_after_hold_days(self):
        cc.run(self.cfg, dry_run=False, recheck=False, send=False)
        held = self._age_holding()
        cc.run(self.cfg, dry_run=False, recheck=False, send=False)
        self.assertFalse(held.exists())
        self.assertFalse((self.hold / "a").exists())

    def test_purge_through_core_waits_for_yes(self):
        cc.run(self.cfg, dry_run=False, recheck=False, send=False)
        held = self._age_holding()
        cfg = CoreConfig(host="127.0.0.1", port=0, data_dir=str(self.tmp / "core"), rulebook=str(HERE.parent / "rulebook" / "rulebook.yml"),
                         max_wait_seconds=10)
        sent = []
        core = Core(cfg, token="tok", secrets=Secrets(file=self.tmp / "none.env"), reviewer=FakeReviewer(),
                    notify_fn=lambda title, body, **kw: (sent.append(body) or {"ntfy": "ok"}))
        httpd = serve(core)
        threading.Thread(target=httpd.serve_forever, daemon=True).start()
        try:
            self.cfg.ask, self.cfg.core_url, self.cfg.ask_wait_seconds = "through_core", f"http://127.0.0.1:{httpd.server_address[1]}", 10
            os.environ["BOSSK_TOKEN"] = "tok"
            # Jason is away: the ask goes to the phone, and he says no
            rc = {}
            t = threading.Thread(target=lambda: rc.update(rc=cc.run(self.cfg, dry_run=False, recheck=False, send=False)))
            t.start()
            for _ in range(100):
                if core.asks.pending():
                    break
                time.sleep(0.1)
            ask = core.asks.pending()[0]
            self.assertIn("Can I delete", ask.text)
            self.assertIn("1 original", ask.text)
            core.asks.answer(ask.id, "no", by="phone")
            t.join(30)
            self.assertTrue(held.exists())
            self.assertIn("denied by Jason", sorted((self.tmp / "reports").glob("summary_*.txt"))[-1].read_text())
            # next night he says yes
            t = threading.Thread(target=lambda: cc.run(self.cfg, dry_run=False, recheck=False, send=False)); t.start()
            for _ in range(100):
                if core.asks.pending():
                    break
                time.sleep(0.1)
            core.asks.answer(core.asks.pending()[0].id, "yes", by="voice")
            t.join(30)
            self.assertFalse(held.exists())
        finally:
            httpd.shutdown()
            os.environ.pop("BOSSK_TOKEN", None)


if __name__ == "__main__":
    unittest.main()
