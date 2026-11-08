---
project: BBS-pipeline
aliases: [UNKNOWN (ask Kyle)]
state: active
updated: 2026-11-07
review: yes   # Kyle confirmed branch protection on main, 2026-11-07 (agent token gets 404 from the API)
last_audit: none recorded
---
# Resume
## Next
Kyle reviews the editor help text (observation source, stop layout, first-year wording); docs/STATUS.md "Open items".
## Where I left off
Last merge on main: fd479e2 (kewhiteh/BBS-pipeline#21, ignore .agents/ and .claude/).
This session: branch `repo-standard-docs`, docs-only repo-standard PR (CHANGELOG, RESUME, REPO_STANDARD, AGENTS rules).
Remote branch `spike/legacy-prototype` is dead (Kyle, 2026-11-07).
## Checks at stop
`uv run ruff check .` green; `uv run ruff format --check .` green; `uv run pytest -q` green, 176 passed. 2026-11-07, commit fd479e2.
## Blocked / DECISION needed
- DECISION: aliases for the front matter above.
## Read next
- [STATUS.md](STATUS.md): verified facts, decisions, limitations, open items
- [instructions/BBS_V3_REFACTOR_PLAN.md](instructions/BBS_V3_REFACTOR_PLAN.md): active plan and D1–D13 decision table
- [REPO_STANDARD.md](REPO_STANDARD.md), [../AGENTS.md](../AGENTS.md), [../CHANGELOG.md](../CHANGELOG.md)
