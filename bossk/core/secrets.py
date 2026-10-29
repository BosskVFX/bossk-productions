"""Secrets broker. Keys live in the core only; machine agents never see one.

Resolution order for `get("ELEVENLABS_API_KEY")`:
  1. environment variable of that name
  2. the secrets file (BOSSK_SECRETS_FILE, default ~/.bossk/secrets.env, chmod 600) as KEY=value lines
  3. 1Password CLI: `op read op://<vault>/<item>/<field>` when BOSSK_OP_VAULT is set and `op` is installed
Values are cached in memory for the process lifetime and never written to the ledger.
"""
from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path


class Secrets:
    def __init__(self, file: str | Path | None = None, op_vault: str | None = None):
        self.file = Path(file or os.environ.get("BOSSK_SECRETS_FILE", "~/.bossk/secrets.env")).expanduser()
        self.op_vault = op_vault or os.environ.get("BOSSK_OP_VAULT")
        self._cache: dict[str, str] = {}
        self._file_cache: dict[str, str] | None = None

    def _from_file(self) -> dict[str, str]:
        if self._file_cache is None:
            self._file_cache = {}
            if self.file.exists():
                for line in self.file.read_text().splitlines():
                    line = line.strip()
                    if not line or line.startswith("#") or "=" not in line:
                        continue
                    k, v = line.split("=", 1)
                    self._file_cache[k.strip()] = v.strip().strip("\"'")
        return self._file_cache

    def get(self, name: str, default: str | None = None) -> str | None:
        if name in self._cache:
            return self._cache[name]
        val = os.environ.get(name) or self._from_file().get(name)
        if not val and self.op_vault and shutil.which("op"):
            item = name.lower().replace("_", "-")
            try:
                r = subprocess.run(["op", "read", f"op://{self.op_vault}/{item}/credential"],
                                   capture_output=True, text=True, timeout=20)
                if r.returncode == 0 and r.stdout.strip():
                    val = r.stdout.strip()
            except (OSError, subprocess.TimeoutExpired):
                val = None
        if val:
            self._cache[name] = val
            return val
        return default

    def require(self, name: str) -> str:
        v = self.get(name)
        if not v:
            raise KeyError(f"secret {name} not found (env, {self.file}, or 1Password)")
        return v
