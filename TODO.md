# Coding To-Do

The working checklist for code. Each line is one small, testable step; the number links it to a
task in `PLAN.md`. When every step of a task is done, tick the task in `PLAN.md` too.
Hardware/account steps (server install, logins) live in `HANDOVER.md` under "Blockers".

Legend: `[ ]` todo · `[~]` in progress · `[x]` done · `[blocked]` needs the server or Soham

## Phase 0: skeleton and infrastructure code

- [x] 0.1a `pyproject.toml`: uv project, Python 3.12, extras `brain`, `node`, `dev`; ruff + pytest + coverage config
- [x] 0.1b Package layout `src/jarvis/{shared,brain,node}` with `__init__` and version
- [x] 0.1c `jarvis.shared.config`: typed settings from env (`JARVIS_*`), validated at startup
- [x] 0.2  GitHub Actions CI: `uv sync`, ruff check, ruff format check, pytest with coverage
- [x] 0.6a Brain app factory + `GET /health` (status, version, uptime)
- [x] 0.6b `jarvis-brain` entry point: uvicorn bound to localhost + configured Tailscale IP only
- [x] 0.6c `deploy/server/jarvis-brain.service` systemd unit (hardened)
- [x] 0.4  `deploy/server/setup.sh`: idempotent packages, users, dirs, ufw, uv, node, Claude Code, tailscale serve
- [blocked] 0.3 / 0.5 / 0.7 run `setup.sh` on the M700, log in Claude, verify gate (waiting for delivery)

## Phase 1: brain core + text chat

- [ ] 1.1a SQLite schema + migration runner (`schema_version`)
- [ ] 1.1b Repositories: conversations, messages, jobs, approvals, audit (immutable dataclasses in/out)
- [ ] 1.2a Memory store: markdown files (profile, preferences, areas, people, log) with safe paths
- [ ] 1.2b FTS5 index: rebuild + incremental update + search
- [ ] 1.2c Memory git commits on write/forget
- [ ] 1.3  Jarvis MCP server (stdio) with `memory_read`, `memory_write`, `memory_search`, `memory_forget`
- [ ] 1.4a Claude runner: build `claude -p` args, spawn, parse stream-json events
- [ ] 1.4b Prompt builder: persona + profile + top memory hits
- [ ] 1.4c Orchestrator: turn handling, session resume, transcript saved, usage recorded
- [ ] 1.5  WebSocket chat API with device-token auth
- [ ] 1.6  PWA v0: chat screen, manifest, service worker, installable on iPhone
- [ ] 1.7  `jarvis chat` terminal client
- [ ] 1.8  Onboarding chat that seeds `profile.md`
