# BBS Pipeline — Refactor Plan to a Config-Driven Extraction Tool

**Hand-off brief for Claude Code.** Kyle is the author and the decision-maker. Wherever this plan says **DECISION**, stop and ask him. Do not pick a value yourself.

---

## 0. Before anything else: confirm which code you are working on

This plan comes from a review of `github.com/kewhiteh/BBS-pipeline`, `main` @ `2c88d70` (2026-09-23, tag line `v2.0.0-rc1`). Kyle's working copy is at `~/workspace/antigravity/01_spike/v2_bbs_etl` and may be ahead of it.

1. `cd` into the working copy. Run `git log --oneline -10`, `git status`, and `git diff 2c88d70 --stat` (if that commit exists there).
2. Report the differences to Kyle before starting Phase 1. If files named below have moved or changed, adjust the plan and tell him what changed.
3. Baseline: `uv sync && uv run pytest -q`. On the GitHub copy, 386 tests pass, all of them on mock data.

---

## 1. Assessment (why the code looks the way it does)

The modules are mostly sound. The orchestration around them is the problem, and much of that comes from the agent directives the code was built under.

### 1.1 Structural problems
| # | Problem | Where |
|---|---|---|
| S1 | **`AGENTS.md` will actively block this refactor.** It marks files immutable, makes the work "additive-only Track B", forces a strict task sequence, and imposes a zero-disk rule. It also names files that don't exist (`ingestion/routes.py`, `processing/aggregation.py`, `tests/test_routes_ingestion.py`, `tests/test_aggregations.py`). The "additive hooks only" rule is why everything ended up inside `cli.py`. | `AGENTS.md`, `CLAUDE.md`, `docs/01–06` |
| S2 | `cli.py` is 1,763 lines and does everything: the state lookup tables, argparse, file loaders, a 640-line `run_pipeline()` with 45 kwargs, guild parsing, linear referencing, and export routing. | `cli.py` |
| S3 | There are two near-duplicate Streamlit apps (697 and 789 lines) that share the same docstring. | `gui.py`, `ui/app.py` |
| S4 | The zero-disk mandate makes every run re-stream the full ScienceBase archives (`50-StopData.zip` etc.) into RAM. That is slow, depends on the network, and makes repeated runs painful. | `_find_and_read_file` |
| S5 | There's dead and duplicated code: `ingestion/stops.py` is only imported by its own test, the `--stop-geometry` arg is parsed but never used, covariates are split across `core/covariates.py` and `covariates/traffic.py`, and the `--shape` arg doubles as a shapefile path. | various |
| S6 | `pyproject.toml` has no `[project.scripts]` entry point, and `pytest` and `requests-mock` are listed as runtime deps. | `pyproject.toml` |
| S7 | Tests only check mocks. **No test asserts which columns end up in the output**, which is how the missing routes join got through. | `tests/` |

### 1.2 Correctness bugs (fix these whatever else changes)
| # | Bug | Where |
|---|---|---|
| B1 | **routes.csv attributes never reach the output.** `routes_df` is only used to filter route keys. Lat/Lon get joined only for gpkg/geojson, through `anchor_routes_spatial`. Parquet/CSV output gets no route attributes at all: no RouteName, BCR, Stratum, RouteTypeID, or coordinates. | `cli.py` Step 7, `export/serializer.py:~722`, `export/spatial.py` |
| B2 | **`--no-zero-fill` leaks excluded runs.** That path filters raw observations by RouteKey, Year, and AOU only. Runs dropped by RunType/Quality, month/day, min-stops, observer, or traffic filters still appear, and none of the weather columns get joined. | `cli.py:~1452` |
| B3 | **The hardcoded state code table is wrong in places.** NS and ON both map to `68`, and NT and NU both map to `65`, so at least two entries are provably wrong, and others may be too. Replace the hand-typed table with the region-codes file shipped in the BBS release. Verify NC/VA/SC/TN/GA against that file, not from memory. | `cli.py:78–211` |
| B4 | **Guild dimensions are hardcoded.** Trait names live in Python sets (`known_bh`, `known_fg`) inside `run_pipeline`. Only `breeding_habitat` and `foraging_guild` can be filtered on, `migratory_status` can't be filtered at all, and guild traits are never joined into the output. | `cli.py:~1371`, `core/taxonomy.py:resolve_target_species` |
| B5 | `except Exception` around the traffic join logs a warning and carries on, which is a silent partial result. It should fail unless the user turned vehicle data off. | `cli.py:~1277` |

