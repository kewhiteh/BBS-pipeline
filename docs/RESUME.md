---
project: BBS-pipeline
aliases: [BBS pipeline, BBS ETL]
state: active
updated: 2026-11-09
review: yes   # ruleset on main requires a PR (gh api repos/kewhiteh/BBS-pipeline/rules/branches/main), checked 2026-11-07
last_audit: none recorded
---
# Resume
## Next
Redesign PR 2 of 3 (Build page cards, U6 toggles, U7 continuity default) per instructions/UI_REDESIGN_PLAN.md, after Kyle reviews PR 1. Open question for Kyle: U18 (Settings per profile vs machine-wide).
## Where I left off
Last merge on main: b00e01a (kewhiteh/BBS-pipeline#25). Open: kewhiteh/BBS-pipeline#26 (redesign plan, docs) and redesign PR 1 on branch `ui-redesign-1-settings-header`, stacked on #26.
## Checks at stop
`uv run ruff check .` green; `uv run ruff format --check .` green; `uv run pytest -q` green, 176 passed. 2026-11-09, branch `ui-redesign-1-settings-header`.
## Blocked / DECISION needed
- None.
## Read next
- [instructions/UI_REDESIGN_PLAN.md](instructions/UI_REDESIGN_PLAN.md): editor redesign decisions and mock-up
- [STATUS.md](STATUS.md): verified facts, decisions, limitations, open items
- [instructions/BBS_V3_REFACTOR_PLAN.md](instructions/BBS_V3_REFACTOR_PLAN.md): active plan and D1–D13 decision table
- [REPO_STANDARD.md](REPO_STANDARD.md), [../AGENTS.md](../AGENTS.md), [../CHANGELOG.md](../CHANGELOG.md)
