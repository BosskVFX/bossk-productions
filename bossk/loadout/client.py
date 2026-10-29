"""BOSSK -> LOADOUT bridge. Written against LOADOUT-BRIDGE.md (LOADOUT 0.2.28, commit a67ac0f).

Facts this client is built around (from that document):
  * LOADOUT is an aiohttp server on 127.0.0.1:<port>; the whole app is HTTP. port.json has the URL.
  * A bare local request with no credentials runs as the owner. Over Tailscale the server must be
    started with PORT=5500 and the hostname added to config.local.json allowed_hosts, or every
    request 421s on the Host check.
  * There is no server-side queue and nothing finishes a cloud/ComfyUI video job unless something
    polls POST /video/status. The poll that sees Success is what downloads, writes the recipe and
    ledger row, and files the result into the project. So this client polls every job to the end.
  * /video/generate returns a descriptor that is POSTed back unchanged to /video/status. On a
    moderation failover the status reply carries a new descriptor under "job"; the caller swaps.
  * The active project is global server state. Two drivers collide, so every submit here is
    serialised through one lock and sets the active project first.
  * Rule: every generation goes through prompt-master first. submit_video / submit_image /
    submit_higgsfield refuse a prompt that is not tagged with a prompt_master_id.

Standard library only.
"""
from __future__ import annotations

import json
import os
import re
import threading
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

TERMINAL_FAIL_RE = re.compile(r"^(fail(ed)?|error|cancell?ed|expired)$", re.I)
POLL_INTERVAL_S = 3.0


class LoadoutError(RuntimeError):
    pass


class PromptNotFromPromptMaster(LoadoutError):
    """Raised when a generation is attempted with a prompt that did not go through prompt-master."""


def discover_url(explicit: str | None = None) -> str:
    """LOADOUT_URL -> port.json (LOADOUT_DATA_DIR or %LOCALAPPDATA%/LOADOUT) if its pid is alive -> 127.0.0.1:5500."""
    if explicit:
        return explicit.rstrip("/")
    if os.environ.get("LOADOUT_URL"):
        return os.environ["LOADOUT_URL"].rstrip("/")
    data_dir = os.environ.get("LOADOUT_DATA_DIR") or (Path(os.environ.get("LOCALAPPDATA", "")) / "LOADOUT" if os.environ.get("LOCALAPPDATA") else None)
    if data_dir:
        pf = Path(data_dir) / "port.json"
        if pf.exists():
            try:
                rec = json.loads(pf.read_text())
                pid = int(rec.get("pid") or 0)
                alive = True
                if pid:
                    try:
                        os.kill(pid, 0)
                    except OSError:
                        alive = False
                if alive and rec.get("url"):
                    return str(rec["url"]).rstrip("/")
            except (json.JSONDecodeError, ValueError):
                pass
    return "http://127.0.0.1:5500"


@dataclass
class JobResult:
    ok: bool
    status: str
    descriptor: dict[str, Any]
    local_url: str = ""
    local_urls: list[str] = field(default_factory=list)
    files: list[dict[str, Any]] = field(default_factory=list)
    spend: Any = None
    error: str = ""
    elapsed_s: float = 0.0
    raw: dict[str, Any] = field(default_factory=dict)


