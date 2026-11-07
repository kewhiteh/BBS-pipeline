# Status & known limitations

What is known, unknown, and decided about BBS-Pipeline. Update this file in the same
change that discovers or fixes any of it. The phase plan and its decisions live in
[instructions/BBS_V3_REFACTOR_PLAN.md](instructions/BBS_V3_REFACTOR_PLAN.md).

## Verified

- `uv run ruff check .`, `uv run ruff format --check .`, `uv run pytest -q` pass
  (2026-11-07; count in the latest PR).
- Pick lists stay up while the draft is briefly invalid (e.g. 1995 with the `50_stop`
  source, mid-edit) and selections are kept: `test_pick_lists_survive_a_briefly_invalid_profile`.
- Inspect with every join table on "All" works on the 2026 release.
- Code names for BCR (37 codes), Stratum (71), RunType (2), and QualityCurrentID (2) are
  read from the release metadata XML (`<attr>/<edom>` lists), checked against the
  2026 release (1966–2025). The editor and `bbs values` show them as `27 · Southeastern
  Coastal Plain`; tests in `tests/test_ui.py` cover this.

## Decisions

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
  shown only for GIS formats, where they're used.

## Known limitations / workarounds

- **Restart Streamlit after pulling or switching branches.** A running server reruns
  `ui/app.py` but keeps the `bbs_pipeline` modules and cached pick lists it already
  loaded, so new app code can crash against old library code (a full red error page).
  Ctrl+C, then `uv run streamlit run ui/app.py`.
- Overlays label each polygon from the first of `name, Name, NAME, id, ID, basin, BASIN,
  BCR, bcr` it has, else its first attribute column (`spatial/geometry.py`). There is no
  way yet to choose the column. Overlays match routes by **intersection** with the start
  point.
- Code names are read from `source.raw_dir`, not the cache. A setup that has only a
  cache (raw files deleted) shows bare codes. Workaround: keep the release files, or run
  **Fetch release** again.
- RunType and QualityCurrentID pick lists offer only 0 and 1 (plus any value already in
  the profile), not the codes found in the data.
- **Observer tenure tiers are not route-specific (known error, fix pending decision).**
  Kyle (2026-11-07, from the literature): tenure used as a condition, variable or
  constraint is **route-specific**. In `stages/runs.py`, `is_first_year` is per
  (route, observer) and correct, but `obs_total_years`, which sets `tenure_tier` and
  drives `drop_novice_observers` / `veteran_only`, counts the observer's distinct years
  on **any** route, over the **whole record including later years**. A first run on a
  route by someone who later surveys for a decade is tagged "veteran". Until it's fixed,
  don't use `tenure_tier`, `drop_novice_observers` or `veteran_only` in analyses.
  `drop_first_year_on_route` and `is_first_year` are fine.

## Environment notes

- Moving the project folder breaks `.venv`: its scripts keep the old absolute Python
  path, so `uv run streamlit ...` fails with "Failed to spawn ... No such file or
  directory". Fix: `rm -rf .venv && uv sync --extra dev`. Plain `uv sync` leaves out
  ruff and pytest.

## Open items

- **DECISION (Kyle): how route-specific tenure counts years.** Kyle's rules
  (2026-11-07): count per route; the first year an observer appears for a route in the
  release is year 1; years after `runs.end_year` don't count. Defaults proposed: years
  before `start_year` count (started 1990 → the 1995 run is year 6); distinct years, not
  runs; runs dropped by RunType/quality filters still count as experience.
  **Still open:** a running count as of each run (1995 = 1 … 2000 = 6; recommended), or
  one total through `end_year` per observer–route (every run = 6, so a first-year run can
  be tagged veteran). Research: USGS trend models use an observer–route effect plus a
  first-year ("start-up") indicator, all per route (Kendall, Peterjohn & Sauer 1996, Auk
  113:823; Link & Sauer 2002; Sauer & Link 2011). Length of service as a running count
  is rare for the North American BBS (Farmer et al. 2014 notes USGS doesn't model it);
  Eglington et al. 2010 (Bird Study 57:141) did it for the English BBS. Checked from
  abstracts and summaries, not full texts. Then fix `compute_observer_tenure`, rename
  `obs_total_years` to match, and add tests.

- Help text is unreviewed by Kyle. Check the wording, especially the observation source,
  stop layout, and tenure descriptions.
- The UI has no in-app walkthrough or example profile picker for first-time users beyond
  tooltips, tab captions and the sidebar "What is a profile?" note.
- Overlays are still a `name=path` text box. A file picker with a column chooser would
  be friendlier.

## How to reproduce key checks

```bash
uv sync --extra dev
uv run ruff check . && uv run ruff format --check . && uv run pytest -q
uv run bbs values --config project.toml routes.bcrs   # codes with names
uv run streamlit run ui/app.py                        # Routes & Runs tab: named BCRs/strata
```
