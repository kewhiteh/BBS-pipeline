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
Kyle hand-checks the rows marked Not verified in docs/VERIFICATION.md (ArcGIS Pro, his guild CSV, filters on real data); mark each off there with date and evidence.
## Where I left off
Last merge on main: kewhiteh/BBS-pipeline#33. Open: kewhiteh/BBS-pipeline#34 (docs: small-screen open item) and branch `validation-script` (scripts/validate_outputs.py V1–V7, docs/VERIFICATION.md).
## Checks at stop
`uv run ruff check .` green; `uv run ruff format --check .` green; `uv run pytest -q` green; `uv run python scripts/validate_outputs.py` all PASS (NC 1990–2024; VA MD ON 1980–2025). 2026-11-09, branch `validation-script`.
## Blocked / DECISION needed
- None.
## Read next
- [instructions/UI_REDESIGN_PLAN.md](instructions/UI_REDESIGN_PLAN.md): editor redesign decisions and mock-up
- [STATUS.md](STATUS.md): verified facts, decisions, limitations, open items
- [VERIFICATION.md](VERIFICATION.md): what is verified against real data, by script / tests / hand, and what is not
- [instructions/BBS_V3_REFACTOR_PLAN.md](instructions/BBS_V3_REFACTOR_PLAN.md): active plan and D1–D13 decision table
- [REPO_STANDARD.md](REPO_STANDARD.md), [../AGENTS.md](../AGENTS.md), [../CHANGELOG.md](../CHANGELOG.md)
