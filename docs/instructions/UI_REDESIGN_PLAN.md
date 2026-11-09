# Editor (Streamlit) redesign plan

Status: **waiting for Kyle's sign-off on the clickable mock-up.** No app code changes until
then (rule: GUI changes get a clickable mock-up first, 2026-11-09).

Mock-up (private to Kyle): https://claude.ai/artifact/8TPjBea1pDvYzkEqj9G7aV
(Build page and Settings page; the numbers in it are illustrative, not real counts).

## Why

The editor today is a profiles sidebar plus five tabs that mirror the profile file's
sections (`[source]`, `[routes]`, ...), not the way a dataset is built. Release & Cache
(changes about once a year) sits at the same level as the year range. A change on one
tab can act on another (the 50_stop auto-switch note shows on Species & Guilds when the
years change on Routes & Runs). Kyle's v2 app (tag v2.0.0-rc1, `src/bbs_pipeline/ui/app.py`)
was one page with Run and download at the bottom, and he preferred that flow.

## Decisions (Kyle, 2026-11-09)

| # | Decision | Reason |
|---|----------|--------|
| U1 | One **Build** page: cards *Where*, *When & how counted*, *Which birds*, then *Output*, then *Check and run*. No tabs, no sidebar. | Reads top to bottom like the task; fewer clicks to one file. |
| U2 | Release, folders, download settings, cache rebuild, saved projections, logs move to a **second Settings page**. Build page shows a one-line release status; a "fetch the release" card appears only when none is fetched. | Settings change once a year; there are enough of them to need a page, not a dialog. |
| U3 | Profiles move to a header toolbar: picker, Unsaved badge, Save, a menu (Save as, New, Start from example, Open, View/download TOML). | Frees the sidebar; profile actions stay one click away. |
| U4 | **Run writes the file and offers a download.** | Both are wanted. |
| U5 | **Inspect stays a button**; after any edit the last counts are marked out of date. | Kyle likes the explicit check; each Inspect is a real data pass. |
| U6 | RunType and QualityCurrentID are **visible toggles** ("standard runs only", "passed QC only"), not hidden. On = `[1]`, off = all. | Only codes 0 and 1 exist; cheap to show. |
| U7 | `runs.min_continuity` sits under "More" and **defaults to 0.8**. | Kyle wants academic-rigour files without opening it every time. Changes the config default for the CLI too (GUI/CLI parity); profiles that omit it will filter at 80%. |
| U8 | Years, source and stop layout share one card; 50_stop only offered from 1997, "all 50 stops" only with 50_stop. | The auto-switch note appears next to the control that caused it. |
| U11 | Small single choices (source, stop layout, output shape long/wide) are **horizontal radio buttons** (`st.radio(..., horizontal=True)`), not segmented buttons or a checkbox. | Kyle, mock-up review: cleaner, more compact, and it is obvious which one is selected. |
| U12 | Years are **one two-handle range slider** (start, end), as `st.slider` with a tuple value. | Kyle, mock-up review: two sliders waste space. |
| U13 | The output file-name box is narrow (~340 px), not full row width. | Kyle, mock-up review. |
| U14 | **Joins are visible** in the Output card, not in an expander: one box per table (Routes, Weather, Observer, Guild traits), each with None / Some / All radios; Some shows a column picker. Projection (CRS) stays in a collapsed expander that shows the current code. | Kyle, mock-up review: joins were hidden under "Extra columns & projection" and read as missing. The first mock-up also left out guild trait joins. |
| U15 | Join modes and output shape use **stacked (vertical) radios**; the four join boxes are narrow (~180 px min) so they sit in one row. | Kyle, mock-up review: more compact than wide rows. |
| U16 | **Earthy theme**, set only in `.streamlit/config.toml` (no CSS): background `#F2ECDD` (parchment), inputs `secondaryBackgroundColor #E6DDC6`, text `#2E3B2B` (dark forest), borders `#CFC3A5`, links `#4E6B36` (moss), `primaryColor #B5432F` (brick red), info `#DDE6CF` (sage), warning `#F3E2A9` (mustard), success `#D3E3C2`. | Kyle, mock-up review: not a fan of white; wants nature/outdoors colours, red kept for sliders. Streamlit has one `primaryColor` for sliders, radios, checkboxes, chips *and* primary buttons, so Run is brick red too unless we add CSS. Green headings in the mock-up also need CSS or `:green[...]` markup. |
| U9 | The Getting started checklist is removed. | The single page is the checklist; the no-release card covers the one prerequisite. |
| U10 | Every profile option stays reachable in the editor; profile format and CLI are unchanged except U7's default. | GUI/CLI parity rule. |

## Build order (after sign-off)

1. Settings page and header toolbar; remove sidebar and checklist.
2. Build page cards and Output strip; U6 toggles; U7 default (config + template + docs).
3. Check-and-run area (Inspect / stale marker / Run + download), wide layout, light theme in
   `.streamlit/config.toml`.

Each PR keeps the existing `ui_*` widget keys where the widget survives so the UI tests
carry over; tests that look for tabs or the checklist are rewritten.

## Open questions

- Anything Kyle changes after clicking through the mock-up.
