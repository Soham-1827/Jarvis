# Handover

Read this first in every new session, then `PLAN.md` and `TODO.md`. Update it at the end of every
session (`/handover`).

## Current state

- **Phase 0:** code done (0.1, 0.2, 0.4, 0.6). Gate waits for the M700 (0.3, 0.5, 0.7).
- **Phase 1:** all code done (1.1 to 1.8), verified live against real Claude locally. Gate waits for the
  M700 + iPhone over Tailscale.
- **Branches:** `phase-0` → `phase-1` (phase-1 contains everything). Both pushed. Merge to `main` via PR
  after the gates pass.
- **Next:** deploy to the M700 and run `/gate` for Phase 0 and Phase 1. Then Phase 2 (coding workers,
  approvals, ntfy). See the "Follow-ups from reviews" list at the bottom of `TODO.md`.
- **Tests:** 202 passing, 97% coverage, ruff clean.
- **Last updated:** 2026-11-05

## What exists

| Area | Where | Notes |
|---|---|---|
| Config | `src/jarvis/shared/config.py` | `JARVIS_*` env; bind hosts loopback/Tailscale only |
| Protocol | `src/jarvis/shared/protocol.py` | pydantic WS messages: auth, chat, ready, thinking, reply, error |
| Store | `src/jarvis/brain/store/` | SQLite + migrations, locked `Database`, repos, devices (hashed tokens), append-only audit |
| Memory | `src/jarvis/brain/memory/` | whitelisted markdown files, FTS5 search, own git repo (isolated git env), caps |
| MCP server | `src/jarvis/brain/mcp_server/` | stdio, `memory_read/write/append/search/list`, audited, rate-limited, no forget |
| Runner | `src/jarvis/brain/core/claude_runner.py` | hardened `claude -p` stream-json runner (env allowlist, process-group kill) |
| Orchestrator | `src/jarvis/brain/core/orchestrator.py`, `prompt.py` | turn loop, session resume, memory fenced as data, onboarding |
| API | `src/jarvis/brain/api/` | `/ws` with token auth, origin + Tailscale checks, per-device limits |
| App | `src/jarvis/brain/app.py`, `services.py` | FastAPI, `/health`, serves PWA with strict CSP |
| PWA | `web/` | pairing + chat + usage gauges; screenshots in `docs/screenshots/` |
| CLIs | `jarvis-brain`, `jarvis-admin` (devices), `jarvis-chat` (terminal) | |
| Deploy | `deploy/server/` | `setup.sh`, hardened systemd unit, `env.example` |

Run checks: `uv sync && uv run ruff check . && uv run pytest --cov=jarvis`

## Decisions (with reasons)

- Home server (Lenovo M700 Tiny) runs the brain; laptop gets a thin node later. Claude Pro only.
- Orchestrator = `claude -p` on the subscription login, `--setting-sources=user`.
- **Phase 1 orchestrator has no built-in tools** (`--tools=` + `--disallowedTools`), only Jarvis memory
  tools. Reason: memory read + WebFetch would let an injected prompt exfiltrate memory. Web research
  (Phase 4) must run in a separate session without memory access.
- No `memory_forget` tool: deleting data is never automatic. Git history keeps old versions (purge later).
- Child `claude` processes get an allowlisted env (no brain secrets); prompt on stdin, system prompt in a
  0600 file, process group killed on timeout/cancel.
- Brain binds 127.0.0.1; `tailscale serve` provides HTTPS. Device token is the real gate;
  `JARVIS_ALLOWED_TAILSCALE_USERS` is optional defence in depth (a local process could spoof the header).
- Approvals expire (1h default) and are decided exactly once (guarded UPDATE). Audit log is append-only.
- `mcp` SDK is v2 (`MCPServer`, not FastMCP).
- Pro usage: every turn reports 5h/7d utilization; shown as gauges in the PWA. Live testing this session
  used some of the real allowance (5h window was ~44% at one point).

## Deploy runbook (when the M700 arrives)

1. Install Ubuntu Server 24.04, wired Ethernet, BIOS "power on after AC loss".
2. `git clone https://github.com/Soham-1827/Jarvis.git && cd Jarvis && git checkout phase-1`
3. `sudo REPO_REF=phase-1 ALLOW_LAN_SSH=1 ./deploy/server/setup.sh`, then `sudo tailscale up`, re-run
   the script so `tailscale serve` is configured.
4. Log in Claude: `sudo -iu jarvis claude` then `/login` (Pro account).
5. Pair the phone:
   `sudo -iu jarvis bash -c 'cd /opt/jarvis && JARVIS_DATA_DIR=/var/lib/jarvis .venv/bin/jarvis-admin add iphone'`
6. On the iPhone (Tailscale on): open `https://<server>.ts.net`, paste the token, Share → Add to Home Screen.
7. Optional: add `JARVIS_ALLOWED_TAILSCALE_USERS=["<you>@github"]` to `/etc/jarvis/env`, restart service.

## Blockers / needs Soham

- M700 delivery, then the runbook above.
- Decide whether `JARVIS_ORCHESTRATOR_MODEL` should default to Haiku (cheaper on Pro limits) or the CLI
  default model (smarter). Currently unset = CLI default.

## Environment notes

- Dev machine: Windows laptop, repo at `D:\jarvis`, worked on from WSL (`/mnt/d/jarvis`).
- WSL has `claude` 2.1.x, `uv`, `node`, `git`. No sudo-installed Chromium libs: for screenshots, drive
  Windows Edge via a temp Windows venv with Playwright (see session log).
- An ECC plugin hook blocks commands that contain both `git commit` and a bare `-n` flag (false
  positive for `--no-verify`); keep `git commit` in its own command.

## Session log

| Date | Phase/task | Outcome |
|---|---|---|
| 2026-11-05 | Planning | Read PDF, chose server + node topology, wrote PLAN.md, HANDOVER.md, dev workflow |
| 2026-11-05 | Planning | Switched to Claude-only and a home server (M700 Tiny) instead of a VPS |
| 2026-11-05 | 0.1, 0.2, 0.4, 0.6 | Skeleton, config, /health, CI, setup.sh, systemd unit. Reviews fixed |
| 2026-11-05 | 1.1 to 1.8 | Store, memory, MCP server, runner, orchestrator, WS API, PWA, terminal client, onboarding. Each reviewed (python/security reviewers) and findings fixed. Live-verified with Haiku: memory save/recall, file-read refusal, PWA in Edge (light/dark), jarvis-chat. 202 tests, 97% coverage. Note: the 1.8 commit had one failing test (fixed in the next commit) |
