---
description: Refresh HANDOVER.md so the next session can pick up cold
---

Update `HANDOVER.md` from what actually happened this session (check `git log`, `git status` and `PLAN.md`):

- **Current state:** phase, branch, next unchecked task, today's date.
- **What exists:** add new modules/scripts in one line each; remove anything deleted.
- **Decisions made:** any choice that is not obvious from the code, with the reason. If it changes the
  design, also update `PLAN.md`.
- **Blockers / needs Soham:** accounts, hardware, approvals still pending.
- **Session log:** one row: date, task ids, outcome (include failing tests or half-done work honestly).

Keep it under ~80 lines; drop stale detail into the session log or delete it. Commit as
`docs: update handover`.
