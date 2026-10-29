"""Core configuration, from bossk.yml. Secrets never live here; see secrets.py."""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml


@dataclass
class CoreConfig:
    host: str = "0.0.0.0"
    port: int = 8750
    public_url: str = "http://bossk-core:8750"      # how the iPhone reaches the core over Tailscale (ntfy buttons)
    data_dir: str = "~/.bossk"
    rulebook: str = "rulebook/rulebook.yml"
    ledger: str = "ledger.sqlite"
    machine_name: str = "truenas"
    reviewer_model: str = "claude-sonnet-5-5"
    reviewer_enabled: bool = True
    voice_grace_seconds: int = 90                     # voice ask unanswered this long -> phone too
    ask_expire_minutes: int = 240
    max_wait_seconds: int = 540                       # cap on how long /decide blocks for an answer (under the hook timeout)
    stale_machine_seconds: int = 120
    knowledge_dir: str = ""                            # the central markdown folder (phase 5); "" until it exists
    voice_id: str = ""                                 # ElevenLabs voice id (or ELEVENLABS_VOICE_ID in secrets.env)
    notify: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def load(cls, path: str | Path | None) -> "CoreConfig":
        raw: dict[str, Any] = {}
        if path and Path(path).exists():
            raw = yaml.safe_load(Path(path).read_text()) or {}
        unknown = set(raw) - set(cls.__dataclass_fields__)
        if unknown:
            raise SystemExit(f"unknown config keys in {path}: {sorted(unknown)}")
        cfg = cls(**raw)
        cfg.data_dir = str(Path(cfg.data_dir).expanduser())
        base = Path(path).parent if path else Path(".")
        if not Path(cfg.rulebook).is_absolute():
            cfg.rulebook = str((base / cfg.rulebook).resolve()) if (base / cfg.rulebook).exists() else str(Path(cfg.rulebook).resolve())
        if not Path(cfg.ledger).is_absolute():
            cfg.ledger = str(Path(cfg.data_dir) / cfg.ledger)
        for k in ("port", "voice_grace_seconds", "ask_expire_minutes", "max_wait_seconds"):
            env = os.environ.get("BOSSK_" + k.upper())
            if env:
                setattr(cfg, k, int(env))
        if os.environ.get("BOSSK_PUBLIC_URL"):
            cfg.public_url = os.environ["BOSSK_PUBLIC_URL"]
        return cfg
