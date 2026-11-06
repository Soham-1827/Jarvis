---
description: Pick up the next Jarvis task from PLAN.md and drive it to done (plan, TDD, review, commit, handover)
argument-hint: "[optional task id, e.g. 1.4]"
---

You are developing Jarvis. Run this loop for exactly ONE task, then stop and report.

## 1. Orient
- Read `HANDOVER.md`, then the current phase in `PLAN.md`.
- Task = `$ARGUMENTS` if given, else the first unchecked `- [ ]` task in the current phase.
- If the task is blocked on Soham (accounts, VPS access, phone setup), say what is needed and stop.
- Ensure you are on branch `phase-N` (create it from `main` if missing). Working tree must be clean.

## 2. Plan
- For a task touching 3+ files or a new module, use the **planner** agent with the task text,
  the relevant `PLAN.md` section and the current file tree. Otherwise write a 3-6 line plan yourself.
- Show the plan in one short message. Continue without waiting unless it changes a decision in `PLAN.md`;
  in that case ask Soham first.

## 3. Build test-first
- Use the **tdd-guide** approach: write failing tests in `tests/` mirroring the module path, run
  `uv run pytest` to see them fail, implement the minimum, run again until green.
- Mock external CLIs, network and OS calls. No real model calls in tests.

## 4. Verify
Run and fix until all pass:
```
uv run ruff format . && uv run ruff check . && uv run pytest --cov=jarvis --cov-report=term-missing
```

## 5. Review
- Run the **python-reviewer** agent on the diff (`git diff`).
- Also run **security-reviewer** if the diff touches `api/`, `approvals/`, `workers/`, `mcp_server/`,
  `node/os_hooks/`, auth, secrets, or subprocess/shell code.
- Fix CRITICAL and HIGH findings, then re-run step 4.

## 6. Close out
- Tick the task in `PLAN.md` (`- [x]`).
- Update `HANDOVER.md`: next task, anything learned, new blockers, one session-log row.
- Commit with a conventional message referencing the task, e.g. `feat(brain): orchestrator stream runner (1.4)`.
- Do not push unless Soham asked to; say that the branch is ready.

## 7. Report
Three to five lines: what was built, test/coverage result, review findings fixed, next task.
If this was the last task of the phase, suggest running `/gate`.
