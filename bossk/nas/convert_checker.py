#!/usr/bin/env python3
"""Nightly verifier for the TrueNAS H.264 conversion job. BOSSK phase 2.

The conversion job makes the H.264 files. This script is the approval to delete the originals.

Per new converted file, all must pass:
  1. exists and is non-empty
  2. ffprobe opens it cleanly with a video stream of the required codec (h264)
  3. duration matches the original within duration_tolerance_s (1.0 s)
  4. size is not suspiciously small: >= min_size_ratio of the original, and bitrate >= a floor scaled by resolution
  5. the last tail_seconds decode (a truncated encode passes ffprobe, fails here)

Verified originals move to holding_dir. Holding files older than hold_days are purged. Before any purge
a ZFS snapshot of the holding dataset is taken (zfs_dataset), so every delete is reversible for the
snapshot's life. With `ask: through_core`, the purge is a tier 3 ask to the BOSSK core (voice at the
desk, phone when away) and only a yes runs it. Nothing is deleted on the night it was converted.

  convert_checker.py --config convert_checker.yml                 nightly run
  convert_checker.py --config ... --dry-run                       report only
  convert_checker.py --config ... --recheck                       re-verify previously failed files
  convert_checker.py --config ... --no-notify

Exit 0 when nothing failed, 2 when something failed, 1 on config or runtime error.
"""
from __future__ import annotations

import argparse
import fcntl
import json
import logging
import os
import shutil
import subprocess
import sys
import time
import urllib.request
from dataclasses import dataclass, field, asdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from core.notify import NotifyConfig, notify  # noqa: E402

log = logging.getLogger("convert_checker")
VIDEO_EXTS = {".mov", ".mp4", ".mxf", ".avi", ".mkv", ".m4v", ".mts", ".m2ts", ".webm"}


@dataclass
class Config:
    source_dir: str
    output_dir: str
    holding_dir: str
    state_file: str = "convert_checker_state.json"
    report_dir: str = "reports"
    hold_days: int = 7
    duration_tolerance_s: float = 1.0
    min_size_ratio: float = 0.03
    min_bitrate_kbps: int = 1500
    required_codec: str = "h264"
    tail_seconds: float = 5.0
    output_suffix: str = ""
    output_ext: str = ".mp4"
    source_exts: list[str] = field(default_factory=lambda: sorted(VIDEO_EXTS))
    ignore_dirs: list[str] = field(default_factory=lambda: [".recycle", "@eaDir", "_holding", "_failed"])
    min_age_minutes: int = 10
    ffprobe: str = "ffprobe"
    ffmpeg: str = "ffmpeg"
    zfs_dataset: str = ""                 # e.g. tank/video  -> snapshot before every purge; "" disables
    zfs_snapshot_keep: int = 14           # delete bossk-purge-* snapshots older than this many days
    ask: str = "none"                     # none | through_core : make the purge a tier 3 ask to the core
    core_url: str = ""                    # http://127.0.0.1:8750 when running on the same box
    core_token_env: str = "BOSSK_TOKEN"
    ask_wait_seconds: int = 1800
    notify: dict[str, Any] = field(default_factory=dict)
    summary_title: str = "NAS conversions"

    @classmethod
    def load(cls, path: str | Path) -> "Config":
        p = Path(path)
        raw = yaml.safe_load(p.read_text()) or {} if p.suffix in (".yml", ".yaml") else json.loads(p.read_text())
        unknown = set(raw) - set(cls.__dataclass_fields__)
        if unknown:
            raise SystemExit(f"unknown config keys: {sorted(unknown)}")
        cfg = cls(**raw)
        for k in ("source_dir", "output_dir", "holding_dir"):
            setattr(cfg, k, str(Path(getattr(cfg, k)).expanduser()))
        for k in ("state_file", "report_dir"):
            if not Path(getattr(cfg, k)).is_absolute():
                setattr(cfg, k, str(p.parent / getattr(cfg, k)))
        return cfg


