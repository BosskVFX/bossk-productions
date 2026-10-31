#!/usr/bin/env python3
"""BOSSK hook for Claude Code. Standard library only. One file handles every hook event:

  PermissionRequest  -> asks the core; returns allow/deny, or nothing so the normal prompt shows
  PreToolUse         -> logs the call to the core (never blocks)
  Notification, Stop, SessionStart, SessionEnd -> tells the core what the session is doing

Install:  python3 bossk_hook.py install --core http://truenas:8750 --token <BOSSK_TOKEN> --machine mini [--can-speak]
          writes ~/.bossk/agent.json and merges the hooks into ~/.claude/settings.json

Fail-closed: if the core is unreachable or anything throws during a PermissionRequest, the hook
prints an explicit deny with the reason. Claude Code treats a crashed hook as "proceed", so the deny
has to be explicit. Set "offline": "ask" in agent.json to fall back to the local prompt instead.
"""
from __future__ import annotations

import json
import os
import socket
import sys
import urllib.error
import urllib.request
from pathlib import Path

AGENT_FILE = Path(os.environ.get("BOSSK_AGENT_FILE", "~/.bossk/agent.json")).expanduser()
HOOK_TIMEOUT = 600          # seconds; Claude Code's default for command hooks
WAIT_SECONDS = 540          # how long the core may hold the request before we give the prompt back


def load_agent() -> dict:
    try:
        return json.loads(AGENT_FILE.read_text())
    except (OSError, json.JSONDecodeError):
        return {}


def post(agent: dict, path: str, body: dict, timeout: float) -> dict:
    req = urllib.request.Request(agent["core"].rstrip("/") + path, data=json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json", "Authorization": f"Bearer {agent['token']}"},
                                 method="POST")
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read() or b"{}")


def transcript_tail(path: str | None, max_chars: int = 6000) -> str:
    """Last user and assistant text from the session transcript, for the reviewer."""
    if not path:
        return ""
    try:
        lines = Path(path).read_text(errors="replace").splitlines()[-80:]
    except OSError:
        return ""
    out = []
    for line in lines:
        try:
            rec = json.loads(line)
        except json.JSONDecodeError:
            continue
        msg = rec.get("message") or {}
        role = msg.get("role") or rec.get("type")
        content = msg.get("content")
        if isinstance(content, list):
            parts = []
            for c in content:
                if isinstance(c, dict):
                    if c.get("type") == "text":
                        parts.append(c.get("text", ""))
                    elif c.get("type") == "tool_use":
                        parts.append(f"[tool {c.get('name')}: {json.dumps(c.get('input'))[:200]}]")
            content = "\n".join(p for p in parts if p)
        if isinstance(content, str) and content.strip() and role in ("user", "assistant"):
            out.append(f"{role}: {content.strip()[:800]}")
    return "\n".join(out)[-max_chars:]


def emit(obj: dict | None) -> None:
    if obj is not None:
        sys.stdout.write(json.dumps(obj))
    sys.stdout.flush()


def permission_request(agent: dict, ev: dict) -> None:
    def deny(reason: str) -> None:
        emit({"hookSpecificOutput": {"hookEventName": "PermissionRequest",
                                     "decision": {"behavior": "deny", "message": f"BOSSK: {reason}"}}})

    body = {"machine": agent.get("machine", socket.gethostname()), "session_id": ev.get("session_id"),
            "session_name": Path(ev.get("cwd") or "").name, "tool_name": ev.get("tool_name"),
            "tool_input": ev.get("tool_input") or {}, "cwd": ev.get("cwd"),
            "transcript_tail": transcript_tail(ev.get("transcript_path")), "wait_seconds": WAIT_SECONDS,
            "hook": "PermissionRequest"}
    try:
        res = post(agent, "/decide", body, timeout=WAIT_SECONDS + 30)
    except (urllib.error.URLError, OSError, ValueError) as e:
        if agent.get("offline") == "ask":
            return emit(None)         # fall back to the local prompt
        return deny(f"core unreachable ({e}); denying by policy")
    behavior = res.get("behavior")
    if behavior == "allow":
        emit({"hookSpecificOutput": {"hookEventName": "PermissionRequest",
                                     "decision": {"behavior": "allow", "updatedInput": ev.get("tool_input") or {}}}})
    elif behavior == "deny":
        deny(res.get("reason", "denied"))
    else:
        emit(None)                    # pending: let Claude Code show its own prompt; the ask stays open on the phone


def pre_tool_use(agent: dict, ev: dict) -> None:
    try:
        post(agent, "/log", {"kind": "tool", "machine": agent.get("machine"), "session_id": ev.get("session_id"),
                             "tool_name": ev.get("tool_name"), "tool_input": ev.get("tool_input") or {},
                             "cwd": ev.get("cwd"), "tool_use_id": ev.get("tool_use_id")}, timeout=3)
    except Exception:  # noqa: BLE001
        pass
    emit(None)


def session_event(agent: dict, ev: dict) -> None:
    try:
        post(agent, "/events", {"machine": agent.get("machine"), "session_id": ev.get("session_id"),
                                "event": ev.get("hook_event_name"), "cwd": ev.get("cwd"),
                                "message": ev.get("message") or ev.get("last_assistant_message") or "",
                                "notification_type": ev.get("notification_type"),
                                "background_tasks": ev.get("background_tasks")}, timeout=3)
    except Exception:  # noqa: BLE001
        pass
    emit(None)


