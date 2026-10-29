from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
from loadout.client import (LoadoutClient, LoadoutError, PromptNotFromPromptMaster, discover_url,  # noqa: E402
                            seedance_body, kling_body)
from tests import fake_loadout  # noqa: E402


class LoadoutClientTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.httpd, cls.state, cls.url = fake_loadout.start()

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()

    def client(self):
        return LoadoutClient(self.url, sleep=lambda s: None)   # no real waiting in tests

    def test_discover_url(self):
        self.assertEqual(discover_url("http://pc:5500/"), "http://pc:5500")
        with tempfile.TemporaryDirectory() as d:
            Path(d, "port.json").write_text(json.dumps({"url": "http://127.0.0.1:61234", "pid": os.getpid()}))
            os.environ["LOADOUT_DATA_DIR"] = d
            try:
                self.assertEqual(discover_url(), "http://127.0.0.1:61234")
                Path(d, "port.json").write_text(json.dumps({"url": "http://127.0.0.1:61234", "pid": 999999999}))
                self.assertEqual(discover_url(), "http://127.0.0.1:5500")   # dead pid -> default
            finally:
                os.environ.pop("LOADOUT_DATA_DIR")

    def test_host_check_error_is_explained(self):
        c = LoadoutClient(self.url.replace("127.0.0.1", "localhost"))   # allowed
        self.assertEqual(c.get("/config")["version"], "0.2.28")
        self.state.allowed_hosts.discard("localhost")
        try:
            with self.assertRaises(LoadoutError) as cm:
                c.get("/config")
            self.assertIn("allowed_hosts", str(cm.exception))
        finally:
            self.state.allowed_hosts.add("localhost")

    def test_prompt_master_rule(self):
        c = self.client()
        with self.assertRaises(PromptNotFromPromptMaster):
            c.submit_video(seedance_body("a hornet"), prompt_master_id=None)
        with self.assertRaises(PromptNotFromPromptMaster):
            c.submit_image("a hornet", prompt_master_id="")
        with self.assertRaises(PromptNotFromPromptMaster):
            c.submit_higgsfield(kind="video", model="seedance_2_0", prompt="x", prompt_master_id=None)

    def test_video_round_trip_with_failover_and_project_pin(self):
        c = self.client()
        self.state.failover_once = True
        seen = []
        res = c.render_video(seedance_body("hornets circling", tier="pro", image_url="loadout-ref://refs/head.png"),
                             prompt_master_id="pm-001", project_id="p1", on_status=lambda st, r: seen.append(st))
        self.assertTrue(res.ok, res.error)
        self.assertIn("Hornets_Video_", res.local_url)
        self.assertEqual(res.descriptor["provider"], "fal")           # swapped to the failover handle
        self.assertEqual(seen[0], "Queued")
        self.assertIn("Rendering", seen)
        sub = self.state.submits[-1]
        self.assertEqual(sub["active_project"], "p1")                   # project pinned before submit
        self.assertEqual(sub["ui_params"]["prompt_master"], "pm-001")
        self.assertEqual(sub["ui_engine"], "bossk:byteplus")
        self.assertIn(self.state.downloaded[-1], res.local_url)             # the poll made it land

    def test_video_failure(self):
        c = self.client()
        res = c.render_video(kling_body("FAIL"), prompt_master_id="pm-002")
        self.assertFalse(res.ok)
        self.assertEqual(res.status, "Fail")
        self.assertIn("provider says no", res.error)

    def test_image_round_trip(self):
        c = self.client()
        jid = c.submit_image("hornet on a leaf", prompt_master_id="pm-003", model="nano-banana-pro", aspect_ratio="16:9")
        res = c.poll_image(jid)
        self.assertTrue(res.ok)
        self.assertEqual(res.files[0]["filename"], "Hornets_Image_001.png")

    def test_higgsfield_quote_and_run(self):
        c = self.client()
        q = c.higgsfield_quote("video", "seedance_2_0", {"duration": 5})
        self.assertEqual(q["credits"], 40)
        jid = c.submit_higgsfield(kind="video", model="seedance_2_0", prompt="x", prompt_master_id="pm-004", est_credits=40, est_usd=1.6)
        res = c.poll_higgsfield(jid)
        self.assertTrue(res.ok)
        self.assertEqual(res.files[0]["filename"], "loadout_higgs_abc.mp4")

    def test_roto_needs_no_prompt_master(self):
        c = self.client()
        with tempfile.NamedTemporaryFile(suffix=".mp4") as f:
            f.write(b"\x00" * 100); f.flush()
            staged = c.previz_upload(f.name)
        self.assertTrue(staged.startswith("staged_"))
        res = c.roto(staged_source=staged, points_positive=[[640, 300]], grow=12, feather=8)
        self.assertTrue(res.ok)
        self.assertEqual(self.state.submits[-1]["provider"], "roto")
        self.assertEqual(self.state.submits[-1]["inputs"]["points_positive"], [[640, 300]])

    def test_raw_comfy_graph_through_proxy(self):
        c = self.client()
        pid = c.comfy_prompt({"1": {"class_type": "WanVaceToVideo", "inputs": {}}})
        res = c.wait_comfy(pid)
        self.assertTrue(res.ok)
        self.assertEqual(res.files[0]["filename"], "vace_0001.png")


if __name__ == "__main__":
    unittest.main()
