"""The rulebook: deterministic, sub-second tiering of a permission request.

Tiers
  0  never      hard deny, no human can approve it through BOSSK (rm -rf /, format a disk)
  1  allow      routine, approved instantly
  2  review     the reviewer agent reads intent and diff, then allows or escalates
  3  ask        Jason, by voice at the desk or by phone when away

Matching is on tool name plus a few fields of the tool input. Tier 3 and tier 0 rules
are checked first so a dangerous command can never be rescued by a broad allow rule.
Everything unmatched is tier 2.

Rule shape (YAML):
  - id: push-main
    tier: 3
    match:
      tool: "Bash"                       # regex, anchored to the whole tool name
      command: "git push .*\\b(main|master)\\b"   # regex, searched in tool_input.command
      path_under: ["/mnt/tank/clients"]  # tool_input.file_path (or paths in command) under one of these
    unless:
      path_under: ["/mnt/tank/video/_holding"]
    confirm: true                        # tier 3 only: BOSSK repeats the figure and waits for "confirm"
    reason: "pushes to main deploy the site"
"""
from __future__ import annotations

import re
import shlex
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import Any

import yaml

TIER_NAMES = {0: "never", 1: "allow", 2: "review", 3: "ask"}


@dataclass
class Rule:
    id: str
    tier: int
    match: dict[str, Any] = field(default_factory=dict)
    unless: dict[str, Any] = field(default_factory=dict)
    confirm: bool = False
    reason: str = ""

    def __post_init__(self):
        if self.tier not in TIER_NAMES:
            raise ValueError(f"rule {self.id}: tier must be 0..3")
        self._tool_re = re.compile(f"^(?:{self.match['tool']})$") if self.match.get("tool") else None
        self._cmd_re = re.compile(self.match["command"], re.I) if self.match.get("command") else None
        self._unless_cmd_re = re.compile(self.unless["command"], re.I) if self.unless.get("command") else None


@dataclass
class Decision:
    tier: int
    rule_id: str | None
    reason: str
    confirm: bool = False

    @property
    def tier_name(self) -> str:
        return TIER_NAMES[self.tier]


# ----------------------------------------------------------------------------- helpers
_PATH_TOKEN = re.compile(r"""(?:^|[\s=:"'])((?:[A-Za-z]:\\|\\\\|/|~/|\./|\.\./)[^\s"'|;&<>]+)""")


def paths_in(tool_input: dict[str, Any]) -> list[str]:
    """Paths a tool call touches: explicit path fields plus path-looking tokens in a shell command."""
    out: list[str] = []
    for k in ("file_path", "path", "notebook_path", "target", "destination"):
        v = tool_input.get(k)
        if isinstance(v, str) and v:
            out.append(v)
    cmd = tool_input.get("command")
    if isinstance(cmd, str):
        for m in _PATH_TOKEN.finditer(" " + cmd):
            out.append(m.group(1))
        try:
            for tok in shlex.split(cmd, posix=True):
                if tok.startswith(("/", "~/", "./", "../")) or re.match(r"^[A-Za-z]:\\", tok):
                    out.append(tok)
        except ValueError:
            pass
    return list(dict.fromkeys(out))


_DRIVE = re.compile(r"^[A-Za-z]:[\\/]")
_HOME = re.compile(r"^(?:/users/[^/]+|/home/[^/]+|/root|[a-z]:/users/[^/]+)(?=/|$)", re.I)


def _norm(p: str, cwd: str | None) -> str:
    """Normalise a path for prefix comparison.

    Home directories collapse to "~" whichever machine the path came from, so a rule
    written as ~/.ssh matches /Users/jason/.ssh, /home/jason/.ssh and C:/Users/jason/.ssh.
    Windows paths are lower-cased and use forward slashes.
    """
    p = p.strip().strip("\"'")
    if p.startswith("~"):
        return "~" + p[1:].replace("\\", "/").rstrip("/")
    if _DRIVE.match(p) or "\\" in p:
        s = p.replace("\\", "/").lower()
        return _HOME.sub("~", s).rstrip("/") or "/"
    pp = PurePosixPath(p)
    if not pp.is_absolute() and cwd:
        pp = PurePosixPath(cwd) / pp
    parts: list[str] = []
    for part in pp.parts:
        if part == "..":
            if len(parts) > 1:
                parts.pop()
        elif part != ".":
            parts.append(part)
    s = str(PurePosixPath(*parts)) if parts else "/"
    return _HOME.sub("~", s)