# ----------------------------------------------------------------------------- install
HOOK_EVENTS = {
    "PermissionRequest": {"matcher": "*", "timeout": HOOK_TIMEOUT},
    "PreToolUse": {"matcher": "*", "timeout": 5},
    "Notification": {"matcher": "", "timeout": 5},
    "Stop": {"matcher": "", "timeout": 5},
    "SessionStart": {"matcher": "", "timeout": 5},
    "SessionEnd": {"matcher": "", "timeout": 5},
}


def install(argv: list[str]) -> int:
    import argparse
    ap = argparse.ArgumentParser(prog="bossk_hook.py install")
    ap.add_argument("--core", required=True, help="core URL over Tailscale, e.g. http://truenas:8750")
    ap.add_argument("--token", required=True)
    ap.add_argument("--machine", default=socket.gethostname().split(".")[0])
    ap.add_argument("--can-speak", action="store_true", help="this machine runs the voice daemon")
    ap.add_argument("--offline", choices=["deny", "ask"], default="deny", help="what to do when the core is unreachable")
    ap.add_argument("--settings", default="~/.claude/settings.json")
    ap.add_argument("--knowledge", default="", help="path to the shared knowledge folder as this machine sees it, "
                    "e.g. /Volumes/bossk/knowledge on a Mac or Z:/bossk/knowledge on the PC")
    ap.add_argument("--claude-md", default="~/.claude/CLAUDE.md")
    a = ap.parse_args(argv)

    AGENT_FILE.parent.mkdir(parents=True, exist_ok=True)
    AGENT_FILE.write_text(json.dumps({"core": a.core, "token": a.token, "machine": a.machine,
                                      "can_speak": a.can_speak, "offline": a.offline}, indent=2))
    try:
        os.chmod(AGENT_FILE, 0o600)
    except OSError:
        pass

    settings_path = Path(a.settings).expanduser()
    settings = {}
    if settings_path.exists():
        settings = json.loads(settings_path.read_text() or "{}")
    hooks = settings.setdefault("hooks", {})
    me = str(Path(__file__).resolve())
    py = sys.executable or "python3"
    for event, spec in HOOK_EVENTS.items():
        entries = [e for e in hooks.get(event, []) if "bossk_hook" not in json.dumps(e)]
        entry = {"hooks": [{"type": "command", "command": f'"{py}" "{me}"', "timeout": spec["timeout"]}]}
        if spec["matcher"]:
            entry["matcher"] = spec["matcher"]
        entries.append(entry)
        hooks[event] = entries
    if a.knowledge:
        link_memory(a.knowledge, a.machine, settings, Path(a.claude_md).expanduser())
    settings_path.parent.mkdir(parents=True, exist_ok=True)
    settings_path.write_text(json.dumps(settings, indent=2))
    print(f"BOSSK agent config: {AGENT_FILE}\nhooks merged into: {settings_path}\nmachine: {a.machine}  can_speak: {a.can_speak}")
    if a.knowledge:
        print(f"memory: {a.claude_md} imports {a.knowledge}/CLAUDE.md; auto memory -> {a.knowledge}/machines/{a.machine}/memory")
    return 0


MEMORY_BEGIN = "<!-- BOSSK shared memory: managed by bossk_hook.py install -->"
MEMORY_END = "<!-- /BOSSK shared memory -->"


def link_memory(knowledge: str, machine: str, settings: dict, claude_md: Path) -> None:
    """Point this machine's Claude Code at the one shared memory folder.
    ~/.claude/CLAUDE.md gets an @import of <knowledge>/CLAUDE.md (user-scope imports load without a prompt),
    and auto memory is written to <knowledge>/machines/<machine>/memory so machines never write the same file."""
    k = knowledge.replace("\\", "/").rstrip("/")
    root = Path(knowledge).expanduser()
    if not (root / "CLAUDE.md").exists():
        raise SystemExit(f"{root}/CLAUDE.md not found: mount Mother's bossk share first (the core seeds it on first start)")
    mem = root / "machines" / machine / "memory"
    mem.mkdir(parents=True, exist_ok=True)
    settings["autoMemoryDirectory"] = str(mem).replace("\\", "/")
    block = f"{MEMORY_BEGIN}\n@{k}/CLAUDE.md\n{MEMORY_END}"
    text = claude_md.read_text() if claude_md.exists() else ""
    if MEMORY_BEGIN in text and MEMORY_END in text:
        pre, rest = text.split(MEMORY_BEGIN, 1)
        post = rest.split(MEMORY_END, 1)[1]
        text = pre + block + post
    else:
        text = (text.rstrip() + "\n\n" if text.strip() else "") + block + "\n"
    claude_md.parent.mkdir(parents=True, exist_ok=True)
    claude_md.write_text(text)


def main() -> int:
    if len(sys.argv) > 1 and sys.argv[1] == "install":
        return install(sys.argv[2:])
    agent = load_agent()
    try:
        ev = json.loads(sys.stdin.read() or "{}")
    except json.JSONDecodeError:
        ev = {}
    event = ev.get("hook_event_name", "")
    if not agent.get("core") or not agent.get("token"):
        if event == "PermissionRequest":
            emit({"hookSpecificOutput": {"hookEventName": "PermissionRequest",
                                         "decision": {"behavior": "deny", "message": "BOSSK: agent not configured, run bossk_hook.py install"}}})
        return 0
    try:
        if event == "PermissionRequest":
            permission_request(agent, ev)
        elif event == "PreToolUse":
            pre_tool_use(agent, ev)
        else:
            session_event(agent, ev)
    except Exception as e:  # noqa: BLE001
        if event == "PermissionRequest":
            emit({"hookSpecificOutput": {"hookEventName": "PermissionRequest",
                                         "decision": {"behavior": "deny", "message": f"BOSSK hook error: {e}"}}})
    return 0


if __name__ == "__main__":
    sys.exit(main())
