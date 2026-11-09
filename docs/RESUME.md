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
Redesign PR 3 of 3 per instructions/UI_REDESIGN_PLAN.md: Inspect & Run area (stale marker, Run writes + download, drop Live inspect per U5), dark earthy theme (U16) and gold Title Case headings (U17) in `.streamlit/config.toml`.
## Where I left off
Last merge on main: kewhiteh/BBS-pipeline#28 (redesign PR 2). Open: branch `ui-settings-projections` (Settings page manages saved projections: add, rename, remove).
## Checks at stop
`uv run ruff check .` green; `uv run ruff format --check .` green; `uv run pytest -q` green, 180 passed. 2026-11-09, branch `ui-settings-projections`.
## Blocked / DECISION needed
- None.
## Read next
- [instructions/UI_REDESIGN_PLAN.md](instructions/UI_REDESIGN_PLAN.md): editor redesign decisions and mock-up
- [STATUS.md](STATUS.md): verified facts, decisions, limitations, open items
- [instructions/BBS_V3_REFACTOR_PLAN.md](instructions/BBS_V3_REFACTOR_PLAN.md): active plan and D1–D13 decision table
- [REPO_STANDARD.md](REPO_STANDARD.md), [../AGENTS.md](../AGENTS.md), [../CHANGELOG.md](../CHANGELOG.md)
