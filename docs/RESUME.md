---
project: BBS-pipeline
aliases: [BBS pipeline, BBS ETL]
state: active
updated: 2026-11-11
review: yes   # ruleset on main requires a PR (gh api repos/kewhiteh/BBS-pipeline/rules/branches/main), checked 2026-11-07
last_audit: none recorded
---
# Resume
## Next
Every hand check (H1–H13) is done. Kyle's GHY 5812/5818 guild file is in `guilds/ghy5812.csv` and set in `examples/ghy5812_guilds.toml`; next is whatever the project analysis needs (e.g. Run the GHY profile with `guilds.filters`, or the Wood Thrush story map). Open items: STATUS.md "Open items". When Kyle reports an editor problem, read `logs/ui_activity.log` first.
## Where I left off
2026-11-11: Kyle synthesised his guild assignments from Whitcomb et al. 1981 and Freemark & Collins 1992 into a CSV in Google Drive (GIS cert / final project files / "BBS Guild Assignments.csv"). Claude read it through the Drive connector, renamed the `species` header to `common_name` and House Wren to Northern House Wren, and ran H9 on it (all passed). The wizard was not needed for this file: it was already a finished table.
## Checks at stop
`uv run ruff check .` green; `uv run ruff format --check .` green; `uv run pytest -q` 229 passed; `uv run python scripts/validate_outputs.py` all PASS. 2026-11-11, branch ghy5812-guild-file.
## Blocked / DECISION needed
- None.
## Read next
- [instructions/GUILD_WIZARD_PLAN.md](instructions/GUILD_WIZARD_PLAN.md): Guild File wizard decisions, mock-up, build order
- [instructions/UI_REDESIGN_PLAN.md](instructions/UI_REDESIGN_PLAN.md): editor redesign decisions and mock-up
- [STATUS.md](STATUS.md): verified facts, decisions, limitations, open items
- [VERIFICATION.md](VERIFICATION.md): what is verified against real data, by script / tests / hand, and what is not
- [instructions/BBS_V3_REFACTOR_PLAN.md](instructions/BBS_V3_REFACTOR_PLAN.md): active plan and D1–D13 decision table
- [REPO_STANDARD.md](REPO_STANDARD.md), [../AGENTS.md](../AGENTS.md), [../CHANGELOG.md](../CHANGELOG.md)
