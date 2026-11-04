# BBS Pipeline v3 — Refactor Plan to a Config-Driven Extraction Tool

**Adapted from `BBS_REFACTOR_PLAN.md`** (which was written against v2, `01_spike/v2_bbs_etl` @ `2c88d70`) to the v3 clean-room code in this folder. Kyle is the author and makes the decisions. Wherever this plan says **DECISION**, stop and ask him. Do not pick a value yourself.

The goals are unchanged from the original: one run-profile file per project, a local raw-data cache, a pluggable guild file, configurable joins and columns, and tests that check what the output actually contains. What changes is the starting point. v3 already fixed some of v2's problems and introduced new ones.

---

## 0. Starting state (verified 2026-11-04)

- `BBS_pipeline/` is **not a git repository**. There is no history, no remote, and no branch protection. The original plan's "one branch + PR per phase" workflow needs a repo first (see Phase 0).
- There are 17 Python files (~2,700 lines in `src/`, plus a 622-line `ui/app.py`). `uv run pytest -q`: **23 passed**, all on synthetic data. `ruff check`: clean. `ruff format --check`: **8 files would be reformatted**.
- There is no `AGENTS.md` or `CLAUDE.md`.
- `requires-python = ">=3.11"`, so `tomllib` is available. This settles open decision 4 of the original plan.

### Current layout
```
src/bbs_pipeline/
  contracts.py                 # 3 frozen Pydantic contracts: guilds.json, protocol.json, spatial.json
  pipeline.py                  # run_bbs_pipeline(bundle): ingest → engine → export (53 lines)
  cli.py                       # --config-dir, --validate-only (63 lines)
  ingest/gate.py               # three-tier ingress: parquet cache → local raw → ScienceBase stream
  ingest/role_resolver.py      # detects table role from column signatures; builds route_id/AOU keys
  ingest/species_normalizer.py # fixed-width SpeciesList.txt parser
  engine/ecology.py            # 300-line run_ecological_engine(): tenure, filters, continuity,
                               #   species, 10/50-stop harmonization, zero-fill, COVID, rollup, joins
  spatial/linear_referencing.py# route geometry, stop/bin linear referencing, STRtree overlays
  spatial/exporter.py          # parquet/csv/duckdb + geoparquet/gpkg/fgb/geojson × N CRSs
ui/app.py                      # Streamlit editor for the 3 JSON files + Run button
config/{guilds,protocol,spatial}.json
```

---

## 1. Assessment of v3

### 1.1 How the original plan's findings map to v3
| v2 item | Status in v3 |
|---|---|
| S1 `AGENTS.md` blocks the refactor | **Gone.** No agent directives exist. Write a fresh, short `AGENTS.md` (Phase 0). |
| S2 1,763-line `cli.py` | **Gone.** `cli.py` is thin. The monolith moved to `engine/ecology.py::run_ecological_engine` (~300 lines, one function doing nine steps). |
| S3 two Streamlit apps | **Gone.** One app. |
| S4 zero-disk re-streaming | **Mostly fixed.** Tier 1 reads a parquet cache. Remaining gaps: no release metadata (item id/version) in the cache, Tier 3 downloads *every* file in the ScienceBase item, and tiers fall back silently. |
| S5 dead/duplicated code | **New dead weight.** Fabricated geometry in `linear_referencing.py`, plus features nobody has asked for yet: 10-stop/hybrid harmonization, COVID 2020 imputation, rolling means, guild rollup, DuckDB export, the `intake_engine` handshake, and the AGOL fallback URL. **DECISION** on what to keep. |
| S6 `pyproject.toml` | **Mostly fixed.** Dev deps are already optional. The entry point is `bbs-pipeline`, where the plan uses `bbs`. |
| S7 tests only check mocks | **Still true.** 23 synthetic tests. None assert the output column set, and none check that a run-level filter keeps an excluded run's counts out. |
| B1 routes.csv attrs missing from tabular output | **Partly fixed.** Routes now join for every format, but the column list is hardcoded (`ROUTES_JOIN_COLS`), `RouteTypeID` is missing, and BCR is a separate "dimension". |
| B2 `--no-zero-fill` leaks excluded runs | **Worse. Now affects both paths. See V1.** |
| B3 wrong hand-typed state table | **Changed.** There's no table now. States are numeric `StateNum` only, and `route_id` = `SS_RRR` drops `CountryNum`. See V3. |
| B4 hardcoded guild dimensions | **Changed shape, same problem.** See V4. |
| B5 silent `except` | **More instances.** See V5. |

