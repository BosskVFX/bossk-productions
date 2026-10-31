"""The shared knowledge folder: seeding it from the template, and writing BOSSK's daily log note from the ledger."""
from __future__ import annotations

import shutil
import threading
import time
from datetime import datetime
from pathlib import Path

TEMPLATE = Path(__file__).resolve().parents[1] / "knowledge-template"


def seed(knowledge_dir: str | Path) -> list[str]:
    """Copy template files that do not exist yet. Never overwrites Jason's notes."""
    root = Path(knowledge_dir)
    created = []
    for src in TEMPLATE.rglob("*"):
        if src.is_dir():
            continue
        dst = root / src.relative_to(TEMPLATE)
        if not dst.exists():
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dst)
            created.append(str(dst.relative_to(root)))
    return created


def daily_log(ledger, day: datetime | None = None) -> str:
    day = day or datetime.now()
    start = datetime(day.year, day.month, day.day).timestamp()
    events = list(reversed(ledger.query(since=start, until=start + 86400, limit=5000)))
    asks = [e for e in events if e["kind"] == "ask"]
    answers = [e for e in events if e["kind"] == "answer"]
    decisions = [e for e in events if e["kind"] == "decision"]
    auto = sum(1 for e in decisions if e["outcome"] == "allow" and e["tier"] == 1)
    reviewed = sum(1 for e in decisions if e["tier"] == 2)
    denied = sum(1 for e in decisions if e["outcome"] == "deny")
    lines = [f"# {day:%Y-%m-%d}", "", f"Auto-approved {auto}, reviewer handled {reviewed}, denied {denied}, asked Jason {len(asks)}.", ""]
    if answers:
        lines += ["## Jason's answers", ""]
        for e in answers:
            lines.append(f"- {datetime.fromtimestamp(e['ts']):%H:%M} {e['outcome']}: {e['summary']}")
        lines.append("")
    open_asks = {e["data"].get("ask_id") for e in asks} - {e["data"].get("ask_id") for e in answers}
    if open_asks:
        lines += ["## Still open", ""]
        for e in asks:
            if e["data"].get("ask_id") in open_asks:
                lines.append(f"- {datetime.fromtimestamp(e['ts']):%H:%M} {e['summary']}")
        lines.append("")
    other = [e for e in events if e["kind"] in ("pause", "rulebook", "notify", "chat", "voice_command")]
    if other:
        lines += ["## Other", ""] + [f"- {datetime.fromtimestamp(e['ts']):%H:%M} {e['kind']}: {e['summary']}" for e in other[-40:]] + [""]
    return "\n".join(lines)


def write_daily_log(knowledge_dir: str | Path, ledger, day: datetime | None = None) -> Path:
    day = day or datetime.now()
    p = Path(knowledge_dir) / "log" / f"{day:%Y-%m-%d}.md"
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".tmp")
    tmp.write_text(daily_log(ledger, day))
    tmp.replace(p)
    return p


def start_log_writer(knowledge_dir: str | Path, ledger, every_s: float = 900) -> threading.Thread:
    def loop():
        while True:
            try:
                write_daily_log(knowledge_dir, ledger)
            except OSError:
                pass
            time.sleep(every_s)
    t = threading.Thread(target=loop, daemon=True, name="daily-log")
    t.start()
    return t