### 1.3 What is worth keeping
- Raw columns are ingested as strings and cast downstream.
- Zero-fill logic (`core/zero_fill.py`): zeros only for species confirmed on the route, and stops beyond `TotalStops` stay NULL. This is the most valuable part of the codebase. Keep it intact, and keep its tests green.
- `core/filters.py` (continuity, min stops, RouteKey construction), `core/discovery.py` (COVID year exclusion), `core/covariates.py` and `covariates/traffic.py` logic, `client/sciencebase.py`, `export/serializer.py` writers, and `export/spatial.py` CRS presets.

---

## 2. Target design

A single **run profile file** (TOML) per project describes the whole extraction: which raw files to load, every filter (all multi-valued), which tables and columns to join, and how to write the output. The CLI and the UI both just produce or consume that file.

```
bbs fetch  --release <item_id> --dest ~/data/bbs/<year>    # one-time download (cache)
bbs inspect --config project.toml                         # dry run: counts per stage, unmatched guild species, columns to be emitted
bbs run    --config project.toml [--set runs.start_year=2000 ...]
bbs values routes.BCR | species.Family | guilds.<trait>   # list valid values for multi-select
```

### 2.1 Target module layout
```
src/bbs_pipeline/
  config.py            # Pydantic models for the run profile + TOML loading + --set overrides
  sources/             # raw loaders (one function per BBS table) + local cache + region codes
  stages/
    routes.py          # route filters (state/BCR/stratum/route/RouteTypeID/active/spatial)
    runs.py            # weather-run filters (years, months, days, quality, continuity, min stops, observer, traffic)
    species.py         # species resolution (AOU/name/family/order/guild filters, migrant exclusion)
    observations.py    # load counts → zero-fill or raw (both restricted to surviving runs)
    enrich.py          # configurable joins: routes, weather, vehicle, observer covariates, species, guilds
    output.py          # shape, column select/drop/rename, export, manifest
  guilds.py            # pluggable guild file loader + matcher
  pipeline.py          # run(config) -> result; ~100 lines, just calls the stages in order
  cli.py               # thin: subcommands parse args → config → pipeline.run
  ui/app.py            # the one Streamlit app (Phase 8)
```

### 2.2 Run profile sketch (illustrative; final field names are a **DECISION**)
```toml
[source]
raw_data_dir = "~/data/bbs/2024"      # from `bbs fetch`; network fallback optional
resolution   = "50stop"

[routes]
states          = ["NC"]              # abbrev, name, or StateNum; all multi-valued
bcrs            = []
strata          = []
route_ids       = []                  # RouteKey or bare Route
route_type_ids  = []                  # e.g. ["1"] = roadside
active_only     = false

[routes.spatial]                      # optional; replaces the stop-interpolation filter
boundary     = ""                     # polygon file (gpkg/shp/geojson)
method       = "start_point_within"   # | "polyline_intersects" | "polyline_within"
polylines    = ""                     # only for polyline_* methods
polyline_key = ""                     # field in polylines that matches RouteKey/Route

[runs]
start_year = 1997
end_year   = 2024
months     = []
day_range  = []
run_type_1_only      = true
quality_current_only = true
min_completeness_pct = 80.0
min_stops            = 50
exclude_first_year_observers = false
observer_cohorts     = []
max_cars_per_stop    = ""             # empty = no filter
max_car_total        = ""

[species]
aous         = []
common_names = []
families     = []
orders       = []
combine      = "union"                # how selector groups combine: union | intersection  (DECISION)
exclude_migrant_nonbreeders = true

[guilds]
file      = "guilds/project_a.csv"    # swappable per project
match_on  = "AOU"                     # AOU | common_name | scientific_name
unmatched = "report"                  # report | drop | error
[guilds.filters]                      # any column in the guild file; OR within a trait, AND across traits
# habitat = ["forest"]
# nest    = ["ground", "shrub"]

[processing]
zero_fill         = true
stop_range        = []                # e.g. [1, 25]; empty = all 50
community_metrics = false

[joins]                               # [] = don't join; ["*"] = all columns; or list columns
routes   = ["RouteName", "BCR", "Stratum", "RouteTypeID", "Latitude", "Longitude"]
weather  = ["Month", "Day", "ObsN", "StartTime", "EndTime"]
vehicle  = []
observer = []
species  = ["English_Common_Name", "Genus", "Species", "Family", "Order"]
guilds   = ["*"]

[output]
path       = "out/project_a.parquet"
format     = ""                       # inferred from extension if empty
shape      = "wide"                   # wide | long
crs        = "EPSG:4326"
layer_name = "bbs_observations"
drop_columns = []
manifest   = true                     # writes <output>.manifest.json: resolved config, versions, row counts per stage
```