### 1.2 Correctness problems in v3 (fix whatever else changes)
| # | Problem | Where |
|---|---|---|
| **V1** | **Excluded runs are summed into counts. Confirmed by probe.** Observations are aggregated by `(route_id, Year, AOU)`, which sums every run (RPID) in that route-year. They are then matched to surviving events on `(route_id, Year)`, not `RouteDataID`. Probe: one route-year with a RunType 1 run (Stop1=3) and a RunType 0 run (Stop1=100) outputs `route_total = 103`, labelled `RunType 1`. This happens with zero-fill on *and* off. The weather join then picks an arbitrary run per route-year (`unique(keep="first")`). | `ecology.py` `harmonize_observations`, Step 7, Step 9c |
| **V2** | **Raw columns are type-inferred, not read as strings.** `pl.read_csv(infer_schema_length=10000)` is used in staging and sanitizing. Leading zeros and mixed-type columns depend on whatever the first 10k rows look like. | `gate.py` `_stage_and_consolidate_files`, `_builtin_polars_sanitize` |
| **V3** | **Region identity is incomplete.** `states` accepts only integers. Nothing reads the release's region-codes file. `route_id` omits `CountryNum`. Check against the region-codes file whether any StateNum repeats across countries; if one does, two routes would collide on `route_id`. | `contracts.py`, `normalize_canonical_keys`, `role_resolver.normalize_lazy_table` |
| **V4** | **Guilds are a fixed schema with surprising precedence.** `GuildProfileContract` has fixed fields (`aou_codes`, `english_names`, `families`, `orders`). `filter_target_species` uses an `if/elif` chain, so setting `aou_codes` silently ignores `families`, and families/orders are ORed. There are no trait columns, no external file, no unmatched report, and no guild attributes in the output. `config/guilds.json` contains illustrative lists, not Kyle's schemes. | `contracts.py`, `ecology.py::filter_target_species` |
| **V5** | **Silent fallbacks.** (a) Tier 3 catches any exception and falls back to an older cache or raw dir with a log warning. (b) CSVs that fail to scan are skipped with `except Exception: continue`. (c) A malformed manifest is `pass`ed. (d) An `intake_engine` failure falls back to the built-in sanitizer. (e) Missing route coordinates become **(0, 0)**, and missing polylines become **fabricated 0.36° diagonal lines**. (f) A missing overlay file produces an all-`None` column. | `gate.py`, `role_resolver.py`, `linear_referencing.py` |
| **V6** | **Wrong CRS label.** routes.csv Lat/Lon are NAD83 (EPSG:4269), but the GeoDataFrame is built as EPSG:4326 with no transform. | `linear_referencing.py::build_spatial_geodataframe` |
| **V7** | **`golden_protocol_preset` silently overwrites user values.** It defaults to `true` and replaces any `run_types`/`quality_current_ids` the user set, before validation. | `contracts.py::_apply_golden_preset` |
| **V8** | **Zero-fill semantics differ from v2.** v3 cross-joins *every* target species with every surviving route-year. v2 only zero-filled species confirmed on that route. Missing `StopN` columns become 0 instead of NULL, and there is no NULL-beyond-TotalStops rule. Fixed in Phase 1 per D6. | `ecology.py` Step 7, `_prepare_50_stop_frame` |
| V9 | Geometry is built per observation row. A zero-filled matrix × N CRSs × 4 vector formats is large. Geometry belongs on routes, and output should join it. | `exporter.py`, `linear_referencing.py` |

