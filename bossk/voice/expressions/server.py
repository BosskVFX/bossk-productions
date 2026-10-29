"""Serves the bare expressions window: the page, the state JSON, and the expression videos."""
from __future__ import annotations

import json
import mimetypes
import threading
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

HERE = Path(__file__).resolve().parent
VIDEO_DIR = Path("~/.bossk/expressions").expanduser()
STATES = ["idle", "listening", "thinking", "speaking", "asking"]


def start(state, port: int = 8751, video_dir: Path | None = None) -> ThreadingHTTPServer:
    vdir = video_dir or VIDEO_DIR

    class H(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def _send(self, code, data: bytes, ctype: str):
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self):
            path = urllib.parse.urlparse(self.path).path
            if path in ("/", "/index.html"):
                return self._send(200, (HERE / "index.html").read_bytes(), "text/html; charset=utf-8")
            if path == "/state.json":
                return self._send(200, json.dumps(state.snapshot()).encode(), "application/json")
            if path == "/videos.json":
                found = {}
                for s in STATES:
                    for ext in (".mp4", ".webm", ".mov"):
                        if (vdir / f"{s}{ext}").exists():
                            found[s] = f"/video/{s}{ext}"
                            break
                return self._send(200, json.dumps(found).encode(), "application/json")
            if path.startswith("/video/"):
                f = vdir / Path(path).name
                if f.exists() and f.suffix in (".mp4", ".webm", ".mov"):
                    return self._send(200, f.read_bytes(), mimetypes.guess_type(f.name)[0] or "video/mp4")
            self._send(404, b"not found", "text/plain")

    httpd = ThreadingHTTPServer(("127.0.0.1", port), H)
    httpd.daemon_threads = True
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    return httpd
