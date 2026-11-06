# Handover

Read this first in every new session, then `PLAN.md`. Update it at the end of every session (`/handover`).

## Current state

- **Phase:** 0 code done; gate waits for the M700. Phase 1 started on branch `phase-1` (from `phase-0`).
- **Branches:** `phase-0` (skeleton, CI, deploy) pushed, merge to `main` after the gate. `phase-1` in progress.
- **Next task:** see `TODO.md` (first unchecked line). Currently 1.1a SQLite schema.
- **Last updated:** 2026-11-05

## What exists

- `PLAN.md`: architecture, decisions, roadmap with task checklists
- `docs/Jarvis-Build-Plan.pdf`: original vision document
- `CLAUDE.md` + `.claude/commands/`: the development workflow (`/next`, `/gate`, `/handover`)
- `TODO.md`: granular coding checklist, one line per small testable step
- `src/jarvis/shared/config.py`: `JARVIS_*` settings; bind hosts limited to loopback/Tailscale
- `src/jarvis/brain/app.py` + `__main__.py`: FastAPI app with `/health`, `jarvis-brain` entry point
- `deploy/server/`: `setup.sh` (idempotent Ubuntu setup), hardened `jarvis-brain.service`, `env.example`
- `.github/workflows/ci.yml`: ruff + pytest with 80% coverage floor
- Run checks: `uv sync && uv run ruff check . && uv run pytest --cov=jarvis`

## Decisions made this session (2026-11-05)

- Brain runs on an always-on home server (Lenovo ThinkCentre M700 Tiny, i3-6100T, 8 GB, 128 GB SSD,
  being bought); the laptop runs a thin node for voice, scenes and app control. Tailscale links server,
  laptop and iPhone. A VPS stays a drop-in alternative.
- **Claude only** (Pro plan): no Codex, Gemini or local models. A usage governor (Haiku for quick work,
  one coding job at a time, pause on limits) replaces the multi-provider router.
- Orchestrator uses `claude -p` on the subscription login, not the Agent SDK with an API key.
- Laptop node starts from Task Scheduler at logon, not as a Windows service.
- PWA is plain HTML/JS served by FastAPI for now. Ollama and embeddings deferred.
- Personal memory lives in a separate private repo (`jarvis-memory`), never in this one.
- Brain binds 127.0.0.1 only; `tailscale serve` provides HTTPS on the tailnet. ufw allows only SSH on
  `tailscale0`. Every non-health endpoint must authenticate (device token + Tailscale identity) from 1.5 on.
- Tailscale installed from its signed apt repo. uv (pinned) and Claude Code use vendor installers over
  HTTPS as the unprivileged `jarvis` user: accepted risk, documented in `setup.sh`.
- `setup.sh` deploys `REPO_REF` (branch, tag or commit) as a detached checkout.
- Phase 2 workers get their own systemd unit as `jarvis-worker` (the brain unit has NoNewPrivileges, so no
  sudo). Treat everything under `/srv/jarvis` as untrusted worker output.

## Blockers / needs Soham

- M700 ordered. When it arrives: install Ubuntu Server 24.04, enable "power on after AC loss" in BIOS,
  `sudo tailscale up` after setup installs it, then merge `phase-0` and run
  `sudo REPO_REF=main ./deploy/server/setup.sh` (add `ALLOW_LAN_SSH=1` to keep LAN SSH).
- Then log in Claude Code as the jarvis user: `sudo -iu jarvis claude` and `/login` (task 0.5).
- iPhone: Tailscale + ntfy installed (done).

## Environment notes

- Dev machine: Windows laptop, repo at `D:\jarvis`, worked on from WSL (`/mnt/d/jarvis`).
- WSL has `claude`, `uv`, `node`, `git`. Tailscale not installed locally yet.
- Windows has Python 3.12 at `C:\Program Files\Python312` (for the laptop node).

## Session log

| Date | Phase/task | Outcome |
|---|---|---|
| 2026-11-05 | Planning | Read PDF, chose server + node topology, wrote PLAN.md, HANDOVER.md, dev workflow |
| 2026-11-05 | Planning | Switched to Claude-only and a home server (M700 Tiny) instead of a VPS |
| 2026-11-05 | 0.1, 0.2, 0.4, 0.6 | Skeleton, config, /health, CI, setup.sh, systemd unit. python-reviewer + security-reviewer findings fixed. 25 tests, 97% coverage |
