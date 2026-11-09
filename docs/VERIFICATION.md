# Verification record

What has been checked to work, how, and when, and what has **not** been checked yet.
Anyone using the outputs: anything marked **Not verified** is untested on real data, so
use it with caution and check it yourself first.

Update this file in the same change that verifies (or breaks) something: set the status,
the date, and the evidence (a command and its result, a test name, or who checked what).

## How things are verified

| Kind | What it means | How to repeat it |
|---|---|---|
| **Script** | `scripts/validate_outputs.py` runs the pipeline on the real release and compares the outputs with the raw release files (read with plain polars, not the pipeline's loader) and with each other. Deterministic: same release, same result. | `uv run python scripts/validate_outputs.py` (default NC, 1990–2024; `--states VA MD --start 1980 --end 2025` for another scope). Needs the release in `data/raw`. Exit 0 = all pass. |
| **Tests** | `uv run pytest -q`: unit and integration tests on small fixtures, including a byte-exact golden slice of the real release (12 NC routes, all years). Run on every PR. | `uv run pytest -q` |
| **Hand** | A person opened or compared something and wrote down what they saw. | As described in the row. |
| **Not verified** | Built, and maybe unit-tested on synthetic data, but never checked against real data or in the target software. | — |

## Kyle's hand-check queue

Open until every item is checked; docs/RESUME.md "Next" points here until then. When one
is done, move its result into the feature checklist below (status **Verified (hand)**,
date, what was seen) and tick it here.

- [x] **H1 GeoPackage in ArcGIS Pro**: done 2026-11-09; result in the checklist below.
- [x] **H2 Other projection in ArcGIS Pro**: done 2026-11-09; result in the checklist below.
- [x] **H3 FlatGeobuf, GeoJSON, GeoParquet in ArcGIS Pro**: done 2026-11-09; result in the checklist below.
- [ ] **H4 RunType / QC toggles on real data**: Inspect, then toggle each off; the runs
  kept at that stage go up, and nothing else changes.
- [ ] **H5 Continuity on real data**: Inspect at 80% and at 0%; fewer routes at 80%, and
  a route dropped at 80% really has runs in fewer than 80% of the years.
- [ ] **H6 Drop observer's first year**: with it ticked, no row has `is_first_year` true;
  the runs-kept count drops.
- [ ] **H7 Subspecies lumping**: Dark-eyed Junco with lumping on includes its forms
  (one row per run); with it off, the forms are separate rows.
- [ ] **H8 Rolling mean**: for one route and species, the rolling value equals the mean
  of that year and the previous N−1 calendar years by hand.
- [ ] **H9 Your guild CSV**: it loads; Inspect's guild report shows the expected matches;
  one deliberate AOU typo is reported as unmatched; filters keep the right species.
- [ ] **H10 Boundary polygon**: with a real polygon (e.g. a county), only routes starting
  inside are kept (compare with the map in Pro).
- [ ] **H11 Overlays**: a real polygon layer labels each route with the polygon its start
  point is in.
- [ ] **H12 Manifest**: rerun from a `<file>.manifest.json` and get an identical file.
- [x] **H13 Large extent**: done 2026-11-09; result in the checklist below ("Large
  extent").

## Script checks (`scripts/validate_outputs.py`)

Kyle ran it 2026-11-09 after merging kewhiteh/BBS-pipeline#35: all PASS (default scope).
It must be rerun after every code change (AGENTS.md).

Run 2026-11-09 by Claude, on the 2026 release (1966–2025), both scopes all PASS:
NC 1990–2024 (20 s) and VA + MD + ON 1980–2025 (25 s). The script uses the profile
defaults (RunType 1, QualityCurrentID 1, continuity 0.8, zero-fill on) with subspecies
lumping **off**, so its raw comparison is one AOU to one AOU.

| Check | What it proves | Status | Evidence (VA + MD + ON, 1980–2025) |
|---|---|---|---|
| V1 raw spot-check | Every 50-stop route total equals the raw Stop1..Stop50 sum for that run and species; every raw record of a kept run is in the output. | **Verified (script)** | 145,710 rows match the raw sums; 145,710 raw records of 2,246 kept runs all present |
| V2 stop layouts | Route total = sum of the five 10-stop bins = sum of the 50 stops, same rows. | **Verified (script)** | 264,440 run × species rows identical in all three layouts |
| V3 1997 seam | From 1997 the 10-stop and 50-stop sources give the same totals; hybrid = 10-stop before 1997, 50-stop after; the raw files agree (States.zip SpeciesTotal = 50-stop sum). | **Verified (script)** | 580,413 rows agree between sources; raw files agree on 357,385 records |
| V4 zero-fill | Zero rows are exactly the zero-filled rows; every run of a route lists the same species set; no zeros in a run with no records. | **Verified (script)** | 118,730 zero rows, all flagged; 2,246 runs on 86 routes consistent |
| V5 long vs wide | Every wide year cell equals the long total; empty cells have no long row. | **Verified (script)** | 264,440 filled cells equal; 19,256 empty cells have no run |
| V6 CLI vs editor/API | `bbs run` and `run_profile()` (what the editor's Run calls) write byte-identical files; a rerun is identical. | **Verified (script)** | identical 46,469,739-byte CSVs |
| V7 formats | CSV, Parquet and GeoPackage hold the same rows and values; GeoPackage points are at the route's Longitude/Latitude in EPSG:4326. | **Verified (script)** | 264,440 rows × 26 columns identical in all three |

Found while building the script (2026-11-09): in the raw 10-stop files `StopTotal` is the
**number of stops** where a species was detected, not a count; the count is
`SpeciesTotal`. The pipeline already uses `SpeciesTotal`
(`test_ten_stop_abundance_is_species_total_not_stop_total`). The raw 50-stop file also
holds 6 NC runs from before 1997.

## Feature checklist

| Feature or claim | Status | Evidence / what is still open |
|---|---|---|
| Counts match the raw release (50-stop, 10-stop, hybrid) | **Verified (script)** | V1, V3 |
| Route total, 10-stop bins and 50 stops agree | **Verified (script)** | V2 |
| Zero-fill only for species recorded on the route, only in kept runs | **Verified (script)** + tests | V4; `test_zero_fill_uses_route_historical_pool`, `test_species_never_recorded_on_route_get_no_rows` |
| Long and wide shapes agree | **Verified (script)** | V5 |
| Command line and editor produce the same file | **Verified (script)** | V6 (the editor's Run calls `run_profile`) |
| CSV, Parquet, GeoPackage agree; points in the right place | **Verified (script)** | V7 (EPSG:4326 only) |
| Golden slice of the real release reproduces exactly | **Verified (tests)** | `tests/test_golden.py`, 12 NC routes, all years, including `test_golden_counts_match_the_raw_fifty_stop_file` |
| RunType / QualityCurrentID filters keep the right runs | Tests only | `test_user_run_filters_are_not_overridden`, `test_excluded_run_never_contributes_counts`; **not checked on real data by hand** (Inspect counts as the toggles change) |
| Continuity filter (default 0.8) | Tests only | `test_cancelled_2020_survey_does_not_count_against_continuity`; not hand-checked on real routes |
| Drop observer's first year / `is_first_year` | Tests only | `test_observer_first_year_is_per_route`; not hand-checked on real data |
| Subspecies lumping | Tests only | `tests/test_taxonomy.py` (forms counted under the species); **not checked against the raw release** (the script runs with lumping off) |
| Rolling mean | Tests only | `test_rolling_mean_uses_calendar_years_not_rows`; values not hand-checked on real data |
| Guild file: matching, filters, trait joins | Tests only | `tests/test_guilds.py`; **Kyle's own guild CSV not yet checked** (try one deliberate AOU typo: it should be reported as unmatched) |
| Boundary polygon filter | Tests only | `tests/test_spatial_integrity.py`; not checked with a real polygon |
| Overlays (route labels from polygons) | Tests only | not checked with a real layer |
| Other projections (e.g. EPSG:5070 Albers) | **Verified (hand)** 2026-11-09 (EPSG:5070) | Kyle, ArcGIS Pro: NC 2015–2024 GeoPackage in EPSG:5070; Pro reads NAD 1983 Contiguous USA Albers, WKID 5070; points sit exactly on the H1 (EPSG:4326) points. Claude checked the file: 43,046 points on 50 NC routes, reprojected back to 4326 they equal the release Latitude/Longitude (max difference 2e-14 degrees). Other EPSG codes: tests only. At 1:212 Pro draws the 4326 and 5070 points about 1 m apart: a datum shift, see STATUS.md "EPSG:4326 output is NAD83 numbers" |
| GeoPackage opens correctly in ArcGIS Pro | **Verified (hand)** 2026-11-09 | Kyle, ArcGIS Pro: all routes 2015–2024 (RunType 1, QC 1, continuity 0.8), EPSG:4326, 1,489,901 points on 1,635 routes. Points on the route starts across the US and Canada; AOU is a Text field with leading zeros; field names fine. Claude checked the same file: every point equals its route's Latitude/Longitude in the release (max difference 0.0) |
| FlatGeobuf, GeoJSON, GeoParquet open in ArcGIS Pro | **Verified (hand)** 2026-11-09, ArcGIS Pro 3.7.3 | Kyle: GeoParquet is not listed as `.geoparquet`; a copy renamed `.parquet` adds directly (Pro draws it with feature binning until binning is turned off) and its points equal the EPSG:5070 GeoPackage's. GeoJSON is not listed; **JSON To Features** imports it with the same points. FlatGeobuf is not listed: Pro 3.7 can't read it. Claude checked the files: all three hold the same 43,046 points as the GeoPackage (EPSG:5070, identical coordinates per route/year/AOU; FlatGeobuf stores rows in spatial-index order), AOU as text. The editor's format list now says this |
| Manifest reproduces the same file | **Not verified** | rerun from `<file>.manifest.json` and compare |
| Large extent (all states, 1966–2025): time and memory | **Verified (hand)** 2026-11-09, with a limit | Kyle, H13 profile (RunType 1, continuity 0, zero-fill, 10-stop bins, all joins, 151 columns). Speed is fine: 20 states (4.69 M rows) Inspect 5.4 s. Memory is the limit: attempt 1 (all states) ran WSL (15.4 GB) out of memory and crashed it; attempt 2 peaks 1 / 10 / 20 states = 5.2 / 7.0 / 13.25 GB. After kewhiteh/BBS-pipeline#44 a 20-state Run peaks 10.5 GB. All states with everything on (~13.7 M rows, ~15 GB table) does not fit; the memory check (kewhiteh/BBS-pipeline#43) stops it in 2 s instead of crashing. Whole-country builds in chunks: future item in STATUS.md |
| CSV opened in Excel keeps AOU leading zeros | **Known limitation** | Excel turns `07550` into `7550`; import the AOU column as text. R, Python and ArcGIS keep them. |
| Editor: every setting behaves as labelled | Tests + hand | `tests/test_ui.py` (54 tests; AppTest passes picks by value, so browser-only bugs need hands-on use). Kyle's use 2026-11-09 found and got fixed: state search, area cross-checks, layout, NC then BCR 29 dropping NC |
