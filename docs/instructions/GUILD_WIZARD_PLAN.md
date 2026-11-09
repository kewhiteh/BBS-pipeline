# Guild File Wizard: Plan

Status: **mock-up signed off by Kyle, 2026-11-09; not built yet.** Kyle will judge details
by using the built page; the defaults below are meant to be changed after that.

Mock-up (private to Kyle): https://claude.ai/artifact/TeEqTJkMQEEZv5JEXQPNMJ

## Why

Building a guild CSV by hand is where people get stuck (H9 is waiting on Kyle's file):
names from papers don't match the BBS list (*Dendroica* → *Setophaga*), trait values
drift (`interior` vs `Interior`), you only learn which recorded species lack a row after
a run, and every AOU code is looked up elsewhere. The guild file format itself
([docs/guild_file.md](../guild_file.md)) stays as it is; the wizard only writes it.

## Decisions

| # | Decision | Reason / source |
|---|---|---|
| G1 | A general-purpose **Guild File** page in the editor (beside Build and Settings), for any guild file in any project. Nothing project-specific is built in. | Kyle, 2026-11-09: "I hope you aren't building a single purpose widget." |
| G2 | Four steps: **Start → Traits → Add Species → Classify & Check**, one visible at a time, with a clickable stepper. | Mock-up, signed off. |
| G3 | Start: blank, the USGS Patuxent starter (`guilds/pwrc_species_groups.csv`), or an existing CSV. Chosen per file; no default tied to a project. The starter is copied under a new name, never edited. | Kyle: decide per file. |
| G4 | Each trait has a **fixed list of allowed values**; table cells are dropdowns. Values are normalised (lower case, spaces → `_`) when added. Trait names that are BBS output columns are refused, as the guild loader already requires. | Stops typos creating new guilds. |
| G5 | Add species three ways, in any mix: search the release; **recorded in my study area** (species on the open profile's kept runs with no row yet); **paste a list from a paper**. A species is never added twice. | Kyle: both pasted lists and one-by-one edits. |
| G6 | Pasted lists: the box starts **empty** (placeholder only). Each line is matched against the open release: AOU, current scientific name, current common name (exact, case-insensitive), then the old-name table (G7), then close spellings. Exact matches are used; old-name and spelling suggestions must be **confirmed** per line; no-match lines are shown and can be skipped or searched. | Kyle: the mock-up's list is only an example. |
| G7 | Old names come from a general reference table, `guilds/name_changes.csv` (old name → current scientific name, kind = renamed / split / form / spelling), seeded from `NAME_MAP` in `scripts/build_pwrc_guilds.py` plus AOS checklist genus changes (e.g. *Dendroica*, *Wilsonia* → *Setophaga*; *Oporornis formosus* → *Geothlypis formosa*; *Seiurus motacilla* → *Parkesia motacilla*). Splits offer every daughter species. Not project data. | Spelling similarity can't find genus-and-ending changes. |
| G8 | "Add and assign": one trait value (and optional note/citation) for every confirmed species of a pasted list; existing values are overwritten only after a confirm listing them. | How papers list guilds. |
| G9 | Classify & Check: editable table (dropdown per trait, note/citation column, remove). Live checks: rows matched, rows with an empty trait (allowed; filters skip them), recorded species without a row (with "Add them"), values outside a trait's list (**blocks Save**). Table filter, default **All rows**; also "rows with an empty trait", "rows added this session". | Mock-up; default chosen, change after use. |
| G10 | Study area for G5/G9 = the **open profile's** routes, years and run filters (default). Choosing them separately is a later option if use shows a need. | Default; revisit after use. |
| G11 | Save writes the CSV (format unchanged; the `notes` column holds citations) and, if ticked, sets `guilds.file` in the open draft. Download CSV too. Every action is in the activity log. | GUIs are self-contained; logging rule. |
| G12 | CLI parity: the matcher and checks live in `bbs_pipeline` (not the UI), and `bbs guilds check <file> --config <profile>` prints the same checks; `bbs guilds match --config <profile> < list.txt` prints pasted-list matches. | GUI/CLI parity rule. |

## Build order (one PR each)

1. `bbs_pipeline.guild_builder`: name matcher (G6, G7) with `guilds/name_changes.csv`, and the checks (G9) as plain functions; CLI `bbs guilds match` / `check`. Tests on the fixture release.
2. Guild File page, steps 1–2 (start, traits) and Save/Download (G3, G4, G11).
3. Step 3 (G5, G6, G8): search, study area, paste-and-match.
4. Step 4 (G9) and the "Use in the open profile" link to Build. Browser check (Playwright) of the whole flow.
5. Kyle builds the GHY 5812/5818 guild file with it; then H9.

## Open questions (answer by using it)

- Does one trait value per pasted list match how Kyle reads papers, or is a per-line value column needed?
- Is All rows the right table default when starting from the 412-row starter?
- Is the open profile's study area enough (G10)?