# ----------------------------------------------------------------------------- ffprobe
@dataclass
class Probe:
    ok: bool
    duration: float = 0.0
    size: int = 0
    bit_rate: int = 0
    codec: str = ""
    width: int = 0
    height: int = 0
    error: str = ""


def ffprobe(path: Path, binary: str = "ffprobe") -> Probe:
    cmd = [binary, "-v", "error", "-select_streams", "v:0",
           "-show_entries", "format=duration,size,bit_rate:stream=codec_name,width,height,duration", "-of", "json", str(path)]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
    except (OSError, subprocess.TimeoutExpired) as e:
        return Probe(ok=False, error=f"ffprobe failed to run: {e}")
    if r.returncode != 0:
        return Probe(ok=False, error=(r.stderr.strip() or f"ffprobe exit {r.returncode}")[:300])
    try:
        data = json.loads(r.stdout or "{}")
    except json.JSONDecodeError:
        return Probe(ok=False, error="ffprobe returned invalid json")
    fmt, streams = data.get("format", {}), data.get("streams", [])
    if not streams:
        return Probe(ok=False, error="no video stream")
    s = streams[0]
    return Probe(ok=True, duration=float(fmt.get("duration") or s.get("duration") or 0.0),
                 size=int(fmt.get("size") or path.stat().st_size), bit_rate=int(fmt.get("bit_rate") or 0),
                 codec=s.get("codec_name", ""), width=int(s.get("width") or 0), height=int(s.get("height") or 0))


def tail_decodes(path: Path, seconds: float, binary: str = "ffmpeg") -> tuple[bool, str]:
    cmd = [binary, "-v", "error", "-nostdin", "-sseof", f"-{seconds}", "-i", str(path), "-f", "null", "-"]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
    except (OSError, subprocess.TimeoutExpired) as e:
        return False, f"ffmpeg failed to run: {e}"
    if r.returncode != 0 or r.stderr.strip():
        return False, (r.stderr.strip() or f"ffmpeg exit {r.returncode}")[:300]
    return True, ""


# ----------------------------------------------------------------------------- verification
@dataclass
class Result:
    original: str
    converted: str
    status: str                       # verified | failed | pending | skipped
    reasons: list[str] = field(default_factory=list)
    orig_size: int = 0
    conv_size: int = 0
    orig_duration: float = 0.0
    conv_duration: float = 0.0
    saved_bytes: int = 0


def bitrate_floor(cfg: Config, width: int, height: int) -> int:
    return max(int(cfg.min_bitrate_kbps * max(width * height, 1) / (1920 * 1080)), 300)


def verify_pair(cfg: Config, original: Path, converted: Path) -> Result:
    res = Result(original=str(original), converted=str(converted), status="failed")
    if not converted.exists():
        res.status, res.reasons = "pending", ["converted file not found yet"]
        return res
    if converted.stat().st_size == 0:
        res.reasons.append("converted file is 0 bytes")
        return res
    age_min = (time.time() - converted.stat().st_mtime) / 60
    if age_min < cfg.min_age_minutes:
        res.status, res.reasons = "pending", [f"converted file modified {age_min:.0f} min ago, may still be writing"]
        return res
    op, cp = ffprobe(original, cfg.ffprobe), ffprobe(converted, cfg.ffprobe)
    res.orig_size, res.conv_size = original.stat().st_size, converted.stat().st_size
    if not op.ok:
        res.reasons.append(f"original does not probe: {op.error}")
    if not cp.ok:
        res.reasons.append(f"converted does not open cleanly: {cp.error}")
    if res.reasons:
        return res
    res.orig_duration, res.conv_duration = op.duration, cp.duration
    if cfg.required_codec and cp.codec != cfg.required_codec:
        res.reasons.append(f"codec is {cp.codec}, expected {cfg.required_codec}")
    delta = abs(op.duration - cp.duration)
    if delta > cfg.duration_tolerance_s:
        res.reasons.append(f"duration off by {delta:.2f}s (orig {op.duration:.2f}s, conv {cp.duration:.2f}s)")
    if res.conv_size < cfg.min_size_ratio * res.orig_size:
        res.reasons.append(f"suspiciously small: {res.conv_size/1e6:.1f} MB is {100*res.conv_size/max(res.orig_size,1):.1f}% of original")
    kbps = (cp.bit_rate or (res.conv_size * 8 / max(cp.duration, 0.001))) / 1000
    floor = bitrate_floor(cfg, cp.width, cp.height)
    if kbps < floor:
        res.reasons.append(f"bitrate {kbps:.0f} kbps below floor {floor} kbps for {cp.width}x{cp.height}")
    if cfg.tail_seconds > 0 and not res.reasons:
        ok, err = tail_decodes(converted, min(cfg.tail_seconds, max(cp.duration, 0.1)), cfg.ffmpeg)
        if not ok:
            res.reasons.append(f"tail decode failed: {err}")
    if not res.reasons:
        res.status, res.saved_bytes = "verified", res.orig_size - res.conv_size
    return res


