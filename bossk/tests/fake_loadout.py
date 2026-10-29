"""A stand-in LOADOUT server for tests, behaving as LOADOUT-BRIDGE.md describes:
Host allow-list (421), /video/generate -> descriptor, /video/status progression with one moderation
failover (new descriptor under "job"), /image/generate job ids, Higgsfield queue states, /projects/active,
/api/prompt + /api/history for raw graphs. Nothing lands on disk unless /video/status is polled to Success."""
from __future__ import annotations

import json
import threading
import urllib.parse
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


class State:
    def __init__(self):
        self.allowed_hosts = {"127.0.0.1", "localhost"}
        self.active_project = ""
        self.video_jobs: dict[str, dict] = {}
        self.image_jobs: dict[str, int] = {}
        self.higgs_jobs: dict[str, int] = {}
        self.comfy: dict[str, int] = {}
        self.submits: list[dict] = []
        self.downloaded: list[str] = []
        self.failover_once = True


class Handler(BaseHTTPRequestHandler):
    state: State

    def log_message(self, *a):
        pass

    def _json(self, code, obj):
        data = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _host_ok(self):
        host = (self.headers.get("Host") or "").split(":")[0]
        if host not in self.state.allowed_hosts:
            self._json(421, {"error": "misdirected"})
            return False
        return True

    def _body(self):
        n = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(n) if n else b""
        try:
            return json.loads(raw) if raw else {}
        except json.JSONDecodeError:
            return {"_raw_len": len(raw)}

    def do_GET(self):
        if not self._host_ok():
            return
        u = urllib.parse.urlparse(self.path)
        if u.path == "/config":
            return self._json(200, {"version": "0.2.28"})
        if u.path == "/gpu":
            return self._json(200, {"target": "local"})
        if u.path == "/projects":
            return self._json(200, {"projects": [{"id": "p1", "name": "Hornets"}], "active": self.state.active_project})
        if u.path == "/local/images":
            return self._json(200, [{"filename": f, "subfolder": "VID GEN", "type": "output", "kind": "video"} for f in self.state.downloaded])
        if u.path == "/recipe":
            return self._json(200, {"prompt": "p", "provider": "byteplus"})
        if u.path == "/spend":
            return self._json(200, {"balance": 10.0})
        if u.path == "/ledger":
            return self._json(200, {"events": []})
        if u.path.startswith("/api/history/"):
            pid = u.path.rsplit("/", 1)[1]
            n = self.state.comfy.get(pid)
            if n is None:
                return self._json(200, {})
            self.state.comfy[pid] = n + 1
            if n < 2:
                return self._json(200, {})
            return self._json(200, {pid: {"status": {"completed": True, "status_str": "success"},
                                          "outputs": {"9": {"images": [{"filename": "vace_0001.png", "subfolder": "", "type": "output"}]}}}})
        self._json(404, {"error": "nf"})

    def do_POST(self):
        if not self._host_ok():
            return
        u = urllib.parse.urlparse(self.path)
        b = self._body()
        s = self.state
        if u.path == "/projects/active":
            s.active_project = b.get("id", "")
            return self._json(200, {"active": s.active_project})
        if u.path == "/video/generate":
            if not b.get("provider"):
                return self._json(400, {"error": "provider"})
            if b["provider"] not in ("byteplus", "kling", "fal", "h3", "ltx25", "roto", "previz", "flux", "minimax", "h3cloud", "ltxv", "ltxrefine"):
                return self._json(400, {"error": "unknown provider"})
            s.submits.append({**b, "active_project": s.active_project})
            tid = uuid.uuid4().hex[:8]
            if b["provider"] in ("roto", "previz", "h3", "ltx25", "ltxv", "ltxrefine"):
                desc = {"provider": "wan", "prompt_id": tid, "client_id": "c", "engine": b["provider"]}
            else:
                desc = {"provider": b["provider"], "task_id": tid, "tier": b.get("tier"), "resolution": "1080p", "project_id": s.active_project}
            s.video_jobs[tid] = {"polls": 0, "desc": desc, "body": b}
            return self._json(200, desc)
        if u.path == "/video/status":
            tid = b.get("task_id") or b.get("prompt_id")
            job = s.video_jobs.get(tid)
            if not job:
                return self._json(200, {"status": "Fail", "error": "unknown task"})
            job["polls"] += 1
            if job["body"].get("prompt") == "FAIL":
                return self._json(200, {"status": "Fail", "error": "provider says no"})
            if job["polls"] == 1:
                return self._json(200, {"status": "Queued"})
            if job["polls"] == 2 and s.failover_once and job["desc"].get("provider") == "byteplus":
                s.failover_once = False
                new = {"provider": "fal", "model_id": "fal-ai/seedance", "request_id": uuid.uuid4().hex[:8], "task_id": uuid.uuid4().hex[:8]}
                s.video_jobs[new["task_id"]] = {"polls": 1, "desc": new, "body": job["body"]}
                return self._json(200, {"status": "Rendering", "note": "moderation failover", "job": new})
            if job["polls"] < 3:
                return self._json(200, {"status": "IN_PROGRESS" if job["desc"].get("provider") in ("wan", "fal") else "Rendering"})
            fname = f"Hornets_Video_{len(s.downloaded)+1:03d}.mp4"
            s.downloaded.append(fname)
            return self._json(200, {"status": "Success", "local_url": f"/view?filename={fname}&subfolder=VID%20GEN&type=output", "spend": 0.68})
        if u.path == "/video/cancel":
            return self._json(200, {"ok": True})
        if u.path == "/image/generate":
            jid = uuid.uuid4().hex[:8]
            s.image_jobs[jid] = 0
            s.submits.append({**b, "kind": "image", "active_project": s.active_project})
            return self._json(200, {"job_id": jid, "references": []})
        if u.path == "/image/generate/status":
            jid = b.get("job_id")
            if jid not in s.image_jobs:
                return self._json(200, {"status": "Fail", "error": "unknown"})
            s.image_jobs[jid] += 1
            if s.image_jobs[jid] < 3:
                return self._json(200, {"status": "Generating 1/1 with Nano Banana Pro"})
            return self._json(200, {"status": "Success", "images": [{"filename": "Hornets_Image_001.png", "subfolder": "IMG GEN", "type": "output", "kind": "image"}]})
        if u.path == "/higgsfield/cost":
            return self._json(200, {"credits": 40, "usd": 1.6})
        if u.path == "/higgsfield/submit":
            jid = uuid.uuid4().hex[:8]
            s.higgs_jobs[jid] = 0
            s.submits.append({**b, "kind": "higgsfield"})
            return self._json(200, {"job_id": jid})
        if u.path == "/higgsfield/status":
            jid = b.get("job_id")
            s.higgs_jobs[jid] = s.higgs_jobs.get(jid, 0) + 1
            n = s.higgs_jobs[jid]
            phases = ["Queued (Higgsfield)", "Submitted (Higgsfield)", "In progress (Higgsfield)"]
            if n <= 3:
                return self._json(200, {"status": phases[n - 1], "phase": "rendering"})
            return self._json(200, {"status": "Success", "files": [{"filename": "loadout_higgs_abc.mp4"}]})
        if u.path.startswith("/previz/upload"):
            return self._json(200, {"staged": "staged_" + urllib.parse.parse_qs(u.query).get("name", ["x"])[0]})
        if u.path == "/api/prompt":
            pid = uuid.uuid4().hex[:8]
            s.comfy[pid] = 0
            return self._json(200, {"prompt_id": pid, "number": 1})
        if u.path == "/mask/apply":
            return self._json(200, {"local_url": "/view?filename=loadout_masked_1.mp4"})
        if u.path == "/local/import":
            return self._json(200, {"imported": [b.get("_raw_len", 0)]})
        self._json(404, {"error": "nf"})


def start(state: State | None = None):
    st = state or State()
    Handler.state = st
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    httpd.daemon_threads = True
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    return httpd, st, f"http://127.0.0.1:{httpd.server_address[1]}"
