# Jarvis Plan

Source of truth for what we are building and in what order. The original vision lives in
`docs/Jarvis-Build-Plan.pdf`; this file supersedes it where they differ (see "Changes from the PDF").

Owner: Soham · Started: 2026-11-05

---

## 1. Vision

A personal agentic harness that is always on, knows Soham's context, and gets real work done across
apps, the web and code. Reachable by voice on the laptop and by text or voice from the iPhone.

- **One front door:** laptop voice, iPhone PWA and a terminal client share one brain, one memory, one job list.
- **Does work, not just chat:** dispatches coding jobs to Claude Code, Codex and Gemini CLI, researches the web, drives the laptop.
- **Knows you:** persistent profile and memory every agent reads.
- **Safe by default:** risky actions wait for a one-tap approval on the phone.
- **Uses existing subscriptions:** Claude Pro, ChatGPT Pro, Gemini Pro via their official headless CLIs.
- **Built to grow:** new tools drop in as MCP servers or plugins without touching the core.

## 2. Decisions

| Topic | Decision |
|---|---|
| Topology | **Brain on an always-on VPS, thin node on the laptop** (see section 3) |
| VPS | Hetzner-class Linux box, ~4 GB RAM, Ubuntu 24.04, ~$5/mo |
| Private network | Tailscale on VPS, laptop and iPhone. No public inbound ports at all (SSH over Tailscale too) |
| Laptop | Windows, node runs in native Windows Python 3.12, started by Task Scheduler at logon |
| Phone | iPhone, PWA on Home Screen served over `tailscale serve` (HTTPS), push via ntfy iOS app |
| Main uses | Coding, school, job search, daily life |
| First apps | Google suite, GitHub, VS Code/terminal, messaging, media |
| Autonomy | Auto for reading and sandboxed coding; phone approval for sending, pushing, installing |
| Personality | Classic JARVIS, polite British butler |
| Voice wake | "Hey Jarvis" wake word plus a hotkey, double clap for scenes |
| Orchestrator LLM | `claude -p` (Claude Code headless) on the subscription login, not the Agent SDK with an API key |
| Data access | All three providers may see private repos, school and email |
| Language/stack | Python 3.12, uv, FastAPI + WebSockets, SQLite, pytest, ruff |

## 3. Architecture

```
            iPhone PWA                 Laptop node (Windows)            Schedules / watchers
     chat, push-to-talk, approvals   wake word, clap, mic, speakers    Gmail, Calendar, GitHub,
                 |                   scenes, window/app control         Slack, Canvas, job boards
                 |  HTTPS (tailnet)        |  outbound WebSocket (tailnet)        |
                 v                         v                                      v
   +------------------------------- Jarvis brain (VPS, systemd) ---------------------------------+
   |  API (FastAPI, WS)  ->  Orchestrator (claude -p + Jarvis MCP tools)                          |
   |                          |-- Memory (markdown git repo + SQLite FTS)                          |
   |                          |-- Router (Claude / Codex / Gemini)                                 |
   |                          |-- Jobs, approvals, audit log (SQLite)                              |
   |                          |-- Node registry (which laptop capabilities are online)            |
   +--------------------------------------------------------------------------------------------+
                 |                         |                                      |
         Coding workers             Apps and data                        Web and screen
   Claude Code, Codex, Gemini   MCP servers (GitHub, Gmail,        search API, Playwright
   in git worktrees on the VPS  Calendar, Slack...)                (VPS); desktop control via node
```

Rules:
- Interfaces never talk to workers directly; everything passes through the orchestrator and the approval queue.
- The **brain** owns all state. The **node** is stateless: it advertises capabilities
  (`tts.play`, `scene.run`, `app.open`, `window.arrange`, `clipboard`, ...) and executes commands it is sent.
- If the laptop is off, the brain still works; laptop-only tools return "the laptop is offline, sir"
  and can be queued to run when it reconnects.
- The node connects **outbound** to the brain over Tailscale, so the laptop needs no listening port.

### 3.1 Orchestrator

Each user turn runs `claude -p --output-format stream-json --resume <session>` with:
- `--mcp-config` pointing at the **Jarvis MCP server** (hosted by the brain) exposing the core tools below.
- a system prompt built from persona + profile + top memory hits.
- `--allowedTools` limited to Jarvis tools and read-only built-ins.

