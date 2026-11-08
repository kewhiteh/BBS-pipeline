---
project: BBS-pipeline
aliases: [BBS pipeline, BBS ETL]
state: active
updated: 2026-11-07
review: yes   # ruleset on main requires a PR (gh api repos/kewhiteh/BBS-pipeline/rules/branches/main), checked 2026-11-07
last_audit: none recorded
---
# Resume
## Next
Kyle reviews the editor help text (observation source, stop layout, first-year wording); docs/STATUS.md "Open items".
## Where I left off
Last merge on main: ce17190 (kewhiteh/BBS-pipeline#23, ruleset check for RESUME review field).
This session: branch `standard-core-v1-sync`, docs-only PR syncing docs/REPO_STANDARD.md to core v1.
## Checks at stop
`uv run ruff check .` green; `uv run ruff format --check .` green; `uv run pytest -q` green, 176 passed. 2026-11-07, commit ce17190.
## Blocked / DECISION needed
- None.
## Read next
- [STATUS.md](STATUS.md): verified facts, decisions, limitations, open items
- [instructions/BBS_V3_REFACTOR_PLAN.md](instructions/BBS_V3_REFACTOR_PLAN.md): active plan and D1–D13 decision table
- [REPO_STANDARD.md](REPO_STANDARD.md), [../AGENTS.md](../AGENTS.md), [../CHANGELOG.md](../CHANGELOG.md)