### 1.3 What is worth keeping
- Table role detection by column signature (`identify_role_from_columns`). File names in BBS releases do drift.
- The fixed-width `SpeciesList.txt` parser and its tests.
- The Tier 1 parquet cache idea and the nested-zip unpacking.
- The Step 4 predicate set, and the continuity ratio with the COVID-gap denominator (`expected_active_years`).
- Observer tenure flags (`is_first_year`, `tenure_tier`). These must be keyed on runs once V1 is fixed.
- `extra="forbid"` Pydantic models with validators. Reuse this pattern for the profile.
- The multi-format, multi-CRS exporter (once geometry is route-level and correctly labelled).

---

## 2. Target design

Same as the original plan §2: one **run profile** (TOML) per project, consumed by the CLI and produced by the UI.

```
bbs fetch   --release <item_id> --dest ~/data/bbs/<year>
bbs inspect --config project.toml
bbs run     --config project.toml [--set runs.start_year=2000 ...]
bbs values  routes.BCR | species.Family | guilds.<trait>
```

**DECISION D1:** Should the three JSON contracts (`guilds.json`/`protocol.json`/`spatial.json`) be replaced by one TOML profile, as the original plan wants? The alternative is to keep them as the internal model, with TOML as a front end. Recommendation: one profile. Three files make "what produced this output?" harder to answer, and the guild *scheme* moves to its own CSV anyway (Phase 5).

### 2.1 Target module layout and where v3 code goes
```
src/bbs_pipeline/
  config.py            ← contracts.py (profile models, TOML load, --set overrides)
  sources/             ← ingest/ (fetch + release.json, string-typed loaders, role detection,
                          species parser, region codes, MigrantNonBreeder, VehicleData)
  stages/
    routes.py          ← ecology Step 4 route predicates + new route-level spatial selection
    runs.py            ← ecology Steps 3–4 (tenure, RunType/Quality, years, continuity), keyed on RouteDataID
    species.py         ← filter_target_species + lumping + migrant exclusion
    observations.py    ← harmonize_observations + zero-fill (Step 7), joined on RouteDataID
    enrich.py          ← ecology Step 9c, configurable per [joins]
    output.py          ← spatial/exporter.py + route-level geometry
  guilds.py            (new) pluggable guild file loader + matcher
  pipeline.py          ← keep; calls stages in order
  cli.py               ← fetch/inspect/run/values subcommands
ui/app.py              ← becomes a profile editor (last phase)
```
The run-profile sketch is the same as in original plan §2.2. Final field names are a **DECISION** to settle in Phase 4.

---

## 3. Phases

Each phase gets its own commit or PR (see D2). Run `ruff check`, `ruff format --check`, and `pytest` before each. **Fast path to usable data: Phases 0–6.**

### Phase 0 — Repo hygiene (small)
- `git init`, `.gitignore` (`.venv`, caches, `data/`, `output/`, `.coverage`). **D2 (decided):** v3 replaces `kewhiteh/BBS-pipeline`, with one PR per phase. The Phase 0 PR replaces the tree on top of the existing `main` history (no force-push). The agent account `kewhitehagent` currently has READ access, so PRs come from a fork unless Kyle grants write.
- Run `ruff format` (8 files), as its own commit.
- Write a short `AGENTS.md`: string-typed ingestion, the zero-fill rules (per D6), no silent `except`, no fabricated values (no (0,0), no placeholder geometry), the ruff gates, and "halt and ask on ambiguity". `CLAUDE.md` points to it.
- Rename the script to `bbs` (keep `bbs-pipeline` as an alias if Kyle wants).