The orchestrator plans and delegates; it does not edit code itself.

| Tool | What it does |
|---|---|
| `agent_start(task, repo?, worker?)` | Start a Claude Code / Codex / Gemini job in a git worktree, return a job id at once |
| `agent_status`, `agent_result` | List jobs, fetch a job's output |
| `agent_send(job, message)` | Answer a worker's question or add a follow-up |
| `agent_stop(job)` | Cancel a job |
| `memory_read`, `memory_write` | Personal context store |
| `approve_request(kind, summary, payload)` | Push an approval card to the phone and wait |
| `schedule(task, when)` | One-off or recurring jobs |
| `node_call(capability, args)` | Run something on the laptop (scenes, TTS, app control) |
| `web_search`, `web_fetch` | Research (Phase 4) |

### 3.2 Coding workers

- Repos cloned under `/srv/jarvis/repos`, each job gets a worktree under `/srv/jarvis/work/<job-id>`.
- Spawned with `asyncio` subprocesses: `claude -p --permission-mode acceptEdits`,
  `codex exec --sandbox workspace-write`, `gemini -p`. Never `--dangerously-skip-permissions`.
- Claude Code hooks in the worker config report back to the brain:
  - `PreToolUse` on Bash: commands outside the worktree, installs and `git push` call the brain's
    approval endpoint and block until approved/denied.
  - `Notification` and `Stop`: post status to the supervisor instead of scraping stdout.
- Workers run as a separate Unix user (`jarvis-worker`) with access only to `repos/` and `work/`.

### 3.3 Router (v1 rules)

1. Conversation and planning: Claude (`claude -p`). Quick classification may use `--model haiku`.
2. Anything that edits code: Claude Code first, Codex when Claude is rate limited or for a second opinion.
3. "Read all of this" over ~200k tokens: Gemini CLI.
4. Important changes: one provider writes, a different one reviews (Phase 5).

### 3.4 Memory

- Plain Markdown in a **separate private git repo** (`jarvis-memory`) on the VPS, never in this code repo.
  - `profile.md`, `preferences.md`, `areas/<project>.md`, `people/<name>.md`, `log/YYYY-MM-DD.md`.
- Index: SQLite FTS5 over those files (embeddings deferred until FTS proves insufficient).
- Fill: after each conversation a background pass proposes facts; only things Soham stated are saved.
  "Forget X" deletes and commits.

### 3.5 Interfaces

**iPhone PWA** (plain HTML/JS served by FastAPI, no Next.js until it is needed):
chat, push-to-talk (records audio, brain transcribes with faster-whisper `base.en` int8),
job list, approval cards, audit log. HTTPS via `tailscale serve` (required for mic access and PWA install).
Auth: Tailscale identity header + a device token.

**Push:** ntfy (hosted ntfy.sh with an unguessable topic and access token). Notifications carry no
sensitive content, only "Approval needed" / "Job done" and a link into the PWA.

**Laptop node:** openWakeWord ("hey jarvis") + hotkey, clap detector, faster-whisper locally, edge-tts
British voice (Piper offline fallback), barge-in, pywinauto + PowerShell for apps and windows.
Runs from **Task Scheduler at logon** (not an NSSM service: services in session 0 cannot reach the
desktop or audio). Power: no sleep on AC, lid close does nothing.

**Claude Code Remote Control:** coding jobs started by Jarvis can be opened and steered from the Claude
iOS app (verify plan availability in Phase 0).

### 3.6 Scenes

`node/scenes.yaml`, idempotent, fired by any trigger (clap, voice, hotkey, phone button, calendar,
Windows unlock) through `node_call("scene.run", name)`.

| Scene | Triggers | What happens |
|---|---|---|
| Home | Double clap, "I'm home", unlock after 6pm | Spotify, Chrome (Claude + Gmail), VS Code on last repo, spoken brief |
| Deep work | "Focus mode", phone button | DND, lo-fi, close Discord, open today's repo, timer |
| Class | Calendar event starts | Canvas, lecture notes, mute notifications |
| Job hunt | "Job mode" | Tracker, recruiter inbox, today's applications |
| Shutdown | "Goodnight" | Summarize day to memory, queue overnight jobs, lock screen |

The spoken brief is generated by the brain (calendar, due dates, job emails, overnight jobs) and only
played by the node.

