#!/usr/bin/env python3
"""Reports keyboard/mouse idle time and live Claude Code sessions to the core every 30 s.
Standard library only. Runs as a launchd agent on the Macs and a scheduled task on the PC.

  python3 idle_reporter.py            # loop forever
  python3 idle_reporter.py --once     # one report, print the reply
"""
from __future__ import annotations

import argparse
import ctypes
import json
import os
import platform
import re
import shutil
import socket
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

AGENT_FILE = Path(os.environ.get("BOSSK_AGENT_FILE", "~/.bossk/agent.json")).expanduser()


def idle_seconds() -> float:
    sysname = platform.system()
    try:
        if sysname == "Darwin":
            out = subprocess.run(["ioreg", "-c", "IOHIDSystem", "-d", "4"], capture_output=True, text=True, timeout=5).stdout
            m = re.search(r'"HIDIdleTime"\s*=\s*(\d+)', out)
            return int(m.group(1)) / 1e9 if m else 0.0
        if sysname == "Windows":
            class LASTINPUTINFO(ctypes.Structure):
                _fields_ = [("cbSize", ctypes.c_uint), ("dwTime", ctypes.c_uint)]
            lii = LASTINPUTINFO(); lii.cbSize = ctypes.sizeof(LASTINPUTINFO)
            ctypes.windll.user32.GetLastInputInfo(ctypes.byref(lii))  # type: ignore[attr-defined]
            return (ctypes.windll.kernel32.GetTickCount() - lii.dwTime) / 1000.0  # type: ignore[attr-defined]
        if shutil.which("xprintidle"):
            return int(subprocess.run(["xprintidle"], capture_output=True, text=True, timeout=5).stdout.strip()) / 1000.0
    except Exception:  # noqa: BLE001
        pass
    return 0.0


def claude_sessions() -> list[dict]:
    """`claude agents --json`: every live session on this machine and what it waits for."""
    exe = shutil.which("claude")
    if not exe:
        return []
    try:
        r = subprocess.run([exe, "agents", "--json"], capture_output=True, text=True, timeout=15)
        data = json.loads(r.stdout or "[]")
    except Exception:  # noqa: BLE001
        return []
    out = []
    for s in data if isinstance(data, list) else []:
        out.append({k: s.get(k) for k in ("sessionId", "id", "name", "cwd", "kind", "state", "status", "waitingFor", "startedAt") if k in s})
    return out


def gpu() -> dict:
    if not shutil.which("nvidia-smi"):
        return {}
    try:
        r = subprocess.run(["nvidia-smi", "--query-gpu=temperature.gpu,utilization.gpu,memory.used,memory.total",
                            "--format=csv,noheader,nounits"], capture_output=True, text=True, timeout=5)
        t, u, mu, mt = [x.strip() for x in r.stdout.strip().split(",")]
        return {"gpu_temp_c": int(t), "gpu_util_pct": int(u), "gpu_mem_used_mb": int(mu), "gpu_mem_total_mb": int(mt)}
    except Exception:  # noqa: BLE001
        return {}


def disk_free_gb(path: str = "/") -> float | None:
    try:
        return round(shutil.disk_usage(path).free / 1e9, 1)
    except OSError:
        return None


def report(agent: dict) -> dict:
    body = {"machine": agent.get("machine", socket.gethostname()), "idle_seconds": idle_seconds(),
            "can_speak": bool(agent.get("can_speak")), "sessions": claude_sessions(),
            "extra": {"os": platform.system(), "disk_free_gb": disk_free_gb(str(Path.home())), **gpu()}}
    req = urllib.request.Request(agent["core"].rstrip("/") + "/idle", data=json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json", "Authorization": f"Bearer {agent['token']}"}, method="POST")
    with urllib.request.urlopen(req, timeout=10) as r:
        return json.loads(r.read() or b"{}")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--once", action="store_true")
    ap.add_argument("--interval", type=float, default=30)
    a = ap.parse_args()
    agent = json.loads(AGENT_FILE.read_text())
    while True:
        try:
            res = report(agent)
            if a.once:
                print(json.dumps(res))
                return 0
        except Exception as e:  # noqa: BLE001
            if a.once:
                print(f"error: {e}", file=sys.stderr)
                return 1
        time.sleep(a.interval)


if __name__ == "__main__":
    sys.exit(main())