# ----------------------------------------------------------------------------- discovery / state
def iter_originals(cfg: Config):
    exts = {e.lower() for e in cfg.source_exts}
    for root, dirs, files in os.walk(cfg.source_dir):
        dirs[:] = [d for d in dirs if d not in cfg.ignore_dirs and not d.startswith(".")]
        for f in files:
            p = Path(root) / f
            if p.suffix.lower() in exts and not f.startswith("."):
                yield p


def converted_path_for(cfg: Config, original: Path) -> Path:
    rel = original.relative_to(cfg.source_dir)
    return Path(cfg.output_dir) / rel.with_name(rel.stem + cfg.output_suffix + cfg.output_ext)


def load_state(path: str) -> dict:
    p = Path(path)
    if p.exists():
        try:
            return json.loads(p.read_text())
        except json.JSONDecodeError:
            log.warning("state file corrupt, starting fresh: %s", path)
    return {"version": 1, "files": {}, "holding": {}}


def save_state(path: str, state: dict) -> None:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, indent=2, sort_keys=True))
    os.replace(tmp, p)


# ----------------------------------------------------------------------------- actions
def move_to_holding(cfg: Config, original: Path, dry_run: bool) -> Path:
    dest = Path(cfg.holding_dir) / original.relative_to(cfg.source_dir)
    if dry_run:
        return dest
    dest.parent.mkdir(parents=True, exist_ok=True)
    if dest.exists():
        dest = dest.with_name(f"{dest.stem}.{datetime.now().strftime('%Y%m%d-%H%M%S')}{dest.suffix}")
    shutil.move(str(original), str(dest))
    return dest


def purge_candidates(cfg: Config, state: dict) -> list[dict]:
    cutoff = datetime.now(timezone.utc) - timedelta(days=cfg.hold_days)
    out = []
    holding = Path(cfg.holding_dir)
    if not holding.exists():
        return out
    for root, _dirs, files in os.walk(holding):
        for f in files:
            p = Path(root) / f
            rec = state["holding"].get(str(p))
            moved_at = datetime.fromisoformat(rec["moved_at"]) if rec else datetime.fromtimestamp(p.stat().st_mtime, tz=timezone.utc)
            if moved_at <= cutoff:
                out.append({"path": str(p), "size": p.stat().st_size, "moved_at": moved_at.isoformat()})
    return out


def zfs_snapshot(cfg: Config, dry_run: bool) -> str | None:
    """Snapshot the dataset before a purge, and prune old bossk-purge snapshots. Returns the snapshot name."""
    if not cfg.zfs_dataset or not shutil.which("zfs"):
        return None
    name = f"{cfg.zfs_dataset}@bossk-purge-{datetime.now().strftime('%Y%m%d-%H%M%S')}"
    if dry_run:
        return name
    r = subprocess.run(["zfs", "snapshot", name], capture_output=True, text=True)
    if r.returncode != 0:
        log.error("zfs snapshot failed: %s", r.stderr.strip())
        return None
    cutoff = time.time() - cfg.zfs_snapshot_keep * 86400
    ls = subprocess.run(["zfs", "list", "-H", "-t", "snapshot", "-o", "name,creation", "-p", "-r", cfg.zfs_dataset],
                        capture_output=True, text=True)
    for line in ls.stdout.splitlines():
        parts = line.split("\t")
        if len(parts) == 2 and "@bossk-purge-" in parts[0] and parts[1].isdigit() and int(parts[1]) < cutoff:
            subprocess.run(["zfs", "destroy", parts[0]], capture_output=True)
    return name


