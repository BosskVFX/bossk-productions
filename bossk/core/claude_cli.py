"""BOSSK's brain on Jason's Claude subscription, not the separately billed API.

Every model call goes through headless Claude Code (`claude -p`), which bills against the Claude plan the
machine is logged in with. On the NAS that login is CLAUDE_CODE_OAUTH_TOKEN, a one-year token made with
`claude setup-token` on any machine signed in to the Max account.

Guarantees, from the Claude Code authentication docs:
  * ANTHROPIC_API_KEY beats the subscription whenever it is present, and in -p mode it is always used.
    ANTHROPIC_AUTH_TOKEN, apiKeyHelper and the Bedrock/Vertex/Foundry switches also outrank it.
    So the child process gets an environment with all of those removed. There is no code path left that
    can bill the API.
  * --bare never reads OAuth credentials, so it is never passed.
Usage counts against the plan's five-hour and weekly limits, shared with the Claude app.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import threading
from dataclasses import dataclass
from typing import Callable, Iterator

API_BILLING_VARS = ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "CLAUDE_CODE_USE_BEDROCK", "CLAUDE_CODE_USE_VERTEX",
                    "CLAUDE_CODE_USE_FOUNDRY", "AWS_BEARER_TOKEN_BEDROCK")


@dataclass
class Reply:
    text: str
    session_id: str = ""
    ok: bool = True
    error: str = ""


class ClaudeCLI:
    def __init__(self, *, binary: str | None = None, oauth_token: str | None = None, model: str = "sonnet",
                 cwd: str | None = None, allowed_tools: tuple[str, ...] = (), timeout: float = 120.0):
        self.binary = binary or shutil.which("claude") or "claude"
        self.oauth_token = oauth_token
        self.model = model
        self.cwd = cwd if cwd and os.path.isdir(cwd) else None
        self.allowed_tools = allowed_tools
        self.timeout = timeout

    @property
    def available(self) -> bool:
        return bool(shutil.which(self.binary) or os.path.exists(self.binary))

    def env(self) -> dict[str, str]:
        e = {k: v for k, v in os.environ.items() if k not in API_BILLING_VARS}
        if self.oauth_token:
            e["CLAUDE_CODE_OAUTH_TOKEN"] = self.oauth_token
        e.setdefault("CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC", "1")
        return e

    def _cmd(self, fmt: str, *, system: str | None, resume: str | None, model: str | None, max_turns: int | None,
             tools: tuple[str, ...] | None) -> list[str]:
        cmd = [self.binary, "-p", "--output-format", fmt, "--model", model or self.model]
        if fmt == "stream-json":
            cmd += ["--verbose", "--include-partial-messages"]
        if system:
            cmd += ["--append-system-prompt", system]
        if resume:
            cmd += ["--resume", resume]
        if max_turns:
            cmd += ["--max-turns", str(max_turns)]
        t = self.allowed_tools if tools is None else tools
        if t:
            cmd += ["--allowedTools", ",".join(t)]
        cmd += ["--disallowedTools", "Bash,Edit,Write,MultiEdit,NotebookEdit,WebFetch,WebSearch,Task"]
        return cmd

    def ask(self, prompt: str, *, system: str | None = None, resume: str | None = None, model: str | None = None,
            max_turns: int | None = 1, tools: tuple[str, ...] | None = ()) -> Reply:
        """One-shot call, whole answer at once. Used by the reviewer."""
        cmd = self._cmd("json", system=system, resume=resume, model=model, max_turns=max_turns, tools=tools)
        try:
            r = subprocess.run(cmd, input=prompt, capture_output=True, text=True, timeout=self.timeout, env=self.env(), cwd=self.cwd)
        except (OSError, subprocess.TimeoutExpired) as e:
            return Reply("", ok=False, error=str(e))
        try:
            data = json.loads(r.stdout.strip().splitlines()[-1]) if r.stdout.strip() else {}
        except (json.JSONDecodeError, IndexError):
            data = {}
        if r.returncode != 0 or data.get("is_error"):
            return Reply(str(data.get("result", "")), session_id=str(data.get("session_id", "")), ok=False,
                         error=(r.stderr.strip() or str(data.get("result", "")) or f"exit {r.returncode}")[:400])
        return Reply(str(data.get("result", "")), session_id=str(data.get("session_id", "")))

    def stream(self, prompt: str, *, system: str | None = None, resume: str | None = None, model: str | None = None,
               max_turns: int | None = 4, tools: tuple[str, ...] | None = None,
               cancel: threading.Event | None = None, meta: dict | None = None) -> Iterator[str]:
        """Yield text as Claude writes it. Used for voice, so speech can start on the first sentence.
        `meta` receives session_id and error. Setting `cancel` kills the process."""
        meta = meta if meta is not None else {}
        cmd = self._cmd("stream-json", system=system, resume=resume, model=model, max_turns=max_turns, tools=tools)
        try:
            p = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                                 env=self.env(), cwd=self.cwd, bufsize=1)
        except OSError as e:
            meta["error"] = str(e)
            return
        assert p.stdin and p.stdout
        p.stdin.write(prompt)
        p.stdin.close()
        done = threading.Event()
        if cancel is not None:
            def watch():
                while not done.is_set():
                    if cancel.wait(0.1):
                        if p.poll() is None:
                            p.kill()
                        return
            threading.Thread(target=watch, daemon=True).start()
        streamed = False
        try:
            for line in p.stdout:
                line = line.strip()
                if not line:
                    continue
                try:
                    ev = json.loads(line)
                except json.JSONDecodeError:
                    continue
                t = ev.get("type")
                if ev.get("session_id"):
                    meta["session_id"] = ev["session_id"]
                if t == "stream_event":
                    d = (ev.get("event") or {}).get("delta") or {}
                    if d.get("type") == "text_delta" and d.get("text"):
                        streamed = True
                        yield d["text"]
                elif t == "assistant" and not streamed:
                    for block in (ev.get("message") or {}).get("content", []):
                        if block.get("type") == "text" and block.get("text"):
                            yield block["text"]
                elif t == "result":
                    if ev.get("is_error"):
                        meta["error"] = str(ev.get("result", "error"))[:400]
                    elif not streamed and ev.get("result") and not meta.get("_yielded_assistant"):
                        pass
        finally:
            if p.poll() is None:
                try:
                    p.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    p.kill()
            done.set()   # release the watcher without touching the caller's cancel flag
            err = p.stderr.read() if p.stderr else ""
            if p.returncode not in (0, None, -9) and not meta.get("error"):
                meta["error"] = (err.strip() or f"exit {p.returncode}")[:400]


def sentences(chunks: Iterator[str], min_len: int = 12) -> Iterator[str]:
    """Group streamed text into speakable sentences."""
    buf = ""
    for c in chunks:
        buf += c
        while True:
            idx = -1
            for i, ch in enumerate(buf):
                if ch in ".!?\n" and i + 1 >= min_len and (i + 1 == len(buf) or buf[i + 1] in " \n\"')"):
                    idx = i
                    break
            if idx < 0:
                break
            s, buf = buf[: idx + 1].strip(), buf[idx + 1:]
            if s:
                yield s
    if buf.strip():
        yield buf.strip()
