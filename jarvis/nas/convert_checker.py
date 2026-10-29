#!/usr/bin/env python3
"""Nightly verifier for the TrueNAS H.264 conversion job.

Problem it solves: the conversion job finishes, Claude Code sits waiting for a human
to approve deleting the originals, and the approval never comes. This script is the
approval. It runs on a timer, verifies every new converted file against its original
with ffprobe, and only then moves the original into a holding folder. Originals in
holding are deleted automatically after `hold_days` (default 7). Nothing is ever
deleted on the same night it was converted.

Checks per file (all must pass):
  1. converted file exists and is non-empty
  2. ffprobe opens it cleanly: a video stream is present and it is H.264 (configurable)
  3. duration matches the original within `duration_tolerance_s` (default 1.0s)
  4. size is not suspiciously small: at least `min_size_ratio` of the original AND
     average bitrate >= `min_bitrate_kbps` (bitrate floor scaled by resolution)
  5. (default on) the last `tail_seconds` of the file actually decode; a truncated
     encode passes ffprobe but fails here

Usage:
  convert_checker.py --config /path/to/convert_checker.yml            # normal nightly run
  convert_checker.py --config ... --dry-run                            # report only, move nothing
  convert_checker.py --config ... --recheck                            # re-verify everything, not just new files
  convert_checker.py --config ... --no-notify                          # skip the phone summary

Exit code 0 when every new file verified, 2 when something failed (so a cron/systemd
monitor can see it), 1 on a config or runtime error.
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
from dataclasses import dataclass, field, asdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

try:
    import yaml  # type: ignore
except ImportError:  # pragma: no cover
    yaml = None

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from common.notify import NotifyConfig, notify  # noqa: E402

log = logging.getLogger("convert_checker")

VIDEO_EXTS = {".mov", ".mp4", ".mxf", ".avi", ".mkv", ".m4v", ".mts", ".m2ts", ".webm", ".prores"}


# ----------------------------------------------------------------------------- config
@dataclass
class Config:
    source_dir: str                       # where the conversion job reads originals from
    output_dir: str                       # where it writes H.264 files (mirrors source_dir's tree)
    holding_dir: str                      # verified originals go here, then auto-delete
    state_file: str = "convert_checker_state.json"
    report_dir: str = "reports"
    hold_days: int = 7
    duration_tolerance_s: float = 1.0
    min_size_ratio: float = 0.03          # converted must be >= 3% of the original's bytes
    min_bitrate_kbps: int = 1500          # floor at 1080p; scaled by pixel count
    required_codec: str = "h264"          # "" to accept any codec
    tail_seconds: float = 5.0             # 0 disables the decode check
    output_suffix: str = ""               # e.g. "_h264" if the job names files like clip_h264.mp4
    output_ext: str = ".mp4"
    source_exts: list[str] = field(default_factory=lambda: sorted(VIDEO_EXTS))
    ignore_dirs: list[str] = field(default_factory=lambda: [".recycle", "@eaDir", "_holding", "_failed"])
    min_age_minutes: int = 10             # skip converted files modified in the last N min (still being written)
    ffprobe: str = "ffprobe"
    ffmpeg: str = "ffmpeg"
    notify: dict[str, Any] = field(default_factory=dict)
    summary_title: str = "NAS conversions"

    @classmethod
    def load(cls, path: str | Path) -> "Config":
        p = Path(path)
        text = p.read_text()
        if p.suffix in (".yml", ".yaml"):
            if yaml is None:
                raise SystemExit("PyYAML not installed; use a .json config or pip install pyyaml")
            raw = yaml.safe_load(text) or {}
        else:
            raw = json.loads(text)
        unknown = set(raw) - set(cls.__dataclass_fields__)
        if unknown:
            raise SystemExit(f"unknown config keys: {sorted(unknown)}")
        cfg = cls(**raw)
        for k in ("source_dir", "output_dir", "holding_dir"):
            setattr(cfg, k, str(Path(getattr(cfg, k)).expanduser()))
        if not Path(cfg.state_file).is_absolute():
            cfg.state_file = str(p.parent / cfg.state_file)
        if not Path(cfg.report_dir).is_absolute():
            cfg.report_dir = str(p.parent / cfg.report_dir)
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
    cmd = [
        binary, "-v", "error",
        "-select_streams", "v:0",
        "-show_entries", "format=duration,size,bit_rate:stream=codec_name,width,height,duration",
        "-of", "json", str(path),
    ]
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
    fmt = data.get("format", {})
    streams = data.get("streams", [])
    if not streams:
        return Probe(ok=False, error="no video stream")
    s = streams[0]
    duration = float(fmt.get("duration") or s.get("duration") or 0.0)
    return Probe(
        ok=True,
        duration=duration,
        size=int(fmt.get("size") or path.stat().st_size),
        bit_rate=int(fmt.get("bit_rate") or 0),
        codec=s.get("codec_name", ""),
        width=int(s.get("width") or 0),
        height=int(s.get("height") or 0),
    )


def tail_decodes(path: Path, seconds: float, binary: str = "ffmpeg") -> tuple[bool, str]:
    """Decode the last `seconds` of the file. Truncated/corrupt tails fail here."""
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
    status: str                      # verified | failed | pending | skipped
    reasons: list[str] = field(default_factory=list)
    orig_size: int = 0
    conv_size: int = 0
    orig_duration: float = 0.0
    conv_duration: float = 0.0
    saved_bytes: int = 0


def expected_bitrate_floor(cfg: Config, width: int, height: int) -> int:
    """Scale the 1080p floor by pixel count, with a hard minimum so tiny clips still get a sane floor."""
    pixels = max(width * height, 1)
    scaled = int(cfg.min_bitrate_kbps * pixels / (1920 * 1080))
    return max(scaled, 300)


def verify_pair(cfg: Config, original: Path, converted: Path) -> Result:
    res = Result(original=str(original), converted=str(converted), status="failed")

    if not converted.exists():
        res.status = "pending"
        res.reasons.append("converted file not found yet")
        return res
    if converted.stat().st_size == 0:
        res.reasons.append("converted file is 0 bytes")
        return res
    age_min = (time.time() - converted.stat().st_mtime) / 60
    if age_min < cfg.min_age_minutes:
        res.status = "pending"
        res.reasons.append(f"converted file modified {age_min:.0f} min ago, may still be writing")
        return res

    op = ffprobe(original, cfg.ffprobe)
    cp = ffprobe(converted, cfg.ffprobe)
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
        res.reasons.append(
            f"suspiciously small: {res.conv_size/1e6:.1f} MB is {100*res.conv_size/max(res.orig_size,1):.1f}% of original"
        )
    kbps = (cp.bit_rate or (res.conv_size * 8 / max(cp.duration, 0.001))) / 1000
    floor = expected_bitrate_floor(cfg, cp.width, cp.height)
    if kbps < floor:
        res.reasons.append(f"bitrate {kbps:.0f} kbps below floor {floor} kbps for {cp.width}x{cp.height}")
    if cfg.tail_seconds > 0 and not res.reasons:
        ok, err = tail_decodes(converted, min(cfg.tail_seconds, max(cp.duration, 0.1)), cfg.ffmpeg)
        if not ok:
            res.reasons.append(f"tail decode failed: {err}")

    if not res.reasons:
        res.status = "verified"
        res.saved_bytes = res.orig_size - res.conv_size
    return res


# ----------------------------------------------------------------------------- discovery
def iter_originals(cfg: Config):
    src = Path(cfg.source_dir)
    exts = {e.lower() for e in cfg.source_exts}
    for root, dirs, files in os.walk(src):
        dirs[:] = [d for d in dirs if d not in cfg.ignore_dirs and not d.startswith(".")]
        for f in files:
            p = Path(root) / f
            if p.suffix.lower() in exts and not f.startswith("."):
                yield p


def converted_path_for(cfg: Config, original: Path) -> Path:
    rel = original.relative_to(cfg.source_dir)
    return Path(cfg.output_dir) / rel.with_name(rel.stem + cfg.output_suffix + cfg.output_ext)


# ----------------------------------------------------------------------------- state
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
    rel = original.relative_to(cfg.source_dir)
    dest = Path(cfg.holding_dir) / rel
    if dry_run:
        return dest
    dest.parent.mkdir(parents=True, exist_ok=True)
    if dest.exists():
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        dest = dest.with_name(f"{dest.stem}.{stamp}{dest.suffix}")
    shutil.move(str(original), str(dest))   # same dataset => rename; cross-dataset => copy+delete
    return dest


def purge_holding(cfg: Config, state: dict, dry_run: bool) -> list[dict]:
    """Delete holding files older than hold_days. Uses the ledger's moved_at, falling back to mtime."""
    purged = []
    cutoff = datetime.now(timezone.utc) - timedelta(days=cfg.hold_days)
    holding = Path(cfg.holding_dir)
    if not holding.exists():
        return purged
    for root, dirs, files in os.walk(holding):
        for f in files:
            p = Path(root) / f
            rec = state["holding"].get(str(p))
            if rec:
                moved_at = datetime.fromisoformat(rec["moved_at"])
            else:
                moved_at = datetime.fromtimestamp(p.stat().st_mtime, tz=timezone.utc)
            if moved_at <= cutoff:
                entry = {"path": str(p), "size": p.stat().st_size, "moved_at": moved_at.isoformat()}
                if not dry_run:
                    try:
                        p.unlink()
                        state["holding"].pop(str(p), None)
                    except OSError as e:
                        entry["error"] = str(e)
                purged.append(entry)
    if not dry_run:
        # prune empty dirs left behind
        for root, dirs, files in os.walk(holding, topdown=False):
            if root != str(holding) and not os.listdir(root):
                os.rmdir(root)
    return purged


