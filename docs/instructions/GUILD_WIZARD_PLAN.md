# Guild File Wizard: Plan

Status: **built 2026-11-09** (kewhiteh/BBS-pipeline#59 core, then the page); waiting for Kyle to use it. Kyle will judge details
by using the built page; the defaults below are meant to be changed after that.

Mock-up (private to Kyle): https://claude.ai/artifact/TeEqTJkMQEEZv5JEXQPNMJ (version 1 = the flow, signed off; version 2, 2026-11-09 = condensed layout after Kyle's first use: "could be more condensed … so there's no scrolling needed"; signed off and built the same day)

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

1. **Done 2026-11-09.** `bbs_pipeline.guild_builder`: name matcher (G6, G7) with `guilds/name_changes.csv`, and the checks (G9) as plain functions; CLI `bbs guilds match` / `check`. Tests on the fixture release.
2. **Done 2026-11-09 (with 3 and 4, one PR).** Guild File page, steps 1–2 (start, traits) and Save/Download (G3, G4, G11).
3. **Done.** Step 3 (G5, G6, G8): search, study area, paste-and-match.
4. **Done.** Step 4 (G9) and the "Use in the open profile" link to Build. Browser check (Playwright) of the whole flow.
5. Kyle builds the GHY 5812/5818 guild file with it; then H9.

## Open questions (answer by using it)

- Does one trait value per pasted list match how Kyle reads papers, or is a per-line value column needed?
- Is All rows the right table default when starting from the 412-row starter?
- Is the open profile's study area enough (G10)?

## As built (differences from the mock-up)

- Steps 2–4 shipped in one PR, not three.
- The stepper is a row of buttons; steps after Start unlock once a working copy exists.
- Starting again needs "Replace the current working copy" ticked.
- Pasted-list confirmations are a "Use" tick column in the match table (exact matches
  ticked, suggestions not).
- Empty trait cells show "—" in the table (Streamlit shows an empty dropdown cell as
  "None"); the file stores a blank.
- Columns with more than 50 distinct values (e.g. the starter's `pwrc_name`) are kept as
  free text, like `notes`, not offered as traits.
- Verified: unit tests (`tests/test_guild_draft.py`, `tests/test_guild_builder.py`), an
  AppTest of the whole flow ending in an Inspect with the saved file, and a Playwright
  run on the real release (starter → paste and match → check → save; no errors).
  Not yet used by Kyle.

## Condensed layout (mock-up v2, built 2026-11-09)

| # | Decision | Reason |
|---|---|---|
| G13 | One slim bar: "Guild File" (intro as its tooltip), the four step buttons, Back and Next. No step headings inside the steps. | Kyle: full-width boxes waste space; navigating needed scrolling. |
| G14 | Traits as cards, 3 per row (`TRAIT_COLUMNS`; Streamlit has no auto-fill grid): name, rows filled and a delete icon on one line, then the values box; "New trait" is the last card. | Five starter traits fit on one screen. |
| G15 | Add Species tabs are two columns: inputs and buttons left (1/3), results right (2/3). | No scrolling to reach Assign. |
| G16 | Classify & Check: table left (3/4, scrolls inside, max 560 px), side column right with the checks as badges (grey "not checked yet" until the study area is looked up), problems, File, Save, Download. | Save and the checks always in view. |

Checked at 1920 × 1080 in Chromium: every step fits without page scrolling except
the Classify table, which scrolls inside itself.
