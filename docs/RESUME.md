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
Kyle tries the finished editor in a browser (restart Streamlit from the repo root) and reports anything off; editor help text review (docs/STATUS.md "Open items").
## Where I left off
Last merge on main: kewhiteh/BBS-pipeline#31. Open: kewhiteh/BBS-pipeline#32 (state search by abbreviation, route counts) and, stacked on it, branch `ui-joins-card-compact-settings` (U19–U21: Joined Columns card above Output, narrower output file block, compact Settings).
## Checks at stop
`uv run ruff check .` green; `uv run ruff format --check .` green; `uv run pytest -q` green, 195 passed. 2026-11-09, branch `ui-joins-card-compact-settings`.
## Blocked / DECISION needed
- None.
## Read next
- [instructions/UI_REDESIGN_PLAN.md](instructions/UI_REDESIGN_PLAN.md): editor redesign decisions and mock-up
- [STATUS.md](STATUS.md): verified facts, decisions, limitations, open items
- [instructions/BBS_V3_REFACTOR_PLAN.md](instructions/BBS_V3_REFACTOR_PLAN.md): active plan and D1–D13 decision table
- [REPO_STANDARD.md](REPO_STANDARD.md), [../AGENTS.md](../AGENTS.md), [../CHANGELOG.md](../CHANGELOG.md)
