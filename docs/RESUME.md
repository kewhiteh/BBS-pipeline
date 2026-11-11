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
Goal (Kyle, 2026-11-11): finish the last two open items, then cut a release.
1. Memory estimate for Run: count the file write (GeoPackage converts through geopandas, another copy) in `check_memory`, so a Run near the limit is stopped like Inspect. STATUS.md "Open items" / "Memory check".
2. Whole-country builds with everything on (~14 M rows x 151 columns): write the output in chunks of states so memory stays at the fixed cost plus one chunk. Needs an append path per format (CSV, Parquet, GeoPackage, others) and care with wide output. Plan it first; GUI-visible behaviour (if any) gets a mock-up.
3. Release: `pyproject.toml` already says 3.0.0, but the last tag is `v2.0.0-rc1` and CHANGELOG has a long `[Unreleased]` section. Kyle wants "a fresh clean version number": proposed **v3.0.0** (the v3 refactor plus the editor, verification and guild work). Move `[Unreleased]` to `[3.0.0]`, tag on main after the PR merges (ask Kyle before tagging/creating the GitHub release).
When Kyle reports an editor problem, read `logs/ui_activity.log` first.
## Where I left off
All hand checks H1–H13 done (VERIFICATION.md). Latest merges: GHY 5812/5818 guild file `guilds/ghy5812.csv` + H9 (#63), guild header aliases (#64). The GHY profile `examples/ghy5812_guilds.toml` uses the guild file.
## Checks at stop
`uv run ruff check .` green; `uv run ruff format --check .` green; `uv run pytest -q` 230 passed; `uv run python scripts/validate_outputs.py` all PASS. 2026-11-11, main at kewhiteh/BBS-pipeline#64.
## Blocked / DECISION needed
- None.
## Read next
- [instructions/GUILD_WIZARD_PLAN.md](instructions/GUILD_WIZARD_PLAN.md): Guild File wizard decisions, mock-up, build order
- [instructions/UI_REDESIGN_PLAN.md](instructions/UI_REDESIGN_PLAN.md): editor redesign decisions and mock-up
- [STATUS.md](STATUS.md): verified facts, decisions, limitations, open items
- [VERIFICATION.md](VERIFICATION.md): what is verified against real data, by script / tests / hand, and what is not
- [instructions/BBS_V3_REFACTOR_PLAN.md](instructions/BBS_V3_REFACTOR_PLAN.md): active plan and D1–D13 decision table
- [REPO_STANDARD.md](REPO_STANDARD.md), [../AGENTS.md](../AGENTS.md), [../CHANGELOG.md](../CHANGELOG.md)
