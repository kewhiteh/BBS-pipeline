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
Kyle works through the hand-check queue in [VERIFICATION.md](VERIFICATION.md) ("Kyle's hand-check queue", H1–H13) in a new session; record each result there with date and evidence. Keep this pointer until every item is ticked. When Kyle reports an editor problem, read `logs/ui_activity.log` first (AGENTS.md "Troubleshooting the editor").
## Where I left off
Last merge on main: kewhiteh/BBS-pipeline#38 (editor activity log). Today's merged work: editor redesign (#26–#30), Settings projections and error log (#29), reactive filters (#31, #32, #37), layout (#33), validation script and VERIFICATION.md (#35), validation gate (#36). Open: none besides this handoff PR. Kyle chose to keep Streamlit's WSL poll-based file watching (the startup message is harmless).
## Checks at stop
`uv run ruff check .` green; `uv run ruff format --check .` green; `uv run pytest -q` green, 198 passed; `uv run python scripts/validate_outputs.py` all PASS. 2026-11-09, main at kewhiteh/BBS-pipeline#38.
## Blocked / DECISION needed
- None.
## Read next
- [instructions/UI_REDESIGN_PLAN.md](instructions/UI_REDESIGN_PLAN.md): editor redesign decisions and mock-up
- [STATUS.md](STATUS.md): verified facts, decisions, limitations, open items
- [VERIFICATION.md](VERIFICATION.md): what is verified against real data, by script / tests / hand, and what is not
- [instructions/BBS_V3_REFACTOR_PLAN.md](instructions/BBS_V3_REFACTOR_PLAN.md): active plan and D1–D13 decision table
- [REPO_STANDARD.md](REPO_STANDARD.md), [../AGENTS.md](../AGENTS.md), [../CHANGELOG.md](../CHANGELOG.md)
