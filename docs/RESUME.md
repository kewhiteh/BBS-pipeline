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
Kyle clicks through the editor redesign mock-up (link in instructions/UI_REDESIGN_PLAN.md) and signs off or asks for changes; then build it in 3 PRs per that plan.
## Where I left off
Last merge on main: b00e01a (kewhiteh/BBS-pipeline#25, 10_stop default and reactive Source list).
This session: branch `docs-ui-redesign-plan`, docs-only PR recording redesign decisions U1–U10 and the mock-up.
## Checks at stop
`uv run ruff check .` green; `uv run ruff format --check .` green; `uv run pytest -q` green, 176 passed. 2026-11-09, branch `ui-reactive-obs-source`.
## Blocked / DECISION needed
- None.
## Read next
- [instructions/UI_REDESIGN_PLAN.md](instructions/UI_REDESIGN_PLAN.md): editor redesign decisions and mock-up
- [STATUS.md](STATUS.md): verified facts, decisions, limitations, open items
- [instructions/BBS_V3_REFACTOR_PLAN.md](instructions/BBS_V3_REFACTOR_PLAN.md): active plan and D1–D13 decision table
- [REPO_STANDARD.md](REPO_STANDARD.md), [../AGENTS.md](../AGENTS.md), [../CHANGELOG.md](../CHANGELOG.md)
