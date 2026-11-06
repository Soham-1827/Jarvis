---
description: Check the current Jarvis phase gate and record the result
---

1. Read `HANDOVER.md` and the current phase's tasks and **Gate** line in `PLAN.md`.
2. List any unchecked tasks. If there are some, report them and stop.
3. Run the full checks: `uv run ruff check . && uv run pytest --cov=jarvis`.
4. Turn the gate into a short manual test script (numbered steps Soham can run on the phone/laptop/VPS),
   plus anything you can verify automatically yourself (health endpoints, CLI smoke tests). Run the
   automatic parts; ask before touching the real VPS.
5. Ask Soham to run the manual steps and report pass/fail.
6. On pass: note it in `HANDOVER.md` (phase complete, date), move "Current state" to the next phase,
   and offer to open a PR from `phase-N` to `main`.
   On fail: add the failures as new tasks under the current phase in `PLAN.md` and update `HANDOVER.md`.
