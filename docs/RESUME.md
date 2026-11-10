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
1. Kyle builds the GHY 5812/5818 guild file with the wizard once he has Whitcomb et al. 1981 (Freemark & Collins 1992 already downloaded); expected around 2026-11-10. Help him; then run H9 on it ([VERIFICATION.md](VERIFICATION.md), the last open hand check).
When Kyle reports an editor problem, read `logs/ui_activity.log` first.
## Where I left off
Hand checks H1–H8 and H10–H13 are done (VERIFICATION.md); H9 waits for the guild file. Merged today after the previous handoff: multiple-runs warning (#40), time/memory log (#41), memory check (#43), memory cuts (#44), Pro format notes (#48), pick-reset fix (#52), manifest rerun (#57), Guild File wizard plan (#58), guild builder core + `bbs guilds match|check` (#59), Guild File page (#60). Kyle's first use of the page: "feeling good", wanted it condensed; mock-up v2 signed off and built (kewhiteh/BBS-pipeline#62, plan G13–G16).
## Checks at stop
`uv run ruff check .` green; `uv run ruff format --check .` green; `uv run pytest -q` 229 passed; `uv run python scripts/validate_outputs.py` all PASS. 2026-11-09, kewhiteh/BBS-pipeline#62 branch (condensed layout).
## Blocked / DECISION needed
- None.
## Read next
- [instructions/GUILD_WIZARD_PLAN.md](instructions/GUILD_WIZARD_PLAN.md): Guild File wizard decisions, mock-up, build order
- [instructions/UI_REDESIGN_PLAN.md](instructions/UI_REDESIGN_PLAN.md): editor redesign decisions and mock-up
- [STATUS.md](STATUS.md): verified facts, decisions, limitations, open items
- [VERIFICATION.md](VERIFICATION.md): what is verified against real data, by script / tests / hand, and what is not
- [instructions/BBS_V3_REFACTOR_PLAN.md](instructions/BBS_V3_REFACTOR_PLAN.md): active plan and D1–D13 decision table
- [REPO_STANDARD.md](REPO_STANDARD.md), [../AGENTS.md](../AGENTS.md), [../CHANGELOG.md](../CHANGELOG.md)
