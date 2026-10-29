# BOSSK · Background Operations Supervisor & Session Keeper

Plan: https://claude.ai/artifact/SKPzbnZnciaxt71rqJL49t

This folder is phases 1 to 3 of that plan: the core on the TrueNAS server, the per-machine
agents that intercept Claude Code permission prompts, and the nightly NAS conversion checker.
Voice (phase 4) and the apps come next and talk to the same core API.

## Layout

| Path | What |
| --- | --- |
| `core/` | The brain's plumbing: rulebook engine, action ledger, ask queue, presence, reviewer agent, secrets broker, HTTP API |
| `rulebook/rulebook.yml` | The three tiers. Edit this, then `POST /rulebook/replay` to see what would change before `POST /rulebook/reload` |
| `agents/hooks/bossk_hook.py` | The Claude Code hook. One file, no dependencies. `install` wires it into `~/.claude/settings.json` |
| `agents/idle_reporter.py` | Keyboard/mouse idle, live sessions (`claude agents --json`), GPU and disk, every 30 s |
| `nas/convert_checker.py` | Nightly verify, holding folder, ZFS snapshot, purge as a tier 3 ask |
| `deploy/` | Docker compose for TrueNAS (core + ntfy + checker), launchd plist for the Macs, scheduled task for the PC |
| `tests/` | `python3 -m unittest discover -s tests -t .` from this folder |

## How a permission prompt flows

1. A Claude Code session on any machine hits a permission prompt. The `PermissionRequest` hook runs `bossk_hook.py`.
2. The hook POSTs the tool call, cwd and the last few transcript turns to the core's `/decide` and waits.
3. The core tiers it from the rulebook in under a millisecond. Tier 1 returns allow. Tier 0 returns deny.
4. Tier 2 goes to the reviewer agent (Claude, Sonnet by default). Allow or deny come straight back. Anything unclear escalates.
5. Tier 3 becomes an ask. If a machine with a voice daemon has seen input in the last 10 minutes, the ask is queued for it and the daemon long-polls `/asks?channel=voice:<machine>`. If not, or after 90 s with no answer, it goes to the iPhone through ntfy with Yes / No buttons that POST back to the core.
6. The hook returns allow or deny to Claude Code. If nobody answers within 9 minutes, the hook returns nothing and Claude Code shows its own prompt. The ask stays open.
7. Money and outbound messages need "confirm" after "yes".
8. Every step is in the ledger (`GET /ledger`). A crashed hook or unreachable core denies, never allows.

## Install

**Core (TrueNAS):** see the comments at the top of `deploy/core/docker-compose.yml`.

**Each machine:**
```
python3 agents/hooks/bossk_hook.py install --core http://truenas:8750 --token <BOSSK_TOKEN> --machine mini --can-speak
```
Then the idle reporter: `deploy/mac/com.bossk.agent.plist` on the Macs, `deploy/windows/install-agent.ps1` on the PC.
Use `--can-speak` only on machines that will run the voice daemon (Mac mini, laptop).

**NAS checker:** copy `nas/convert_checker.example.yml` to `/mnt/tank/bossk/data/convert_checker.yml`, set the three paths,
run a week with `--dry-run` in the compose command, then drop the flag.

## Core API

| Route | Who calls it |
| --- | --- |
| `POST /decide` | the hook; blocks up to `max_wait_seconds` |
| `POST /log`, `POST /events` | the hook, non-blocking |
| `POST /idle` | idle reporter |
| `GET /asks?channel=voice:mini&wait=25` | voice daemon, long-poll |
| `POST /asks/<id>/answer {text, by}` | voice daemon, app |
| `POST /a/<id>/<token>/<yes,no,confirm>` | ntfy buttons (one-time token, no bearer) |
| `POST /pause`, `POST /unpause` | "BOSSK, stop everything" |
| `GET /health`, `GET /sessions`, `GET /ledger` | app, status |
| `POST /notify` | jobs sending a summary through the core |
| `POST /rulebook/replay`, `POST /rulebook/reload` | rule changes |

All routes take `Authorization: Bearer <BOSSK_TOKEN>` except the ntfy button callbacks.

## Loadout bridge

`loadout/client.py` drives LOADOUT over its own HTTP API, as documented in LOADOUT-BRIDGE.md (0.2.28).
No new surface in LOADOUT is required for a first pass. The client polls every job to completion because
LOADOUT only downloads, files and records a video when something polls `/video/status`. Every creative
generation requires a `prompt_master_id`; prep tools (roto, previz) do not. Submits are serialised and pin the
active project first, since the active project is global server state.

Reaching LOADOUT from the NAS over Tailscale: start the server with `PORT=5500` (binds all interfaces) and add
the PC's Tailscale name to `config.local.json` `allowed_hosts`. Set `LOADOUT_URL=http://<pc>:5500` for the core.

Hornets through LOADOUT: stage 1 is LOADOUT's AI Roto (SAM 2 + MatAnyone) via `client.roto()`; stage 2 is a
Wan VACE graph submitted with `client.comfy_prompt()` through LOADOUT's `/api/prompt` proxy to the 4090;
stage 3 is the ffmpeg composite in `pipelines/hornets/` (next).