### 3.7 Watchers (Phase 4)

| Connector | Checks | Pings when |
|---|---|---|
| Gmail | every 10 min | recruiter, professor, interview email; deadline mentioned |
| Google Calendar | next 24h | 15 min before events, conflicts, new invites |
| GitHub | PRs, issues, CI | CI fails, review requested, PR merged |
| Canvas | assignments, announcements | due within 48h, new grade or announcement |
| Slack | DMs, mentions every 5 min | someone needs a reply (draft for one-tap send) |
| Job boards | daily | new-grad SWE/AI role matches |

## 4. Security

The brain can run code from a phone, so it is treated as a remote shell.

| Action class | Policy |
|---|---|
| Read files, search web, read calendar/email | Auto |
| Edit code inside a job's worktree | Auto (`acceptEdits`) |
| Shell outside the worktree, installs, `git push` | Ask on phone |
| Send email/messages, post anything, calendar invites | Ask on phone, show the exact text |
| Purchases, payments, logins, deleting data | Never automatic; Jarvis prepares, Soham does it |

- **Network:** VPS firewall denies all inbound on the public interface; brain binds to `127.0.0.1` and
  the Tailscale IP only. SSH only over Tailscale.
- **Prompt injection:** web pages, emails and repo files are data, never instructions; anything they
  "ask" for goes to the approval queue.
- **Secrets:** VPS: `/etc/jarvis/env` (root-owned, 0600) loaded by systemd; laptop: Windows Credential
  Manager. Never in memory files, prompts, logs or git.
- **Audit:** every tool call and approval recorded in SQLite, viewable from the phone.
- **Data boundaries:** work material stays off Jarvis; passwords and tokens never go to models.

## 5. Repo layout

```
jarvis/
  pyproject.toml          uv project, extras: [brain], [node], [dev]
  src/jarvis/
    shared/               protocol models (pydantic) for brain <-> node <-> PWA
    brain/
      api/                FastAPI app, websocket, auth
      core/               orchestrator, router, supervisor
      mcp_server/         Jarvis MCP tools exposed to claude -p
      workers/            claude_code.py, codex.py, gemini.py
      memory/             markdown store + FTS index
      approvals/          queue, policy, ntfy
      jobs/               SQLite models, scheduler
      watchers/           gmail, calendar, github, canvas, slack, jobs
    node/
      link.py             outbound WS to brain, capability registry
      voice/              wake, clap, stt, tts, audio loop
      scenes/             runner + scenes.yaml
      os_hooks/           pywinauto, powershell helpers
  web/                    PWA static files
  deploy/
    vps/                  setup.sh, systemd units, tailscale serve config
    windows/              task scheduler xml, power settings script
  tests/
  docs/
```

## 6. Roadmap

Weeks assume part-time work alongside classes. Each phase ends with a **gate** you can test by hand.
Work happens on a `phase-N` branch, merged to `main` when the gate passes.

### Phase 0, week 1: infrastructure and skeleton
- [ ] 0.1 Repo skeleton: `pyproject.toml` (uv, extras), `src/jarvis/` packages, ruff + pytest config
- [ ] 0.2 GitHub Actions CI: ruff + pytest on push
- [ ] 0.3 VPS provisioned (Ubuntu 24.04), non-root `jarvis` + `jarvis-worker` users, ufw, Tailscale, SSH over tailnet only
- [ ] 0.4 `deploy/vps/setup.sh` that is re-runnable (packages, uv, node, CLIs, dirs)
- [ ] 0.5 CLIs logged in on VPS with subscriptions: `claude setup-token`, Codex device login, Gemini login; smoke test each headless
- [ ] 0.6 Minimal FastAPI `/health` as a systemd unit, exposed with `tailscale serve`
- [ ] 0.7 Verify open questions: Remote Control on the plan, subscription terms for headless personal use, Pro rate limits observed
- **Gate:** from the iPhone over Tailscale, `https://<vps>.ts.net/health` returns ok, and `claude -p "hello"` works on the VPS.