# ----------------------------------------------------------------------------- run
def human(n: float) -> str:
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if abs(n) < 1024:
            return f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} PB"


def build_summary(cfg: Config, results: list[Result], purged: list[dict], dry_run: bool) -> str:
    v = [r for r in results if r.status == "verified"]
    f = [r for r in results if r.status == "failed"]
    p = [r for r in results if r.status == "pending"]
    saved = sum(r.saved_bytes for r in v)
    lines = []
    if dry_run:
        lines.append("(DRY RUN, nothing moved)")
    lines.append(f"Verified {len(v)}, failed {len(f)}, pending {len(p)}. Reclaimed {human(saved)}.")
    if v:
        lines.append("")
        lines.append("Verified -> holding:")
        for r in v[:15]:
            lines.append(f"  {Path(r.original).name}  ({human(r.orig_size)} -> {human(r.conv_size)})")
        if len(v) > 15:
            lines.append(f"  ... and {len(v)-15} more")
    if f:
        lines.append("")
        lines.append("FAILED (originals kept):")
        for r in f:
            lines.append(f"  {Path(r.original).name}: " + "; ".join(r.reasons))
    if purged:
        total = sum(x["size"] for x in purged)
        lines.append("")
        lines.append(f"Deleted {len(purged)} originals from holding (> {cfg.hold_days} days), {human(total)}.")
    if p and not v and not f:
        lines.append("Nothing new to verify.")
    return "\n".join(lines)