### Phase 1 — Correctness fixes in place (before any restructuring)
These corrupt numbers today, so they come before the refactor. Write a failing test first for each.
- **V1:** Carry `RouteDataID` (plus `RPID`) through. Aggregate observations per run, not per route-year. Inner-join observations to *surviving runs* on `RouteDataID` in both the zero-fill and raw paths. Join weather on `RouteDataID`. Test: the probe scenario above must yield `route_total = 3`. **D3 (resolved by data):** In the v2 copy's `Weather.csv`, 103 of 143,992 route-years have more than one run (RPIDs 102/103/104/203/501/502), and **none** has more than one RunType 1 run. Add a guard that raises if more than one run survives for a route-year, rather than adding a rule.
- **D6 (decided):** Zero-fill each route's *historical species pool*: every species ever recorded on that route in the full BBS record, not just the selected years. Zeros are filled for all surviving runs, so extirpations show up as zeros. Species never recorded on a route get no rows there. Stops beyond `TotalStops` stay NULL (v2 rule). Test: a species last seen on a route before `start_year` still gets zero rows inside the window.
- **V5:** Remove the silent fallbacks. Tier failures raise unless the user explicitly chose `auto`. In that case, the tier used and the reason go into the manifest. Unreadable CSVs and malformed manifests raise. Missing coordinates or polylines get reported and get NULL geometry, never (0,0) or fabricated lines.
- **V6:** Build start points as EPSG:4269, then `to_crs` the target.
- **V7:** Drop `golden_protocol_preset` and make the defaults `[1]`/`[1]`, or make the preset an explicit choice that errors if the user sets conflicting values. Recommendation: drop it.

### Phase 2 — Cut fabricated and unused code
- Delete stop linear referencing (`_apply_stop_linear_referencing`, `_ensure_linestring`, `enable_stop_linear_referencing`, the `0.36°` placeholders) and its test. Keep `stop_range` / bin columns as tabular data.
- Replace `custom_overlay_paths` (which annotates output) with **route-level spatial selection** in `stages/routes.py`: `start_point_within` (NAD83 start point inside the boundary) or `polyline_intersects`/`polyline_within`. Report routes with no polyline. **DECISION D4:** the default method, and whether polylines are needed at all. **DECISION D5:** which of these to keep: 10-stop/hybrid harmonization, COVID `substitute_2019`/`interpolate_2019_2021`, rolling mean, guild rollup, DuckDB export, the `intake_engine` handshake, the AGOL fallback. Each one kept needs a test on real data in Phase 8.

### Phase 3 — Raw-data cache with provenance
- Add `bbs fetch`: download only the needed release files (routes, weather, SpeciesList, MigrantNonBreeder, 50-StopData, VehicleData, region codes) once. Write `release.json` (item id, DOI, file names, sizes, sha256, download date). Then build the parquet cache from the downloaded files.
- **V2:** Read all raw CSVs with `infer_schema=False` (all strings), and cast in one place with explicit dtypes.
- **V3:** Load the region-codes file. Accept a state abbreviation, name, or StateNum in `routes.states`. Add a test that `(CountryNum, StateNum)` is unique, and decide from the file whether `route_id` must include `CountryNum`.
- Add `--offline`, which fails if the cache is missing rather than fetching.
- Rename `IngressMode.LOCAL_RAW_VIA_INTAKE` (the intake_engine hook was removed in Phase 2).
- **Done (findings):**
  - No recent release ships a region-codes file. The StateNum code list is in the release metadata XML (the `StateNum` attribute domain), so codes come from there. Postal abbreviations map to names, never to numbers.
  - No StateNum repeats across countries in the 2026 release, so `route_id` = `SS_RRR` stays.
  - A DOI resolves through doi.org (ScienceBase search does not index DOIs). The ingress modes were replaced by `offline`.
  - Two more bugs fixed: `Migrants.csv`/`MigrantSummary.csv` were merged into breeding counts by column-signature matching, and the 10-stop path used `StopTotal` (stops with a detection) as abundance instead of `SpeciesTotal`.
- **intake_engine:** Kyle's separate, in-progress package for cleaning messy unknown databases. Once its API is stable, give it an explicit, opt-in integration point in `sources/` (the old hook called `assess`/`execute_pipeline`, which did not exist yet). Until then, the built-in sanitizer is the only path.