def ask_core(cfg: Config, summary: str) -> str:
    """Tier 3 ask through the BOSSK core. Returns allow | deny | pending | error."""
    token = os.environ.get(cfg.core_token_env, "")
    if not cfg.core_url or not token:
        return "error"
    body = {"machine": "truenas", "session_id": "convert_checker", "session_name": "NAS conversions", "tool_name": "Bash",
            "tool_input": {"command": f"rm (purge {cfg.holding_dir})"}, "cwd": cfg.holding_dir,
            "wait_seconds": cfg.ask_wait_seconds, "context": {"summary": summary}, "force_tier": 3,
            "reason": f"purge of originals held more than {cfg.hold_days} days"}
    req = urllib.request.Request(cfg.core_url.rstrip("/") + "/decide", data=json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json", "Authorization": f"Bearer {token}"}, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=cfg.ask_wait_seconds + 30) as r:
            return json.loads(r.read()).get("behavior", "error")
    except Exception as e:  # noqa: BLE001
        log.error("core ask failed: %s", e)
        return "error"


def purge(cfg: Config, state: dict, candidates: list[dict], dry_run: bool) -> tuple[list[dict], str | None]:
    snap = zfs_snapshot(cfg, dry_run)
    done = []
    for c in candidates:
        entry = dict(c)
        if not dry_run:
            try:
                Path(c["path"]).unlink()
                state["holding"].pop(c["path"], None)
            except OSError as e:
                entry["error"] = str(e)
        done.append(entry)
    if not dry_run:
        for root, _dirs, _files in os.walk(cfg.holding_dir, topdown=False):
            if root != cfg.holding_dir and not os.listdir(root):
                os.rmdir(root)
    return done, snap


# ----------------------------------------------------------------------------- run
def human(n: float) -> str:
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if abs(n) < 1024:
            return f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} PB"


def build_summary(cfg: Config, results: list[Result], purged: list[dict], purge_status: str, snap: str | None,
                  dry_run: bool) -> str:
    v = [r for r in results if r.status == "verified"]
    f = [r for r in results if r.status == "failed"]
    p = [r for r in results if r.status == "pending"]
    lines = ["(DRY RUN, nothing moved)"] if dry_run else []
    lines.append(f"Verified {len(v)}, failed {len(f)}, pending {len(p)}. Reclaimed {human(sum(r.saved_bytes for r in v))}.")
    if v:
        lines += ["", "Verified -> holding:"] + [f"  {Path(r.original).name}  ({human(r.orig_size)} -> {human(r.conv_size)})" for r in v[:15]]
        if len(v) > 15:
            lines.append(f"  ... and {len(v)-15} more")
    if f:
        lines += ["", "FAILED (originals kept):"] + [f"  {Path(r.original).name}: " + "; ".join(r.reasons) for r in f]
    if purged:
        total = sum(x["size"] for x in purged)
        if purge_status == "done":
            lines += ["", f"Deleted {len(purged)} originals from holding (> {cfg.hold_days} days), {human(total)}." + (f" Snapshot {snap}." if snap else "")]
        else:
            lines += ["", f"{len(purged)} originals ({human(total)}) are past {cfg.hold_days} days in holding. Purge {purge_status}."]
    if p and not v and not f:
        lines.append("Nothing new to verify.")
    return "\n".join(lines)