---

## 3. Phases

**Fast path to usable data for the final projects: Phases 0–5.** Phases 6–8 are cleanup and UI, done afterwards. Each phase gets its own branch and PR (Kyle's repos use branch protection, so changes land through PRs he approves). Run `ruff check`, `ruff format --check`, and `pytest` before every PR.

### Phase 0 — Unlock the repo (do this first, small PR)
- Rewrite `AGENTS.md`: drop "Track B additive", the immutable file list, the sequence lock, and the zero-disk mandate. Keep string-typed ingestion, the zero-fill/NULL-beyond-TotalStops rules, the no-silent-except rule, the ruff gates, and "halt and ask on ambiguity."
- Point `CLAUDE.md` to the new `AGENTS.md`. Mark `docs/01–06` as historical, or move them to `docs/archive/`.
- Add `[project.scripts] bbs = "bbs_pipeline.cli:main"` and move pytest/requests-mock to dev deps.

### Phase 1 — Strip stop segmentation / linear referencing
Delete:
- `processing/linear_referencing.py`, `tests/test_linear_referencing.py`
- `ingestion/stops.py`, `tests/test_stops_ingestion.py` (verify there are no other importers first)
- In `cli.py`: `--include-stops`, `--stop-geometry`, the `--shape <shapefile>` overload, and the whole interpolate → unpivot → stop-join block in Step 7
- `spatial/filtering.py` stop-mask modes (`ANY_STOP_INTERSECT`, `SPATIAL_STOP_MASK`, `STRICT_CONTAINMENT` on stops) and their tests
- The `export_vector_layers` routes+stops two-layer export, if nothing else uses it

Replace them with **route-level** spatial selection in `stages/routes.py`:
- `start_point_within`: the routes.csv start coordinate (NAD83, EPSG:4269) falls inside the boundary.
- `polyline_intersects` / `polyline_within`: these use Kyle's polylines, reprojected to the boundary CRS. Report routes with no matching polyline; don't drop them silently.
- **DECISION:** which method is the default, and whether the polyline methods are needed at all given the polyline quality.

Keep `--stop-range` slicing. It is tabular (Stop1..Stop50 columns), not geometric.

### Phase 2 — Local raw-data cache
- `bbs fetch` downloads the release files once (routes, weather, SpeciesList, MigrantNonBreeder, 50-StopData, VehicleData, region codes) to a directory and writes a small `release.json` (item id, file names, sizes, download date).
- Loaders read from the cache. The network is used only if the cache is missing and the user didn't pass `--offline`.
- Replace the hardcoded state tables with the release's region-codes file (B3). Add a test asserting that every StateNum is unique per CountryNum.

### Phase 3 — Run profile config
- `config.py`: Pydantic models matching §2.2. Validation errors must name the bad key, and unknown keys are errors (`extra="forbid"`).
- TOML via `tomllib`. This needs Python ≥3.11, so bump `requires-python`, or add `tomli` if Kyle wants to stay on 3.10 (**DECISION**).
- `--set dotted.key=value` overrides for quick variations without editing the file.
- Ship `examples/` with two starter profiles: Kyle fills in the values for his two final projects (**DECISION**: Claude Code should not guess the study design).
- `bbs inspect` prints the row/route/species count after each stage plus the exact output column list. This is the main feedback loop.

### Phase 4 — Pluggable guilds file
- Format: CSV with one key column plus any number of trait columns. **DECISION:** whether JSON should still be accepted too.
- `match_on` supports `AOU`, `common_name`, and `scientific_name` (Genus + Species from SpeciesList). The guild source (e.g. Partners in Flight exports) may not carry BBS AOU numbers, so this matters.
- Matching is case- and whitespace-insensitive. Unmatched rows on either side (guild rows with no BBS species, and target species with no guild row) are listed in `bbs inspect` and in the manifest.
- Filters: OR within a trait, AND across traits. Any trait column is filterable, with no trait names in code. Delete `known_bh` / `known_fg` and `--breeding-habitat` / `--foraging-guild` (B4).
- Convert the current `data/guilds.json` to `examples/guilds_example.csv` so it serves as a format reference only. It is not Kyle's guild scheme.

### Phase 5 — Joins, column selection, and the two bugs
- `stages/enrich.py` applies each `[joins]` entry as a left join on the correct key. Routes join on `RouteKey`. Weather is already on the zero-fill grid, so select or trim its columns there. Vehicle joins on `RouteDataID`. Species and guilds join on `AOU`. Observer covariates go on the run rows.
- Validate requested column names against the source table and fail with the list of valid names.
- Assert there is **no row multiplication**: the row count must be the same before and after each join (raise if not).
- Fixes **B1**: routes are joinable for every format, not only spatial ones.
- Fixes **B2**: the non-zero-fill path must inner-join observations to the surviving runs (`RouteDataID`), so every run filter applies to both paths.
- Fixes **B5**: no silent except.
- Spatial formats (gpkg/geojson) build geometry from the joined Lat/Lon, then reproject to `output.crs`.
- Manifest sidecar: the resolved config, package version + git SHA, release item id, per-stage counts, and unmatched guild species.

### Phase 6 — Break up `cli.py` (after the fast path works)
- Move the logic from `run_pipeline` into `stages/*` without changing behaviour. A golden-output test (Phase 7) must pass before and after.
- `cli.py` should end up under ~200 lines.

### Phase 7 — Tests that would have caught this
- A **column-contract test**: given a profile, assert the exact output column set.
- A **filter-propagation test**: a run excluded by RunType must not appear in either zero-fill or raw output.
- A **join-cardinality test** for each join.
- A **guild matching test** covering AOU/name/scientific-name keys and the unmatched report.
- A **golden test** on a small real slice (e.g. 2–3 NC routes, a few years) committed under `tests/fixtures/real/`. **DECISION:** Kyle picks the slice.

### Phase 8 — One UI (optional, after the projects are served)
- Delete `gui.py`. Rebuild `ui/app.py` as a **profile editor**: multi-selects populated from `bbs values` (states, BCRs, strata, routes, families, orders, every guild trait), join and column pickers per table, a live `inspect` panel, a "Save profile" button, and a "Run" button. It calls `pipeline.run(config)` and nothing else.

---

## 4. Definition of done (fast path)
1. `bbs fetch` once, then `bbs run --config project_a.toml` produces the file with no network access.
2. The output contains the route attributes Kyle selected, in every format.
3. Swapping `guilds.file` and `guilds.filters` changes the species set with no code edits.
4. Every filter and join is a list in the profile, and `bbs inspect` shows its effect.
5. No stop-interpolation code remains (`grep -ri "linear_ref\|interpolat" src/` returns nothing).
6. ruff is clean and the tests are green, including the new contract tests.

## 5. Open decisions for Kyle (collect before or during Phase 3)
1. Spatial selection: the default method, and whether polyline methods are needed at all.
2. Guild file format and match key, and what PIF/other source each project's guilds come from.
3. Species selector combination: union vs intersection.
4. Python 3.11+ (tomllib) vs keeping 3.10.
5. Default output shape and format per project.
6. The real-data slice for the golden test.
7. The values for the two project profiles (states/buffer boundary, years, species/guilds, joins).