class LoadoutClient:
    def __init__(self, url: str | None = None, *, timeout: float = 120.0, sleep: Callable[[float], None] = time.sleep):
        self.url = discover_url(url)
        self.timeout = timeout
        self._sleep = sleep
        self._submit_lock = threading.Lock()

    # ------------------------------------------------------------------ transport
    def call(self, method: str, path: str, body: dict[str, Any] | None = None, *, timeout: float | None = None,
             raw: bytes | None = None, content_type: str = "application/json") -> dict[str, Any]:
        data = raw if raw is not None else (json.dumps(body).encode() if body is not None else None)
        req = urllib.request.Request(self.url + path, data=data, method=method,
                                     headers={"Content-Type": content_type} if data is not None else {})
        try:
            with urllib.request.urlopen(req, timeout=timeout or self.timeout) as r:
                text = r.read()
        except urllib.error.HTTPError as e:
            detail = e.read().decode(errors="replace")[:400]
            if e.code == 421:
                raise LoadoutError(f"LOADOUT rejected Host for {self.url}: add this hostname to config.local.json allowed_hosts") from None
            if e.code == 503 and "starting" in detail:
                raise LoadoutError(f"GPU starting: {detail}") from None
            raise LoadoutError(f"{method} {path} -> {e.code}: {detail}") from None
        except urllib.error.URLError as e:
            raise LoadoutError(f"LOADOUT unreachable at {self.url}: {e.reason}") from None
        if not text:
            return {}
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            return {"_raw": text.decode(errors="replace")}

    def get(self, path: str, **kw) -> dict[str, Any]:
        return self.call("GET", path, **kw)

    def post(self, path: str, body: dict[str, Any] | None = None, **kw) -> dict[str, Any]:
        return self.call("POST", path, body or {}, **kw)

    # ------------------------------------------------------------------ status
    def status(self) -> dict[str, Any]:
        return {"config": self.get("/config"), "gpu": self.get("/gpu"), "projects": self.get("/projects")}

    def spend(self) -> dict[str, Any]:
        return self.get("/spend")

    def ledger(self, project: str = "", limit: int = 100) -> dict[str, Any]:
        q = f"?limit={limit}" + (f"&project={urllib.request.quote(project)}" if project else "")
        return self.get("/ledger" + q)

    # ------------------------------------------------------------------ projects
    def projects(self) -> list[dict[str, Any]]:
        r = self.get("/projects")
        return r.get("projects", r) if isinstance(r, dict) else r

    def set_active_project(self, project_id: str) -> dict[str, Any]:
        return self.post("/projects/active", {"id": project_id})

    def create_project(self, name: str, path: str) -> dict[str, Any]:
        return self.post("/projects", {"name": name, "path": path})

    # ------------------------------------------------------------------ gallery
    def gallery(self, limit: int = 200) -> list[dict[str, Any]]:
        r = self.get(f"/local/images?limit={limit}")
        return r.get("images", r.get("files", r)) if isinstance(r, dict) else r

    def recipe(self, rel_path: str) -> dict[str, Any]:
        return self.get("/recipe?path=" + urllib.request.quote(rel_path))

    def view_url(self, filename: str, subfolder: str = "", kind: str = "output") -> str:
        return f"{self.url}/view?filename={urllib.request.quote(filename)}&subfolder={urllib.request.quote(subfolder)}&type={kind}"

    def import_file(self, path: str | Path) -> dict[str, Any]:
        """POST /local/import (multipart). Lands in OUT/imported or <project>/ASSETS. No recipe, no ledger row."""
        p = Path(path)
        boundary = "----bossk" + os.urandom(8).hex()
        body = (f"--{boundary}\r\nContent-Disposition: form-data; name=\"file\"; filename=\"{p.name}\"\r\n"
                f"Content-Type: application/octet-stream\r\n\r\n").encode() + p.read_bytes() + f"\r\n--{boundary}--\r\n".encode()
        return self.call("POST", "/local/import", raw=body, content_type=f"multipart/form-data; boundary={boundary}")

    # ------------------------------------------------------------------ generation guard
    @staticmethod
    def _require_prompt_master(prompt_master_id: str | None) -> None:
        if not prompt_master_id:
            raise PromptNotFromPromptMaster("generation refused: prompt must come from prompt-master (pass prompt_master_id)")

    # ------------------------------------------------------------------ video
    def submit_video(self, body: dict[str, Any], *, prompt_master_id: str | None, project_id: str | None = None) -> dict[str, Any]:
        """POST /video/generate. `body` mirrors app.js VIDEO_ENGINES: {provider, prompt, inputs{...}, tier|kind|model_id|mode, ...}.
        Returns the descriptor to poll with. Serialised, and pins the active project first."""
        if body.get("provider") in VIDEO_PROVIDERS_NO_PROMPT:
            pass
        else:
            self._require_prompt_master(prompt_master_id)
        if "provider" not in body:
            raise LoadoutError("video body needs a provider")
        body = {**body, "ui_engine": (body.get("ui_engine") or f"bossk:{body['provider']}")[:60],
                "ui_params": {**(body.get("ui_params") or {}), "bossk": 1, **({"prompt_master": prompt_master_id} if prompt_master_id else {})}}
        with self._submit_lock:
            if project_id:
                self.set_active_project(project_id)
            desc = self.post("/video/generate", body)
        if not isinstance(desc, dict) or not desc.get("provider"):
            raise LoadoutError(f"unexpected /video/generate reply: {desc}")
        return desc

    def poll_video(self, descriptor: dict[str, Any], *, timeout_s: float = 3600, interval_s: float = POLL_INTERVAL_S,
                   on_status: Callable[[str, dict[str, Any]], None] | None = None) -> JobResult:
        """POST /video/status until Success or a terminal failure. This poll is what makes LOADOUT download
        the file, write the recipe and ledger row, and file it into the project. Swaps descriptors on failover."""
        t0 = time.time()
        desc = dict(descriptor)
        last = ""
        while True:
            r = self.post("/video/status", desc)
            st = str(r.get("status", ""))
            if st != last and on_status:
                on_status(st, r)
            last = st
            if isinstance(r.get("job"), dict) and r["job"].get("provider"):
                desc = dict(r["job"])            # moderation failover: continue with the new handle
            if st == "Success":
                return JobResult(ok=True, status=st, descriptor=desc, local_url=r.get("local_url", ""),
                                 local_urls=r.get("local_urls") or ([r["local_url"]] if r.get("local_url") else []),
                                 spend=r.get("spend"), elapsed_s=time.time() - t0, raw=r)
            if st == "Fail" or TERMINAL_FAIL_RE.match(st):
                return JobResult(ok=False, status=st, descriptor=desc, error=str(r.get("error", st)), elapsed_s=time.time() - t0, raw=r)
            if time.time() - t0 > timeout_s:
                return JobResult(ok=False, status="timeout", descriptor=desc, error=f"no result after {timeout_s}s (last: {st})",
                                 elapsed_s=time.time() - t0, raw=r)
            self._sleep(interval_s)

    def cancel_video(self, descriptor: dict[str, Any]) -> dict[str, Any]:
        return self.post("/video/cancel", descriptor)

    def render_video(self, body: dict[str, Any], *, prompt_master_id: str | None, project_id: str | None = None,
                     timeout_s: float = 3600, on_status=None) -> JobResult:
        return self.poll_video(self.submit_video(body, prompt_master_id=prompt_master_id, project_id=project_id),
                               timeout_s=timeout_s, on_status=on_status)

    # ------------------------------------------------------------------ images
    def submit_image(self, prompt: str, *, prompt_master_id: str | None, model: str = "nano-banana-pro", aspect_ratio: str = "1:1",
                     image_size: str = "2K", n: int = 1, references: list[str] | None = None, project_id: str | None = None) -> str:
        self._require_prompt_master(prompt_master_id)
        body = {"prompt": prompt, "model": model, "aspect_ratio": aspect_ratio, "image_size": image_size, "n": n,
                "references": (references or [])[:16]}
        with self._submit_lock:
            if project_id:
                self.set_active_project(project_id)
            r = self.post("/image/generate", body)
        if not r.get("job_id"):
            raise LoadoutError(f"unexpected /image/generate reply: {r}")
        return str(r["job_id"])

    def poll_image(self, job_id: str, *, timeout_s: float = 900, interval_s: float = POLL_INTERVAL_S) -> JobResult:
        t0 = time.time()
        while True:
            r = self.post("/image/generate/status", {"job_id": job_id})
            st = str(r.get("status", ""))
            if st == "Success":
                return JobResult(ok=True, status=st, descriptor={"job_id": job_id}, files=r.get("images") or [], elapsed_s=time.time() - t0, raw=r)
            if st == "Fail" or TERMINAL_FAIL_RE.match(st):
                return JobResult(ok=False, status=st, descriptor={"job_id": job_id}, error=str(r.get("error", st)), elapsed_s=time.time() - t0, raw=r)
            if time.time() - t0 > timeout_s:
                return JobResult(ok=False, status="timeout", descriptor={"job_id": job_id}, error=f"no result after {timeout_s}s", raw=r)
            self._sleep(interval_s)

    # ------------------------------------------------------------------ higgsfield (credits)
    def higgsfield_quote(self, kind: str, model: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        return self.post("/higgsfield/cost", {"kind": kind, "model": model, "params": params or {}})

    def submit_higgsfield(self, *, kind: str, model: str, prompt: str, prompt_master_id: str | None,
                          refs: list[dict[str, str]] | None = None, params: dict[str, Any] | None = None,
                          engine_label: str = "", est_usd: float | None = None, est_credits: float | None = None,
                          project_id: str | None = None) -> str:
        """Higgsfield spends plan credits: the BOSSK core must have approved this (tier 3) before it is called."""
        self._require_prompt_master(prompt_master_id)
        body = {"kind": kind, "model": model, "prompt": prompt, "refs": refs or [], "params": params or {},
                "engine_label": engine_label or f"bossk:{model}", "est_usd": est_usd, "est_credits": est_credits}
        with self._submit_lock:
            if project_id:
                self.set_active_project(project_id)
            r = self.post("/higgsfield/submit", body)
        if not r.get("job_id"):
            raise LoadoutError(f"unexpected /higgsfield/submit reply: {r}")
        return str(r["job_id"])

    def poll_higgsfield(self, job_id: str, *, timeout_s: float = 3600, interval_s: float = 10.0) -> JobResult:
        t0 = time.time()
        while True:
            r = self.post("/higgsfield/status", {"job_id": job_id})
            st = str(r.get("status", ""))
            if st == "Success":
                return JobResult(ok=True, status=st, descriptor={"job_id": job_id}, files=r.get("files") or [], elapsed_s=time.time() - t0, raw=r)
            if st in ("Fail", "Cancelled") or TERMINAL_FAIL_RE.match(st):
                return JobResult(ok=False, status=st, descriptor={"job_id": job_id}, error=str(r.get("error", st)), elapsed_s=time.time() - t0, raw=r)
            if time.time() - t0 > timeout_s:
                return JobResult(ok=False, status="timeout", descriptor={"job_id": job_id}, error=f"no result after {timeout_s}s", raw=r)
            self._sleep(interval_s)

    # ------------------------------------------------------------------ prep tools (no prompt, no prompt-master needed)
    def previz_upload(self, path: str | Path) -> str:
        p = Path(path)
        r = self.call("POST", "/previz/upload?name=" + urllib.request.quote(p.name), raw=p.read_bytes(), content_type="application/octet-stream")
        if not r.get("staged"):
            raise LoadoutError(f"unexpected /previz/upload reply: {r}")
        return str(r["staged"])

    def roto(self, *, staged_source: str, points_positive: list[list[float]], points_negative: list[list[float]] | None = None,
             grow: int = 0, feather: int = 0, max_side: int | None = None, matte_engine: str = "matanyone",
             project_id: str | None = None, timeout_s: float = 3600) -> JobResult:
        """AI Roto: SAM 2 + MatAnyone through LOADOUT's ComfyUI backend. Returns the matte and subject files.
        This is hornets stage 1 (head mask through the shot)."""
        inputs: dict[str, Any] = {"staged_source": staged_source, "points_positive": points_positive,
                                  "points_negative": points_negative or [], "grow": grow, "feather": feather, "matte_engine": matte_engine}
        if max_side:
            inputs["max_side"] = max_side
        desc = self.submit_video({"provider": "roto", "inputs": inputs}, prompt_master_id=None, project_id=project_id)
        return self.poll_video(desc, timeout_s=timeout_s)

    def mask_apply(self, source: str, regions: list[dict[str, float]], mode: str = "blackout", blur_radius: int = 20) -> dict[str, Any]:
        return self.post("/mask/apply", {"source": source, "regions": regions, "mode": mode, "blur_radius": blur_radius}, timeout=900)

    def splice_lastframe(self, source: str) -> dict[str, Any]:
        return self.post("/splice/lastframe", {"source": source}, timeout=900)

    def splice_stitch(self, clips: list[str], blend_ms: int = 250, overlap_ms: int = 0) -> dict[str, Any]:
        return self.post("/splice/stitch", {"clips": clips, "blend_ms": blend_ms, "overlap_ms": overlap_ms}, timeout=900)

    def retime(self, *, source: str, mode: str, amount: float, method: str = "motion", model: str = "") -> JobResult:
        r = self.post("/retime/submit", {"source": source, "mode": mode, "amount": amount, "method": method, "model": model})
        job_id = str(r.get("job_id", ""))
        t0 = time.time()
        while True:
            s = self.post("/retime/status", {"job_id": job_id})
            st = str(s.get("status", ""))
            if st == "Success":
                return JobResult(ok=True, status=st, descriptor={"job_id": job_id}, local_url=s.get("local_url", ""), raw=s, elapsed_s=time.time() - t0)
            if st == "Fail" or TERMINAL_FAIL_RE.match(st):
                return JobResult(ok=False, status=st, descriptor={"job_id": job_id}, error=str(s.get("error", st)), raw=s)
            if time.time() - t0 > 3600:
                return JobResult(ok=False, status="timeout", descriptor={"job_id": job_id}, error="no result after 3600s", raw=s)
            self._sleep(POLL_INTERVAL_S)

    # ------------------------------------------------------------------ raw ComfyUI graph through LOADOUT's proxy
    def comfy_prompt(self, graph: dict[str, Any], client_id: str = "bossk") -> str:
        """POST /api/prompt: LOADOUT proxies an arbitrary ComfyUI graph to the active GPU backend.
        Used for graphs LOADOUT has no builder for (Wan VACE). Outputs land in ComfyUI's output dir, which
        is LOADOUT's gallery when OUTPUT_DIR is the local ComfyUI output. Returns prompt_id."""
        r = self.post("/api/prompt", {"prompt": graph, "client_id": client_id})
        pid = r.get("prompt_id")
        if not pid:
            raise LoadoutError(f"unexpected /api/prompt reply: {r}")
        return str(pid)

    def comfy_history(self, prompt_id: str) -> dict[str, Any]:
        return self.get(f"/api/history/{prompt_id}")

    def wait_comfy(self, prompt_id: str, *, timeout_s: float = 3600, interval_s: float = POLL_INTERVAL_S) -> JobResult:
        t0 = time.time()
        while True:
            h = self.comfy_history(prompt_id)
            rec = h.get(prompt_id) if isinstance(h, dict) else None
            if rec:
                status = rec.get("status") or {}
                if status.get("status_str") == "error" or status.get("completed") is False and status.get("status_str"):
                    return JobResult(ok=False, status="error", descriptor={"prompt_id": prompt_id}, error=json.dumps(status)[:400], raw=rec)
                outputs = rec.get("outputs") or {}
                files = [f for node in outputs.values() for key in ("images", "gifs", "videos") for f in node.get(key, [])]
                if files or status.get("completed"):
                    return JobResult(ok=True, status="Success", descriptor={"prompt_id": prompt_id}, files=files, elapsed_s=time.time() - t0, raw=rec)
            if time.time() - t0 > timeout_s:
                return JobResult(ok=False, status="timeout", descriptor={"prompt_id": prompt_id}, error=f"no result after {timeout_s}s")
            self._sleep(interval_s)


VIDEO_PROVIDERS_NO_PROMPT = {"previz", "roto", "ltxrefine"}   # prep tools: no creative prompt, prompt-master not required


# ---------------------------------------------------------------------- body helpers (mirror app.js VIDEO_ENGINES)
def seedance_body(prompt: str, *, tier: str = "pro", image_url: str | None = None, end_image_url: str | None = None,
                  image_urls: list[str] | None = None, video_urls: list[str] | None = None, resolution: str = "1080p",
                  duration: int = 5, aspect_ratio: str = "16:9", generate_audio: bool = False) -> dict[str, Any]:
    """tier: pro | fast | mini | 2.5. 2.5 routes to WaveSpeed / fal; 2.0 to BytePlus / Replicate / fal."""
    inputs: dict[str, Any] = {"resolution": resolution, "duration": duration, "aspect_ratio": aspect_ratio, "generate_audio": generate_audio}
    if image_url:
        inputs["image_url"] = image_url
    if end_image_url:
        inputs["end_image_url"] = end_image_url
    if image_urls:
        inputs["image_urls"] = image_urls
    if video_urls:
        inputs["video_urls"] = video_urls
    return {"provider": "byteplus", "tier": tier, "prompt": prompt, "inputs": inputs}


def kling_body(prompt: str, *, kind: str = "video", model_path: str = "kling-3.0", start_image_url: str | None = None,
               end_image_url: str | None = None, duration: int = 5, resolution: str = "1080p", aspect_ratio: str = "16:9",
               generate_audio: bool = False) -> dict[str, Any]:
    inputs: dict[str, Any] = {"duration": duration, "resolution": resolution, "aspect_ratio": aspect_ratio, "generate_audio": generate_audio}
    if start_image_url:
        inputs["start_image_url"] = start_image_url
    if end_image_url:
        inputs["end_image_url"] = end_image_url
    return {"provider": "kling", "kind": kind, "model_path": model_path, "prompt": prompt, "inputs": inputs}


def h3_local_body(prompt: str, *, first_frame: str | None = None, image_urls: list[str] | None = None, duration: int = 5,
                  resolution: str = "720p", aspect_ratio: str = "16:9", steps: int | None = None, seed: int | None = None) -> dict[str, Any]:
    """MiniMax H3 on the GPU backend (CLOUD_ONLY_ENGINES pins it to the rented GPU unless LOADOUT's config changes)."""
    inputs: dict[str, Any] = {"duration": duration, "resolution": resolution, "aspect_ratio": aspect_ratio}
    if first_frame:
        inputs["first_frame"] = first_frame
    if image_urls:
        inputs["image_urls"] = image_urls
    if steps:
        inputs["steps"] = steps
    if seed is not None:
        inputs["seed"] = seed
    return {"provider": "h3", "prompt": prompt, "inputs": inputs}


def ltx25_body(prompt: str, *, duration: int = 5, resolution: str = "1080p", aspect_ratio: str = "16:9", fps: int = 24,
               seed: int | None = None, negative_prompt: str = "") -> dict[str, Any]:
    inputs: dict[str, Any] = {"duration": duration, "resolution": resolution, "aspect_ratio": aspect_ratio, "fps": fps, "negative_prompt": negative_prompt}
    if seed is not None:
        inputs["seed"] = seed
    return {"provider": "ltx25", "prompt": prompt, "inputs": inputs}


def fal_body(prompt: str, model_id: str, inputs: dict[str, Any]) -> dict[str, Any]:
    return {"provider": "fal", "model_id": model_id, "prompt": prompt, "inputs": inputs}