def path_under(p: str, roots: list[str], cwd: str | None) -> bool:
    np_ = _norm(p, cwd)
    for r in roots:
        if "$cwd" in r:
            if not cwd:
                continue
            r = r.replace("$cwd", cwd)
        nr = _norm(r, cwd).rstrip("/")
        if np_ == nr or np_.startswith(nr + "/"):
            return True
    return False


def _clause_matches(rule: Rule, clause: dict[str, Any], tool_re, cmd_re, tool: str,
                    tool_input: dict[str, Any], cwd: str | None) -> bool:
    if not clause:
        return False
    if tool_re and not tool_re.match(tool):
        return False
    cmd = tool_input.get("command") if isinstance(tool_input.get("command"), str) else ""
    if cmd_re and not cmd_re.search(cmd or ""):
        return False
    roots = clause.get("path_under")
    if roots:
        ps = paths_in(tool_input)
        if not ps:
            # no path in the call: only match if the clause is purely path-based on an explicit field
            if clause.get("require_path", True):
                return False
        elif not all(path_under(p, roots, cwd) for p in ps):
            return False
    url_re = clause.get("url")
    if url_re:
        url = tool_input.get("url") or ""
        if not re.search(url_re, str(url), re.I):
            return False
    return True


# ----------------------------------------------------------------------------- rulebook
class Rulebook:
    def __init__(self, rules: list[Rule], defaults: dict[str, Any] | None = None):
        self.rules = rules
        self.defaults = {"unknown_tier": 2, "idle_minutes_away": 10, "ask_timeout_minutes": 240,
                         "hold_days": 7, **(defaults or {})}
        ids = [r.id for r in rules]
        dupes = {i for i in ids if ids.count(i) > 1}
        if dupes:
            raise ValueError(f"duplicate rule ids: {sorted(dupes)}")

    @classmethod
    def load(cls, path: str | Path) -> "Rulebook":
        raw = yaml.safe_load(Path(path).read_text()) or {}
        return cls.from_dict(raw)

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "Rulebook":
        rules = [Rule(id=r["id"], tier=int(r["tier"]), match=r.get("match") or {}, unless=r.get("unless") or {},
                      confirm=bool(r.get("confirm", False)), reason=r.get("reason", ""))
                 for r in raw.get("rules", [])]
        return cls(rules, raw.get("defaults"))

    def decide(self, tool: str, tool_input: dict[str, Any] | None, cwd: str | None = None) -> Decision:
        tool_input = tool_input or {}
        # Most restrictive tiers first: 0, then 3, then 1. Unmatched -> tier 2.
        for want in (0, 3, 1):
            for r in self.rules:
                if r.tier != want:
                    continue
                if not _clause_matches(r, r.match, r._tool_re, r._cmd_re, tool, tool_input, cwd):
                    continue
                if r.unless and _clause_matches(r, r.unless, None, r._unless_cmd_re, tool, tool_input, cwd):
                    continue
                return Decision(tier=r.tier, rule_id=r.id, reason=r.reason or r.id, confirm=r.confirm and r.tier == 3)
        return Decision(tier=int(self.defaults["unknown_tier"]), rule_id=None, reason="no rule matched")

    def replay(self, prompts: list[dict], other: "Rulebook") -> dict[str, Any]:
        """Compare this rulebook against `other` over logged prompt events.

        Returns the prompts whose tier would change, grouped by direction. The ones that go
        from 2/3 down to 1 are the ones to read before accepting a change: they are new auto-approvals.
        """
        newly_allowed, newly_restricted, same = [], [], 0
        for ev in prompts:
            d = ev.get("data", {})
            tool, ti, cwd = d.get("tool_name") or ev.get("tool") or "", d.get("tool_input") or {}, d.get("cwd")
            old, new = other.decide(tool, ti, cwd), self.decide(tool, ti, cwd)
            if old.tier == new.tier:
                same += 1
                continue
            entry = {"ts": ev.get("ts"), "tool": tool, "input": _short(ti), "from": old.tier, "to": new.tier,
                     "old_rule": old.rule_id, "new_rule": new.rule_id}
            (newly_allowed if new.tier < old.tier else newly_restricted).append(entry)
        return {"unchanged": same, "newly_allowed": newly_allowed, "newly_restricted": newly_restricted}


def _short(ti: dict[str, Any]) -> str:
    for k in ("command", "file_path", "url", "path", "pattern"):
        if ti.get(k):
            return f"{k}={str(ti[k])[:160]}"
    return str(ti)[:160]
