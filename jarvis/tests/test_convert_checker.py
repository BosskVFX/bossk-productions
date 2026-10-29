"""End-to-end test of the NAS conversion checker using ffmpeg-generated clips.

Run:  python3 -m unittest jarvis.tests.test_convert_checker   (from the repo root)
  or: python3 -m unittest tests.test_convert_checker           (from jarvis/)
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
from nas import convert_checker as cc  # noqa: E402


def make_clip(path: Path, seconds: float, codec: str, extra: list[str] | None = None):
    path.parent.mkdir(parents=True, exist_ok=True)
    cmd = ["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", f"testsrc2=size=640x360:rate=25",
           "-t", str(seconds), "-pix_fmt", "yuv420p", "-c:v", codec] + (extra or []) + [str(path)]
    subprocess.run(cmd, check=True)


@unittest.skipUnless(shutil.which("ffmpeg") and shutil.which("ffprobe"), "ffmpeg not installed")
class ConvertCheckerTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="jarvis-cc-"))
        self.src = self.tmp / "originals"
        self.out = self.tmp / "h264"
        self.hold = self.tmp / "_holding"
        # Originals: big-ish (mpeg4 at high quality so H.264 is clearly smaller)
        make_clip(self.src / "a" / "good.mov", 4, "mpeg4", ["-q:v", "1"])
        make_clip(self.src / "a" / "truncated.mov", 4, "mpeg4", ["-q:v", "1"])
        make_clip(self.src / "b" / "short.mov", 4, "mpeg4", ["-q:v", "1"])
        make_clip(self.src / "b" / "tiny.mov", 4, "mpeg4", ["-q:v", "1"])
        make_clip(self.src / "b" / "nothing_yet.mov", 2, "mpeg4", ["-q:v", "1"])
        # Converted counterparts
        make_clip(self.out / "a" / "good.mp4", 4, "libx264", ["-crf", "23"])
        # faststart puts the moov atom first, so a chopped file still probes fine and only the tail decode catches it
        make_clip(self.out / "a" / "truncated.mp4", 4, "libx264", ["-crf", "23", "-movflags", "+faststart"])
        make_clip(self.out / "b" / "short.mp4", 2, "libx264", ["-crf", "23"])        # duration mismatch
        make_clip(self.out / "b" / "tiny.mp4", 4, "libx264", ["-crf", "51", "-s", "64x36"])  # bitrate floor
        # Chop the tail off truncated.mp4 so it still probes but the end does not decode
        t = self.out / "a" / "truncated.mp4"
        data = t.read_bytes()
        t.write_bytes(data[: int(len(data) * 0.6)])
        # Make every converted file look old enough
        old = time.time() - 3600
        for p in self.out.rglob("*.mp4"):
            os.utime(p, (old, old))
        self.cfg = cc.Config(
            source_dir=str(self.src), output_dir=str(self.out), holding_dir=str(self.hold),
            state_file=str(self.tmp / "state.json"), report_dir=str(self.tmp / "reports"),
            min_size_ratio=0.03, min_bitrate_kbps=1500, tail_seconds=2,
        )

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_dry_run_moves_nothing(self):
        rc = cc.run(self.cfg, dry_run=True, recheck=False, send=False)
        self.assertEqual(rc, 2)  # failures present
        self.assertTrue((self.src / "a" / "good.mov").exists())
        self.assertFalse(self.hold.exists())
        self.assertFalse((self.tmp / "state.json").exists())

    def test_real_run_verifies_and_moves_only_good(self):
        rc = cc.run(self.cfg, dry_run=False, recheck=False, send=False)
        self.assertEqual(rc, 2)
        self.assertFalse((self.src / "a" / "good.mov").exists())
        self.assertTrue((self.hold / "a" / "good.mov").exists())
        for kept in ("a/truncated.mov", "b/short.mov", "b/tiny.mov", "b/nothing_yet.mov"):
            self.assertTrue((self.src / kept).exists(), kept)
        state = json.loads((self.tmp / "state.json").read_text())
        statuses = {Path(k).name: v["status"] for k, v in state["files"].items()}
        self.assertEqual(statuses["good.mov"], "verified")
        self.assertEqual(statuses["truncated.mov"], "failed")
        self.assertEqual(statuses["short.mov"], "failed")
        self.assertEqual(statuses["tiny.mov"], "failed")
        self.assertNotIn("nothing_yet.mov", statuses)  # pending, not recorded as failed
        reasons = {Path(k).name: " ".join(v.get("reasons", [])) for k, v in state["files"].items()}
        self.assertIn("tail decode failed", reasons["truncated.mov"])
        self.assertIn("duration off", reasons["short.mov"])
        self.assertTrue("bitrate" in reasons["tiny.mov"] or "suspiciously small" in reasons["tiny.mov"])
        # Second run: failed ones are skipped (converted unchanged), nothing new happens
        rc2 = cc.run(self.cfg, dry_run=False, recheck=False, send=False)
        self.assertEqual(rc2, 0)

    def test_purge_after_hold_days(self):
        cc.run(self.cfg, dry_run=False, recheck=False, send=False)
        state = json.loads((self.tmp / "state.json").read_text())
        held = self.hold / "a" / "good.mov"
        self.assertIn(str(held), state["holding"])
        # Backdate the ledger entry 8 days
        state["holding"][str(held)]["moved_at"] = (datetime.now(timezone.utc) - timedelta(days=8)).isoformat()
        cc.save_state(self.cfg.state_file, state)
        cc.run(self.cfg, dry_run=False, recheck=False, send=False)
        self.assertFalse(held.exists())
        self.assertFalse((self.hold / "a").exists())  # empty dir pruned
        state = json.loads((self.tmp / "state.json").read_text())
        self.assertNotIn(str(held), state["holding"])

    def test_summary_text(self):
        cc.run(self.cfg, dry_run=False, recheck=False, send=False)
        summaries = sorted((self.tmp / "reports").glob("summary_*.txt"))
        text = summaries[-1].read_text()
        self.assertIn("Verified 1, failed 3, pending 1", text)
        self.assertIn("FAILED (originals kept)", text)


if __name__ == "__main__":
    unittest.main()