def run(cfg: Config, *, dry_run: bool, recheck: bool, send: bool) -> int:
    state = load_state(cfg.state_file)
    results: list[Result] = []
    now_iso = datetime.now(timezone.utc).isoformat()

    for original in iter_originals(cfg):
        key, converted = str(original), converted_path_for(cfg, original)
        rec = state["files"].get(key)
        if rec and rec.get("status") == "failed" and not recheck:
            if converted.exists() and converted.stat().st_mtime <= rec.get("conv_mtime", 0):
                results.append(Result(original=key, converted=str(converted), status="skipped", reasons=["previously failed, unchanged"]))
                continue
        res = verify_pair(cfg, original, converted)
        results.append(res)
        if res.status == "verified":
            dest = move_to_holding(cfg, original, dry_run)
            log.info("VERIFIED %s -> %s", original.name, dest)
            if not dry_run:
                state["holding"][str(dest)] = {"original": key, "converted": str(converted), "moved_at": now_iso, "size": res.orig_size}
                state["files"][key] = {"status": "verified", "moved_to": str(dest), "at": now_iso}
        elif res.status == "failed":
            log.warning("FAILED %s: %s", original.name, "; ".join(res.reasons))
            if not dry_run:
                state["files"][key] = {"status": "failed", "reasons": res.reasons, "at": now_iso,
                                       "conv_mtime": converted.stat().st_mtime if converted.exists() else 0}
        else:
            log.info("%s %s: %s", res.status.upper(), original.name, "; ".join(res.reasons))

    candidates = purge_candidates(cfg, state)
    purged, purge_status, snap = [], "not needed", None
    if candidates:
        total = sum(c["size"] for c in candidates)
        if cfg.ask == "through_core" and not dry_run:
            summary = (f"Excuse me Jason. Can I delete {human(total)} from the server? That is {len(candidates)} original "
                       f"video files that have been in holding for more than {cfg.hold_days} days, all verified converted.")
            verdict = ask_core(cfg, summary)
            if verdict == "allow":
                purged, snap = purge(cfg, state, candidates, dry_run)
                purge_status = "done"
            else:
                purged, purge_status = candidates, {"deny": "denied by Jason", "pending": "waiting for an answer"}.get(verdict, "skipped, core unreachable")
        else:
            purged, snap = purge(cfg, state, candidates, dry_run)
            purge_status = "done"
        for x in purged:
            log.info("PURGE %s %s", purge_status, x["path"])

    if not dry_run:
        save_state(cfg.state_file, state)
    rd = Path(cfg.report_dir)
    rd.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y-%m-%d_%H%M%S")
    (rd / f"report_{stamp}.json").write_text(json.dumps({"run_at": now_iso, "dry_run": dry_run,
                                                        "results": [asdict(r) for r in results if r.status != "skipped"],
                                                        "purged": purged, "purge_status": purge_status, "snapshot": snap}, indent=2))
    summary = build_summary(cfg, results, purged, purge_status, snap, dry_run)
    (rd / f"summary_{stamp}.txt").write_text(summary)
    print(summary)

    failed = any(r.status == "failed" for r in results)
    acted = any(r.status in ("verified", "failed") for r in results) or purged
    if send and acted:
        out = notify(cfg.summary_title + (": problems" if failed else ""), summary, priority=4 if failed else 3,
                     tags=["warning"] if failed else ["white_check_mark"], config=NotifyConfig.from_dict(cfg.notify))
        log.info("notify: %s", out)
    return 2 if failed else 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--config", required=True)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--recheck", action="store_true")
    ap.add_argument("--no-notify", action="store_true")
    ap.add_argument("-v", "--verbose", action="store_true")
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    cfg = Config.load(args.config)
    for k in ("source_dir", "output_dir"):
        if not Path(getattr(cfg, k)).is_dir():
            log.error("%s does not exist: %s", k, getattr(cfg, k))
            return 1
    lock_path = Path(cfg.state_file).with_suffix(".lock")
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with open(lock_path, "w") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            log.error("another run is in progress (lock %s)", lock_path)
            return 1
        return run(cfg, dry_run=args.dry_run, recheck=args.recheck, send=not args.no_notify)


if __name__ == "__main__":
    sys.exit(main())