### Phase 4 — Run profile config
- `config.py`: Pydantic models following original plan §2.2, built with `extra="forbid"`, and validation errors name the bad key. Port the useful v3 validators (AOU padding, year range, EPSG normalization).
- TOML via `tomllib`. `--set dotted.key=value` overrides.
- `bbs inspect` prints row/route/species counts after every stage (extending `FunnelAuditMetrics`) and the exact output column list.
- `examples/`: two starter profiles. **DECISION D9:** Kyle supplies the study-design values.
- UI: keep it working by having it read and write the profile (a minimal change; the full rebuild is in Phase 9).

### Phase 5 — Pluggable guild file (replaces `GuildProfileContract`)
- CSV with one key column plus any number of trait columns. **DECISION D7:** JSON too? Which source (PIF etc.) per project?
- `match_on`: `AOU` | `common_name` | `scientific_name`. Matching is case- and whitespace-insensitive. Unmatched rows on both sides go to `inspect` and the manifest.
- Filters: OR within a trait, AND across traits, over any column. Species selectors (`aous`, `common_names`, `families`, `orders`) combine per **DECISION D8** (union vs intersection). This replaces the `if/elif` precedence (V4).
- Migrant/non-breeder exclusion from `MigrantNonBreeder`.
- Convert the old `config/guilds.json` (removed in Phase 4; see git history) into `examples/guilds_example.csv` as a format reference only.

- **Done:** `bbs_pipeline/guilds.py`, the `[guilds]` profile section, `joins.tables = ["guilds"]`, `docs/guild_file.md`, `examples/guilds_example.csv`. Guild filters combine with the `[species]` lists by AND (D12, flagged to Kyle). Migrant non-breeders are already excluded: since Phase 3 they are separate tables that never enter the observations.

### Phase 6 — Joins, columns, manifest
- `[joins]` per table (routes, weather, vehicle, observer, species, guilds): `[]` | `["*"]` | an explicit list. Validate names against the source table and fail with the list of valid names. Replaces `JoinDimension`, `*_JOIN_COLS`, and the separate BCR dimension (B1).
- Assert row count is unchanged across every join.
- Geometry is built once per route (V9) and joined at export.
- Manifest sidecar: resolved profile, package version + git SHA, `release.json` contents, per-stage counts, ingress tier used, and unmatched guild species.

- **Done:** `[joins]` has one list per table (routes, weather, observer, vehicle, species, guilds) in `engine/joins.py`; unknown columns raise with the valid list; a right side with duplicate keys raises, and the engine asserts the row count is unchanged after all joins. `vehicle` adds derived `CarTotal`/`NoiseTotal`. The wide pivot takes its index from the joined route/species columns. Geometry is built once per route and joined onto the rows (V9, done with Phase 9).

### Phase 7 — Split `run_ecological_engine` into `stages/*`
- Move code into `stages/` without changing behaviour. The golden test must pass before and after. The goal is `pipeline.py` ≈ 100 lines that just call the stages in order.

