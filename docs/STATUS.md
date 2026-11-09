# Status & known limitations

What is known, unknown, and decided about BBS-Pipeline. Update this file in the same
change that discovers or fixes any of it. The phase plan and its decisions live in
[instructions/BBS_V3_REFACTOR_PLAN.md](instructions/BBS_V3_REFACTOR_PLAN.md).

## Verified

- `uv run ruff check .`, `uv run ruff format --check .`, `uv run pytest -q` pass
  (2026-11-07; results in [RESUME.md "Checks at stop"](RESUME.md)).
- The editor's Source list follows the years: with a start year before 1997, `50_stop`
  is not offered, a draft on `50_stop` is switched to `10_stop` with a blue note (not a
  red error), and a `stops` layout is switched to `ten_stop_bins`. Pick lists and other
  selections are kept: `test_years_before_1997_switch_the_source_off_50_stop`.
- Wide output: a year cell is `null` (shown "None") when the route/species has no kept
  run that year: the route was not run, or the run was filtered out (RunType,
  QualityCurrentID, observer's first year). `0` means a kept run with the species not
  seen (zero-fill on). With `zero_fill = false`, unseen species are also `null`. Read
  from `shape.py` (polars pivot) and the zero-fill stage, 2026-11-09.
- Inspect with every join table on "All" works on the 2026 release.
- Code names for BCR (37 codes), Stratum (71), RunType (2), and QualityCurrentID (2) are
  read from the release metadata XML (`<attr>/<edom>` lists), checked against the
  2026 release (1966–2025). The editor and `bbs values` show them as `27 · Southeastern
  Coastal Plain`; tests in `tests/test_ui.py` cover this.

## Decisions

- **`observations.source` defaults to `10_stop`** (was `50_stop`), and the editor lists
  it first: 10-stop data cover 1966 on, 50-stop only 1997 on, so the widest option is the
  default (Kyle, 2026-11-09). Profiles that omit `source` now read 10-stop data; set
  `source = "50_stop"` to keep the old behavior. The CLI still rejects `50_stop` with a
  start year before 1997 (a profile file has no one to ask); only the editor auto-switches.
- **Code names come only from the release XML, never a typed-in table** (same rule as
  StateNum, see `sources/regions.py`). If the raw release directory has no metadata XML,
  or more than one, the editor shows bare codes instead of guessing names.
- **Editor help text** (tooltips and the intro line on each tab) describes what each
  setting does in plain terms. It was written from `config.py`/`contracts.py`; wording
  about BBS meaning (e.g. RunType, strata) follows the release metadata.
- **"All columns" on two tables joins a shared column once.** `ObsN` is offered by
  both weather and observer (same frame, same values). A `["*"]` list skips columns an
  earlier table already joins; naming one column in two explicit lists still raises.
- **Editor failures are logged** to `logs/ui_errors.log` (git-ignored) with time,
  profile and traceback: Fetch, Inspect, Run, pick-list read errors (once per distinct
  error), and unexpected page errors. The error box names the log and has a
  "Technical details" expander, so errors never need copying off the screen.
- **Output projection list** (`CRS_CHOICES` in `ui/app.py`): EPSG:4326, 4269, 5070,
  32119 (NC State Plane, meters), 3857, plus "Other" for any EPSG code. The v2 GUI
  offered 4326, 5070, 3857 and `nc_state_plane`. The v3 config takes EPSG codes only,
  so NC State Plane is listed as EPSG:32119. The projection, layer name and overlays are
  shown only for GIS formats, where they're used. Kyle approved this list on 2026-11-07.
- **Saved projections.** With "Other", the editor names the typed code from pyproj
  (e.g. `EPSG:26917: NAD83 / UTM zone 17N, metre`), flags unknown codes, and offers
  **Add to my list**. Added codes go in `projections.toml` (`[projections]`
  `"EPSG:26917" = "label"`) at the project root, read relative to where Streamlit is
  started, and appear after the built-ins in every profile. The file is not
  git-ignored: commit it to share the list, or leave it local. Remove entries by editing
  it.

- **Observer experience matches USGS trend analysis: first year on a route, or not**
  (Kyle, 2026-11-07). `runs.drop_observer_first_year = true` drops each observer's
  first year on a route; the `is_first_year` and `ObsN` columns (joins.observer) let an
  analysis model the start-up effect and an observer-route effect instead. A run is a
  first year when it falls in the earliest year that ObsN surveyed that route anywhere in
  the release (route-specific; earlier years count even before `start_year`). The
  novice/veteran tiers (`tenure_tier`, `obs_total_years`, `observer_min_years`,
  `veteran_min_years`, `drop_novice_observers`, `veteran_only`) were removed: they
  counted years on any route, including years after the run. Long-running BBS work
  models only the start-up effect (Kendall, Peterjohn & Sauer 1996, Auk 113:823; Link &
  Sauer 2002; Sauer & Link 2011; Farmer et al. 2014 notes length of service isn't
  modelled). Old `observer_tenure` keys fail with a message saying what to use instead.
  The golden 50-stop expected CSV lost only its `tenure_tier` column; every other value
  is unchanged.

- **Editor pages (redesign PR 1, 2026-11-09).** Two pages, no sidebar: **Build** (the
  dataset settings, still in tabs until redesign PR 2) and **Settings** (release,
  folders, download settings, saved projections, logs). The header on both shows the
  release status, the open profile, an Unsaved/Saved badge, Save, and a **Profile**
  menu (open, new, start from an example, save as, TOML). The Getting started checklist
  is gone (U9). **Start from an example** copies an `examples/*.toml` profile into a new,
  unsaved draft, so examples are never overwritten. Edits on either page share one
  draft: `test_settings_page_holds_the_release_and_build_page_does_not`.
- **Fixed 2026-11-07:** a profile without a guild file read "Unsaved changes" right
  after opening, because the editor cleared the default `joins.guilds = ["*"]` to `[]`.
- **Overlay and boundary files** (Kyle asked for a file picker, 2026-11-07). Overlays
  are cards: name, polygon file, label column. The file list holds GIS files found in
  the project, "Type a path…", and an upload (GeoPackage, GeoJSON, FlatGeobuf,
  GeoParquet, zipped shapefile) saved to `data/gis/` (git-ignored, so uploads stay
  local). The profile stores `overlays = { basin = { file = "...", column = "HUC4" } }`; a
  plain path still works and means an automatic column (the first of `name, Name, NAME,
  id, ID, basin, BASIN, BCR, bcr`, else the first attribute). A column the file lacks
  fails with the file's columns. Zip uploads are refused if a member would unpack outside
  its folder.
- **Branches live in `kewhiteh/BBS-pipeline`, not a fork** (Kyle, 2026-11-07). The agent
  account `kewhitehagent` has write access and pushes feature branches there; a ruleset on
  `main` requires a PR with 1 approval and blocks force pushes and deletion, so nothing
  reaches `main` without Kyle's approval. Merged branches are deleted automatically. The
  old fork `kewhitehagent/BBS-pipeline` (PRs #2–#19) is retired.

## Known limitations / workarounds

- **Restart Streamlit after pulling or switching branches.** A running server reruns
  `ui/app.py` but keeps the `bbs_pipeline` modules and cached pick lists it already
  loaded, so new app code can crash against old library code (a full red error page).
  Ctrl+C, then `uv run streamlit run ui/app.py`.
- Overlays match routes by **intersection** with the start point. A start point on a
  shared border, or in overlapping polygons, takes the **first** matching polygon's label;
  the others are not reported.
- The GIS file list searches the project from where Streamlit is started, skipping
  `.git`, `.venv`, `output/`, `tests/`, `data/raw` and `data/sanitized_cache`, and stops
  at 300 files. Files elsewhere: use "Type a path…".
- Code names are read from `source.raw_dir`, not the cache. A setup that has only a
  cache (raw files deleted) shows bare codes. Workaround: keep the release files, or run
  **Fetch release** again.
- RunType and QualityCurrentID pick lists offer only 0 and 1 (plus any value already in
  the profile), not the codes found in the data.

## Environment notes

- Moving the project folder breaks `.venv`: its scripts keep the old absolute Python
  path, so `uv run streamlit ...` fails with "Failed to spawn ... No such file or
  directory". Fix: `rm -rf .venv && uv sync --extra dev`. Plain `uv sync` leaves out
  ruff and pytest.

## Open items

- Editor redesign (one Build page + Settings page): decisions U1–U10 and the mock-up link
  are in [instructions/UI_REDESIGN_PLAN.md](instructions/UI_REDESIGN_PLAN.md). Mock-up signed
  off 2026-11-09; build next, in 3 PRs. Includes a planned change of the
  `runs.min_continuity` default from 0.0 to 0.8 (U7).
- Help text is unreviewed by Kyle. Check the wording, especially the observation source,
  stop layout, and first-year descriptions.

## How to reproduce key checks

```bash
uv sync --extra dev
uv run ruff check . && uv run ruff format --check . && uv run pytest -q
uv run bbs values --config project.toml routes.bcrs   # codes with names
uv run streamlit run ui/app.py                        # Routes & Runs tab: named BCRs/strata
```
