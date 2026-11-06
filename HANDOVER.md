# Handover

Read this first in every new session, then `PLAN.md`. Update it at the end of every session (`/handover`).

## Current state

- **Phase:** 0 (infrastructure and skeleton), not started
- **Branch:** `main` (create `phase-0` before writing code)
- **Next task:** 0.1 Repo skeleton
- **Last updated:** 2026-11-05

## What exists

- `PLAN.md`: architecture, decisions, roadmap with task checklists
- `docs/Jarvis-Build-Plan.pdf`: original vision document
- `CLAUDE.md` + `.claude/commands/`: the development workflow (`/next`, `/gate`, `/handover`)
- No code yet

## Decisions made this session (2026-11-05)

- Brain runs on a cheap always-on VPS (Hetzner-class, ~4 GB); the laptop runs a thin node for voice,
  scenes and app control. Tailscale links VPS, laptop and iPhone.
- Orchestrator uses `claude -p` on the subscription login, not the Agent SDK with an API key.
- Laptop node starts from Task Scheduler at logon, not as a Windows service.
- PWA is plain HTML/JS served by FastAPI for now. Ollama and embeddings deferred.
- Personal memory lives in a separate private repo (`jarvis-memory`), never in this one.

## Blockers / needs Soham

- Provision the VPS and share its Tailscale hostname (task 0.3).
- Log in to Claude, Codex and Gemini CLIs on the VPS (task 0.5, needs your accounts).
- Install Tailscale + ntfy on the iPhone.

## Environment notes

- Dev machine: Windows laptop, repo at `D:\jarvis`, worked on from WSL (`/mnt/d/jarvis`).
- WSL has `claude`, `uv`, `node`, `git`. Codex, Gemini CLI, Tailscale not installed locally yet.
- Windows has Python 3.12 at `C:\Program Files\Python312` (for the laptop node).

## Session log

| Date | Phase/task | Outcome |
|---|---|---|
| 2026-11-05 | Planning | Read PDF, chose VPS + node topology, wrote PLAN.md, HANDOVER.md, dev workflow |