def run(cfg: Config, *, dry_run: bool, recheck: bool, send: bool) -> int:
    state = load_state(cfg.state_file)
    results: list[Result] = []
    now_iso = datetime.now(timezone.utc).isoformat()

    for original in iter_originals(cfg):
        key = str(original)
        rec = state["files"].get(key)
        converted = converted_path_for(cfg, original)
        if rec and rec.get("status") == "failed" and not recheck:
            # Re-verify failed ones only if the converted file changed since we last looked.
            try:
                if converted.exists() and converted.stat().st_mtime <= rec.get("conv_mtime", 0):
                    results.append(Result(original=key, converted=str(converted), status="skipped",
                                          reasons=["previously failed, converted file unchanged"]))
                    continue
            except OSError:
                pass
        res = verify_pair(cfg, original, converted)
        results.append(res)
        if res.status == "verified":
            dest = move_to_holding(cfg, original, dry_run)
            log.info("VERIFIED %s -> %s", original.name, dest)
            if not dry_run:
                state["holding"][str(dest)] = {"original": key, "converted": str(converted), "moved_at": now_iso,
                                               "size": res.orig_size}
                state["files"][key] = {"status": "verified", "moved_to": str(dest), "at": now_iso}
        elif res.status == "failed":
            log.warning("FAILED %s: %s", original.name, "; ".join(res.reasons))
            if not dry_run:
                state["files"][key] = {"status": "failed", "reasons": res.reasons, "at": now_iso,
                                       "conv_mtime": converted.stat().st_mtime if converted.exists() else 0}
        else:
            log.info("%s %s: %s", res.status.upper(), original.name, "; ".join(res.reasons))

    purged = purge_holding(cfg, state, dry_run)
    for x in purged:
        log.info("PURGED %s", x["path"])

    if not dry_run:
        save_state(cfg.state_file, state)

    report = {
        "run_at": now_iso,
        "dry_run": dry_run,
        "results": [asdict(r) for r in results if r.status != "skipped"],
        "purged": purged,
    }
    rd = Path(cfg.report_dir)
    rd.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y-%m-%d_%H%M")
    (rd / f"report_{stamp}.json").write_text(json.dumps(report, indent=2))
    summary = build_summary(cfg, results, purged, dry_run)
    (rd / f"summary_{stamp}.txt").write_text(summary)
    print(summary)

    failed = any(r.status == "failed" for r in results)
    acted = any(r.status in ("verified", "failed") for r in results) or purged
    if send and (acted or failed):
        title = cfg.summary_title + (": problems" if failed else "")
        out = notify(title, summary, priority=4 if failed else 3,
                     tags=["warning"] if failed else ["white_check_mark"],
                     config=NotifyConfig.from_dict(cfg.notify))
        log.info("notify: %s", out)
    return 2 if failed else 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--config", required=True)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--recheck", action="store_true", help="re-verify files that failed before")
    ap.add_argument("--no-notify", action="store_true")
    ap.add_argument("-v", "--verbose", action="store_true")
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO,
                        format="%(asctime)s %(levelname)s %(message)s")
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