### Phase 1, weeks 2 to 3: brain core + text chat
- [ ] 1.1 SQLite schema + repository layer: conversations, messages, jobs, approvals, audit
- [ ] 1.2 Memory store: markdown repo, FTS5 index, `memory_read/write`, onboarding chat to seed profile
- [ ] 1.3 Jarvis MCP server with `memory_*` tools
- [ ] 1.4 Orchestrator: `claude -p` stream-json runner, session resume, persona + memory prompt, transcript saved
- [ ] 1.5 WebSocket chat API with device-token auth
- [ ] 1.6 PWA v0: chat screen, installable on iPhone Home Screen
- [ ] 1.7 Terminal client (`jarvis chat`) for testing
- **Gate:** chat with Jarvis from the iPhone PWA and it answers using something from your profile.

### Phase 2, weeks 4 to 5: coding workers + approvals (usable v1)
- [ ] 2.1 Repo manager: clone/fetch, worktree per job, cleanup
- [ ] 2.2 Worker runners for Claude Code and Codex with streamed logs
- [ ] 2.3 `agent_start/status/result/send/stop` MCP tools
- [ ] 2.4 Supervisor: detect waiting-for-input, failure, retry, timeout
- [ ] 2.5 Approval queue + policy engine + `PreToolUse` hook calling it
- [ ] 2.6 ntfy push + PWA approval cards + job list + audit view
- [ ] 2.7 Claude Code Remote Control link from job card (if available)
- **Gate:** from the iPhone, start a coding job on a real repo and approve its `git push`.

### Phase 3, weeks 6 to 7: laptop node, scenes, voice
- [ ] 3.1 Node link: outbound WS, capability registry, reconnect, offline handling in brain
- [ ] 3.2 Scenes runner + `scenes.yaml` + pywinauto/PowerShell actions; Home scene
- [ ] 3.3 Clap detector with calibration
- [ ] 3.4 Spoken brief (brain composes, node speaks with edge-tts, Piper fallback)
- [ ] 3.5 Wake word + hotkey + faster-whisper + VAD + barge-in
- [ ] 3.6 Phone push-to-talk (brain-side transcription), spoken job updates
- [ ] 3.7 Task Scheduler install script + power settings script
- **Gate:** double clap fires Home with a spoken brief, and one full task is done hands-free by voice.

### Phase 4, weeks 8 to 9: watchers, apps, web
- [ ] 4.1 MCP registry (GitHub, Gmail, Calendar, Slack) shared by orchestrator and workers
- [ ] 4.2 Watcher framework + Gmail, Calendar, GitHub, Canvas watchers
- [ ] 4.3 Scheduler: morning brief, nightly repo checks
- [ ] 4.4 Web research tools (search API + fetch, Gemini grounding)
- [ ] 4.5 Gemini CLI worker + router rules
- [ ] 4.6 Playwright browser agent with a dedicated profile
- **Gate:** morning brief and watcher pings arrive on schedule for a full week.

### Phase 5, week 10 on: autonomy and polish
- [ ] 5.1 Router fallbacks on rate limits
- [ ] 5.2 Cross-provider code review
- [ ] 5.3 Computer use for API-less desktop apps (via node)
- [ ] 5.4 Eval suite of real tasks, usage dashboard
- [ ] 5.5 Plugin interface for new skills

## 7. Changes from the PDF

| PDF | Now | Why |
|---|---|---|
| Everything on the laptop | Brain on VPS, thin laptop node | Phone, watchers and coding keep working when the laptop is off |
| NSSM Windows service | Task Scheduler at logon for the node | Session-0 services cannot use the mic or control the desktop |
| Next.js PWA | Plain HTML/JS PWA served by FastAPI | Fewer moving parts; upgrade when the UI earns it |
| Ollama local model | Dropped for v1 | A 4 GB VPS cannot host a useful model; Haiku/Gemini cover quick intents |
| Embeddings + FTS | FTS5 first | YAGNI until search quality is a problem |
| Windows Credential Manager | systemd env file on VPS, Credential Manager on laptop | Secrets live where the process runs |
| Agent SDK open question | `claude -p` headless | Stays on the Pro subscription |
| Phase 1 included scenes | Scenes moved to Phase 3 with the node | Brain must exist before the laptop has something to talk to |

## 8. Open questions

- Is Claude Code Remote Control available on the Pro plan? (check in 0.7)
- How fast do Pro rolling limits run out with one orchestrator + one worker? Decides how early the router fallback is needed.
- Canvas: API token available from the school, or browser agent only?
- Which repos should the VPS clone first?
