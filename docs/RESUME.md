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
Kyle reviews kewhiteh/BBS-pipeline PR for branch `ui-reactive-obs-source` (10_stop default, reactive Source list); then editor help text review; docs/STATUS.md "Open items".
## Where I left off
Last merge on main: 66433ac (kewhiteh/BBS-pipeline#24, standard core v1 sync).
This session: branch `ui-reactive-obs-source`: default source 10_stop; editor hides 50_stop before 1997 and auto-switches with a note.
## Checks at stop
`uv run ruff check .` green; `uv run ruff format --check .` green; `uv run pytest -q` green, 176 passed. 2026-11-09, branch `ui-reactive-obs-source`.
## Blocked / DECISION needed
- None.
## Read next
- [STATUS.md](STATUS.md): verified facts, decisions, limitations, open items
- [instructions/BBS_V3_REFACTOR_PLAN.md](instructions/BBS_V3_REFACTOR_PLAN.md): active plan and D1–D13 decision table
- [REPO_STANDARD.md](REPO_STANDARD.md), [../AGENTS.md](../AGENTS.md), [../CHANGELOG.md](../CHANGELOG.md)