- **Done:** `bbs_pipeline/stages/` holds `common`, `routes`, `runs`, `species`, `observations`, and `enrich`; `engine/ecology.py` (142 lines) only runs them in order. Golden outputs (Phase 8's real-data slice, added first) are byte-identical before and after, as are two larger real-data runs (NC hybrid wide with guilds; BCRs 27-29 long, 421,623 rows).

### Phase 8 — Tests that would have caught V1
- **Column-contract test:** profile → exact output column set.
- **Filter-propagation test:** a run excluded by RunType or by tenure must not contribute counts or rows, with zero-fill on and off. This is the generalized V1 probe.
- **Join-cardinality test** for each join.
- **Guild matching test** on AOU, common name, and scientific name, plus the unmatched report.
- **Golden test** on a small real slice under `tests/fixtures/real/`. **DECISION D10:** Kyle picks the slice.

### Phase 9 — UI as profile editor (optional)
- `ui/app.py` edits one profile: multi-selects fed by `bbs values`, join/column pickers, a live `inspect` panel, Save, and Run → `pipeline.run(config)` only.

---

- **Phase 9 done:** `bbs values FIELD` and `bbs_pipeline/values.py` list valid values from the release; `ui/app.py` is a profile editor with pick lists, per-table column pickers, guild trait filters, a draft with explicit Save, and an Inspect panel (optional live updates, cached per profile). It calls only `pipeline.run_profile`.

## 4. Definition of done (fast path)
1. `bbs fetch` once, then `bbs run --config project_a.toml` produces output with no network access.
2. An excluded run never contributes to any count (filter-propagation test passes on real data).
3. The output contains the route attributes Kyle selected, in every format, with correctly labelled CRS.
4. Swapping `guilds.file`/`guilds.filters` changes the species set with no code edits.
5. Every filter and join is a list in the profile, and `bbs inspect` shows its effect.
6. `grep -rn "0\.36\|line_interpolate\|substring(" src/` returns nothing, and no code path invents coordinates.
7. ruff is clean and the tests are green, including the new contract tests.

## 5. Open decisions for Kyle
| # | Decision | Needed by |
|---|---|---|
| D1 | ~~TOML profile~~ **Decided:** one TOML profile replaces the three JSON files; layout approved (source, routes, runs, observations, species, joins, output); one output file per run, format from the extension | Phase 4 |
| D2 | ~~Git remote~~ **Decided:** replace `kewhiteh/BBS-pipeline`, one PR per phase | Phase 0 |
| D3 | ~~Multiple runs per route-year~~ **Resolved by data:** guard that raises | Phase 1 |
| D4 | ~~Spatial selection~~ **Decided:** route start points only; no polylines | Phase 2 |
| D5 | ~~v3 extras~~ **Decided:** keep 10-stop/hybrid (pre-1997 years are needed) and the 2020 exclusion; drop COVID imputation, rolling mean, guild rollup, DuckDB, AGOL. intake_engine is Kyle's in-progress project: hook removed for now (see Phase 3) | Phase 2 |
| D6 | ~~Zero-fill semantics~~ **Decided:** historical species pool per route; NULL beyond TotalStops | Phase 1 |
| D7 | ~~Guild file~~ **Decided:** CSV only; one row per species with any of aou / common_name / scientific_name plus any trait columns; match AOU, then scientific, then common name; conflicting identifiers raise; unmatched rows reported. Ship a sample CSV with instructions. Source for GHY 5812: BBS breeding-habitat groups, woodland split by Freemark & Collins (1992) and Whitcomb et al. (1981) | Phase 5 |
| D8 | ~~Selector combination~~ **Decided:** union (any selector matches); a selector value that matches no species raises | Phase 4 |
| D11 | ~~Minimum stops per run~~ **Decided:** use BBS's own criterion. QualityCurrentID = 1 (part of RunType = 1) requires >= 45 of 50 stops completed; the release has no per-run stop count, so there is no separate stop filter. "80% completion" means `runs.min_continuity = 0.8` | Phase 4 follow-up |
| D9 | ~~Project profiles~~ **Decided:** `examples/wood_thrush_story_map.toml` (12 NC routes, 1995-2025, no quality filters, wide CSV) and `examples/ghy5812_guilds.toml` (BCRs 27-29, RunType 1, continuity 0.8, years open, long CSV). Output shape long/wide and an N-year rolling mean are profile options | Phase 4 |
| D13 | ~~Subspecies lumping~~ **Decided:** derive from the release's scientific names (forms, within-species hybrids, and within-species "unid." entries count under the species-level entry); replaces the 7-entry hand-typed table, which missed 18 of 28 form/hybrid entries | Phase 5 follow-up |
| D12 | Guild filters AND the `[species]` lists (chosen in Phase 5, flagged to Kyle; change if he prefers otherwise) | Phase 5 |
| D10 | ~~Golden slice~~ **Decided:** the 12 story-map NC routes, all years in the fixture; profiles use 2015-2024 with RunType 1, QualityCurrentID 1, and min_continuity 0.8 | Phase 7/8 |

Settled: Python ≥ 3.11, so `tomllib` (original decision 4).
