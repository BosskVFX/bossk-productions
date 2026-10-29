"""Phone notifications for Jarvis.

One function, `notify()`, fans out to whichever channels are configured.
Channels (all optional, set the env var or config key to enable):

  JARVIS_NTFY_URL      e.g. https://ntfy.sh/bossk-jarvis-7f3a  (or a self-hosted ntfy on the NAS)
  JARVIS_PUSHOVER_USER + JARVIS_PUSHOVER_TOKEN
  JARVIS_SLACK_WEBHOOK  (the same kind of incoming webhook the website already uses)

ntfy is the recommended channel: free, has an iOS app, supports action buttons
(the approve/deny buttons on supervisor pings use them) and can be self-hosted
on the TrueNAS box behind Tailscale.
"""
from __future__ import annotations

import json
import os
import urllib.request
from dataclasses import dataclass, field
from typing import Iterable


@dataclass
class Action:
    """An ntfy action button. `url` is opened (view) or POSTed to (http)."""
    label: str
    url: str
    method: str = "POST"
    body: str = ""
    clear: bool = True

    def header_value(self) -> str:
        if self.method.upper() == "GET" and not self.body:
            return f"view, {self.label}, {self.url}, clear={str(self.clear).lower()}"
        parts = [f"http, {self.label}, {self.url}", f"method={self.method.upper()}"]
        if self.body:
            parts.append("body=" + self.body.replace(",", "\\,"))
        parts.append(f"clear={str(self.clear).lower()}")
        return ", ".join(parts)


@dataclass
class NotifyConfig:
    ntfy_url: str | None = field(default_factory=lambda: os.environ.get("JARVIS_NTFY_URL"))
    ntfy_token: str | None = field(default_factory=lambda: os.environ.get("JARVIS_NTFY_TOKEN"))
    pushover_user: str | None = field(default_factory=lambda: os.environ.get("JARVIS_PUSHOVER_USER"))
    pushover_token: str | None = field(default_factory=lambda: os.environ.get("JARVIS_PUSHOVER_TOKEN"))
    slack_webhook: str | None = field(default_factory=lambda: os.environ.get("JARVIS_SLACK_WEBHOOK"))

    @classmethod
    def from_dict(cls, d: dict | None) -> "NotifyConfig":
        d = d or {}
        cfg = cls()
        for k in ("ntfy_url", "ntfy_token", "pushover_user", "pushover_token", "slack_webhook"):
            if d.get(k):
                setattr(cfg, k, d[k])
        return cfg

    def enabled_channels(self) -> list[str]:
        out = []
        if self.ntfy_url:
            out.append("ntfy")
        if self.pushover_user and self.pushover_token:
            out.append("pushover")
        if self.slack_webhook:
            out.append("slack")
        return out


def _post(url: str, data: bytes, headers: dict, timeout: float = 15.0) -> int:
    req = urllib.request.Request(url, data=data, headers=headers, method="POST")
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.status


def notify(
    title: str,
    body: str,
    *,
    priority: int = 3,          # ntfy scale 1..5, 5 = urgent (bypasses iPhone focus if you allow it)
    tags: Iterable[str] = (),
    actions: Iterable[Action] = (),
    click_url: str | None = None,
    config: NotifyConfig | None = None,
) -> dict[str, str]:
    """Send to every configured channel. Returns {channel: 'ok' | 'error: ...'}.

    Never raises: a dead notification channel must not break the job that called it.
    """
    cfg = config or NotifyConfig()
    results: dict[str, str] = {}
    if not cfg.enabled_channels():
        results["none"] = "no channel configured (set JARVIS_NTFY_URL)"
        return results

    if cfg.ntfy_url:
        try:
            headers = {
                "Title": title.encode("ascii", "ignore").decode(),
                "Priority": str(max(1, min(5, priority))),
                "Content-Type": "text/plain; charset=utf-8",
            }
            if tags:
                headers["Tags"] = ",".join(tags)
            if click_url:
                headers["Click"] = click_url
            acts = [a.header_value() for a in actions]
            if acts:
                headers["Actions"] = "; ".join(acts)
            if cfg.ntfy_token:
                headers["Authorization"] = f"Bearer {cfg.ntfy_token}"
            _post(cfg.ntfy_url, body.encode("utf-8"), headers)
            results["ntfy"] = "ok"
        except Exception as e:  # noqa: BLE001
            results["ntfy"] = f"error: {e}"

    if cfg.pushover_user and cfg.pushover_token:
        try:
            payload = {
                "token": cfg.pushover_token,
                "user": cfg.pushover_user,
                "title": title,
                "message": body[:1024],
                "priority": {1: -2, 2: -1, 3: 0, 4: 1, 5: 1}.get(priority, 0),
            }
            if click_url:
                payload["url"] = click_url
            _post(
                "https://api.pushover.net/1/messages.json",
                json.dumps(payload).encode(),
                {"Content-Type": "application/json"},
            )
            results["pushover"] = "ok"
        except Exception as e:  # noqa: BLE001
            results["pushover"] = f"error: {e}"

    if cfg.slack_webhook:
        try:
            text = f"*{title}*\n{body}"
            if click_url:
                text += f"\n<{click_url}|Open>"
            _post(cfg.slack_webhook, json.dumps({"text": text}).encode(), {"Content-Type": "application/json"})
            results["slack"] = "ok"
        except Exception as e:  # noqa: BLE001
            results["slack"] = f"error: {e}"

    return results
