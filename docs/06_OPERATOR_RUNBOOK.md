# Operator Steering Runbook: Phased XML Prompt Deck (v2.0)
## Track B Step-by-Step Implementation Guide

This runbook provides the exact, copy-pasteable XML prompts for steering autonomous agents through the six phases of USGS Breeding Bird Survey (BBS) Pipeline v2.0.

---

## Phase 1: In-Memory 50-Stop Ingestion Engine

```xml
<context>
You are executing Phase 1 of the Track B Additive Upgrade for USGS Breeding Bird Survey (BBS) Pipeline v2.0.
Before authoring code:
1. Verify baseline tag is intact: git rev-parse --verify v1.0.0
2. Verify active branch: git checkout -b feature/phase-1-stops-ingestion
3. Confirm clean working tree: git status
4. Review immutable baseline files in docs/05_CONSTRAINTS.md:
   - src/bbs_pipeline/client/parser.py
   - src/bbs_pipeline/client/sciencebase.py
   - src/bbs_pipeline/core/discovery.py
   - src/bbs_pipeline/core/zero_fill.py
   - src/bbs_pipeline/ingestion/routes.py
   - src/bbs_pipeline/processing/aggregation.py
   - tests/test_routes_ingestion.py
   - tests/test_aggregations.py
   Do NOT touch or modify these files under any circumstances.
</context>

<mandate>
Implement the in-memory 50-stop ingestion engine in src/bbs_pipeline/ingestion/stops.py and corresponding tests in tests/test_stops_ingestion.py.
Adhere strictly to:
- Zero-disk in-memory streaming via io.BytesIO.
- Ingestion boundary isolation: infer_schema_length=0, all columns read natively as pl.String.
- Immediate header trimming: df.rename({c: c.strip() for c in df.columns}).
- Exhaustive null tokens: ["", "NA", "null", "NULL", "*", "None"].
- Deferred arithmetic casting: Stop1..Stop50 cast to pl.Int32 with strict=False downstream.
- Scope-restricted zero-filling: 0 for stops 1..TotalStops; native NULL for stops > TotalStops.
- Tool lock: Polars LazyFrame/DataFrame exclusively. Zero Pandas.
</mandate>

<tasks>
1. Add synthetic in-memory zip fixtures in tests/conftest.py simulating 50-StopData.zip with fifty1.csv..fifty10.csv, varying TotalStops (45, 50), and ragged rows.
2. Author tests/test_stops_ingestion.py covering:
   - String dtype assertions at parser boundary.
   - Header whitespace stripping.
   - Null token parsing.
   - Deferred pl.Int32 casting.
   - TotalStops null preservation (stops > TotalStops remain NULL).
   - Negative Assertion 1: Reject corrupt/non-zip stream with descriptive domain error.
   - Negative Assertion 2: Reject missing mandatory headers with descriptive domain error.
   - Negative Assertion 3: Assert unconducted stops beyond TotalStops are NOT zero-filled.
3. Implement src/bbs_pipeline/ingestion/stops.py:
   - read_stops_data(stream: io.BytesIO) -> pl.LazyFrame
   - Composite key construction (RouteKey, StopKey).
</tasks>

<execution>
Execute tasks sequentially:
1. Implement test fixtures and test cases.
2. Implement ingestion logic.
3. Run verification gates:
   - ruff check src/ tests/
   - ruff format --check src/ tests/
   - pytest (Assert 100% pass across baseline tests AND test_stops_ingestion.py)
   - git status (Ensure clean working tree)
4. Record atomic Conventional Commit on feature branch:
   git add src/ tests/
   git commit -m "feat(ingestion): implement in-memory 50-stop ingestion engine with deferred typing"
5. Merge to main:
   git checkout main && git merge --ff-only feature/phase-1-stops-ingestion
6. Update docs/04_TASKS.md marking Phase 1 complete.
7. STOP. Do not proceed to Phase 2.
</execution>
```

---

## Phase 2: High-Precision Linear Referencing Engine

```xml
<context>
You are executing Phase 2 of the Track B Additive Upgrade for USGS Breeding Bird Survey (BBS) Pipeline v2.0.
Before authoring code:
1. Verify active branch: git checkout -b feature/phase-2-linear-referencing
2. Confirm clean working tree: git status
3. Review spatial invariants in docs/01_PLAYBOOK.md and schemas in docs/03_SCHEMAS.md:
   - 24.5-mile route, 50 stops, 0.5-mile monotonic increments.
   - Conformal projection via EPSG:5070; output in EPSG:4326.
   - Fallback origin bearing generation when geometry is missing.
</context>

<mandate>
Implement the 50-stop linear referencing engine in src/bbs_pipeline/processing/linear_referencing.py and tests in tests/test_linear_referencing.py.
Adhere strictly to:
- Tool lock: Shapely >=2.0.0 and PyProj >=3.6.0 exclusively.
- Monotonic distance progression checks.
- GeometrySource categorization ("route_vector_interpolated" vs "origin_linear_fallback").
- Blast-radius lock: Baseline modules remain strictly untouched.
</mandate>

<tasks>
1. Author tests/test_linear_referencing.py covering:
   - Interpolation of exactly 50 points along a known synthetic LineString.
   - Stop 1 at 0.0 mi, Stop 50 at 24.5 mi.
   - Monotonic distance validation.
   - Fallback linear bearing generator when polyline is null.
   - Negative Assertion 1: Raise ValueError if polyline length < 24.0 miles.
   - Negative Assertion 2: Raise ValueError on invalid/unprojectable coordinates.
2. Implement src/bbs_pipeline/processing/linear_referencing.py:
   - interpolate_stops_along_route(route_line, origin_lat, origin_lon, crs="EPSG:4269") -> pl.DataFrame
   - Validation and fallback logic.
</tasks>

<execution>
Execute tasks sequentially:
1. Author tests and implementation.
2. Run verification gates:
   - ruff check src/ tests/
   - ruff format --check src/ tests/
   - pytest (100% pass across all baseline and new tests)
   - git status (Clean tree)
3. Record atomic Conventional Commit on feature branch:
   git add src/ tests/
   git commit -m "feat(processing): implement 50-stop linear referencing and interpolation engine"
4. Merge to main:
   git checkout main && git merge --ff-only feature/phase-2-linear-referencing
5. Update docs/04_TASKS.md marking Phase 2 complete.
6. STOP. Do not proceed to Phase 3.
</execution>
```

---

## Phase 3: Two-Tier Granular Spatial Filtering

```xml
<context>
You are executing Phase 3 of the Track B Additive Upgrade for USGS Breeding Bird Survey (BBS) Pipeline v2.0.
Before authoring code:
1. Verify active branch: git checkout -b feature/phase-3-spatial-filtering
2. Confirm clean working tree: git status
3. Review spatial filtering specifications in docs/01_PLAYBOOK.md and docs/02_ARCHITECTURE.md:
   - Tier 1: Route envelope bounding box index via Shapely STRtree.
   - Tier 2: Vectorized stop point-in-polygon checks.
   - Modes: STRICT_CONTAINMENT, ANY_STOP_INTERSECT, SPATIAL_STOP_MASK.
</context>

<mandate>
Implement two-tier spatial filtering in src/bbs_pipeline/spatial/filtering.py and tests in tests/test_spatial_filtering.py.
Adhere strictly to:
- Tool lock: Shapely STRtree and vectorized containment ufuncs.
- In-memory processing of geometry inputs.
- Re-calculation of route-level totals under SPATIAL_STOP_MASK.
</mandate>

<tasks>
1. Author tests/test_spatial_filtering.py covering:
   - Tier 1 bounding box pruning.
   - Tier 2 stop containment.
   - Filter modes: STRICT_CONTAINMENT, ANY_STOP_INTERSECT, SPATIAL_STOP_MASK.
   - Correctness of recomputed totals under masking.
   - Negative Assertion 1: Raise ValueError on invalid/unclosed polygon geometries.
   - Negative Assertion 2: Raise ValueError on invalid spatial filter mode string.
2. Implement src/bbs_pipeline/spatial/filtering.py:
   - build_route_spatial_index(...)
   - filter_stops_by_boundary(stops_lf, polygon, mode) -> pl.LazyFrame
</tasks>

<execution>
Execute tasks sequentially:
1. Author tests and implementation.
2. Run verification gates:
   - ruff check src/ tests/
   - ruff format --check src/ tests/
   - pytest (100% pass across all tests)
   - git status (Clean tree)
3. Record atomic Conventional Commit on feature branch:
   git add src/ tests/
   git commit -m "feat(spatial): implement two-tier STRtree spatial boundary filter"
4. Merge to main:
   git checkout main && git merge --ff-only feature/phase-3-spatial-filtering
5. Update docs/04_TASKS.md marking Phase 3 complete.
6. STOP. Do not proceed to Phase 4.
</execution>
```

---

## Phase 4: In-Memory Multi-Format Vector Export Engine

```xml
<context>
You are executing Phase 4 of the Track B Additive Upgrade for USGS Breeding Bird Survey (BBS) Pipeline v2.0.
Before authoring code:
1. Verify active branch: git checkout -b feature/phase-4-vector-export
2. Confirm clean working tree: git status
3. Review export specifications in docs/02_ARCHITECTURE.md and docs/03_SCHEMAS.md:
   - Formats: OGC GeoPackage (.gpkg), RFC 7946 GeoJSON (.geojson), Apache Parquet (.parquet).
   - Encodings: wide and long.
   - Provenance stamping: pipeline_version="2.0.0", baseline_tag="v1.0.0", git commit hash, timestamp UTC.
</context>

<mandate>
Implement in-memory vector export serialization in src/bbs_pipeline/export/vector.py and tests in tests/test_vector_export.py.
Adhere strictly to:
- Zero-disk in-memory mandate: Write strictly to io.BytesIO buffers.
- Terminal vector boundary: GeoPandas and Pandas usage is restricted strictly to this module for serialization only.
- Strict provenance metadata embedding.
</mandate>

<tasks>
1. Author tests/test_vector_export.py covering:
   - In-memory GPKG export with dual layers (routes and stops).
   - In-memory GeoJSON export.
   - In-memory Parquet export.
   - Wide and Long structure validation.
   - Metadata stamping validation.
   - Negative Assertion 1: Raise ValueError on unsupported export format.
   - Negative Assertion 2: Raise ValueError on missing required spatial coordinates.
2. Implement src/bbs_pipeline/export/vector.py:
   - export_vector_dataset(df, format, shape="long", metadata=None) -> io.BytesIO
</tasks>

<execution>
Execute tasks sequentially:
1. Author tests and implementation.
2. Run verification gates:
   - ruff check src/ tests/
   - ruff format --check src/ tests/
   - pytest (100% pass across all tests)
   - git status (Clean tree)
3. Record atomic Conventional Commit on feature branch:
   git add src/ tests/
   git commit -m "feat(export): implement in-memory multi-format vector export engine"
4. Merge to main:
   git checkout main && git merge --ff-only feature/phase-4-vector-export
5. Update docs/04_TASKS.md marking Phase 4 complete.
6. STOP. Do not proceed to Phase 5.
</execution>
```

---

## Phase 5: CLI Extension & Dual-Interface Orchestration

```xml
<context>
You are executing Phase 5 of the Track B Additive Upgrade for USGS Breeding Bird Survey (BBS) Pipeline v2.0.
Before authoring code:
1. Verify active branch: git checkout -b feature/phase-5-cli-extension
2. Confirm clean working tree: git status
3. Review CLI specifications in docs/02_ARCHITECTURE.md:
   - Additive flags: --include-stops, --stop-geometry, --stop-range, --spatial-boundary, --spatial-filter-mode, --shape.
   - Complete backward compatibility with v1.0.0 CLI options.
</context>

<mandate>
Extend src/bbs_pipeline/cli.py with additive CLI controls and verify with tests in tests/test_cli_additive.py.
Adhere strictly to:
- Backward compatibility: Calling CLI without new flags preserves v1.0.0 behavior.
- Clean exit codes: 0 for success, non-zero for failure; diagnostics to stderr.
- Tool lock: Standard argparse and Polars pipelines.
</mandate>

<tasks>
1. Author tests/test_cli_additive.py covering:
   - Backward-compatibility test: v1.0 CLI command invocations work unchanged.
   - Additive flags invocation with simulated in-memory outputs.
   - Negative Assertion 1: CLI error on invalid stop range (start > end, start < 1, end > 50).
   - Negative Assertion 2: CLI error on non-existent or invalid spatial boundary input.
2. Extend src/bbs_pipeline/cli.py:
   - Additive arguments and dispatch to stops, linear referencing, spatial filtering, and export modules.
</tasks>

<execution>
Execute tasks sequentially:
1. Author tests and implementation.
2. Run verification gates:
   - ruff check src/ tests/
   - ruff format --check src/ tests/
   - pytest (100% pass across all tests)
   - git status (Clean tree)
3. Record atomic Conventional Commit on feature branch:
   git add src/ tests/
   git commit -m "feat(cli): wire additive stop-level and spatial CLI parameters"
4. Merge to main:
   git checkout main && git merge --ff-only feature/phase-5-cli-extension
5. Update docs/04_TASKS.md marking Phase 5 complete.
6. STOP. Do not proceed to Phase 6.
</execution>
```

---

## Phase 6: Reactive Web GUI Linear Spatial Explorer

```xml
<context>
You are executing Phase 6 of the Track B Additive Upgrade for USGS Breeding Bird Survey (BBS) Pipeline v2.0.
Before authoring code:
1. Verify active branch: git checkout -b feature/phase-6-gui-extension
2. Confirm clean working tree: git status
3. Review GUI feature/phase-6-gui-extension
2. Confirm clean working tree: git status
3. Review GUI specifications in docs/02_ARCHITECTURE.md and docs/05_CONSTRAINTS.md:
   - Add dedicated Tab 3: "📍 Stop-Level Linear Spatial View".
   - Sub-route range slider (1..50).
   - Spatial polygon uploader / bbox input.
   - Stop point map viewer color-coded by detection intensity.
   - Direct RAM streaming via st.download_button.
   - Tabs 1 and 2 must remain completely intact.
</context>

<mandate>
Extend src/bbs_pipeline/gui.py with Tab 3 and author integration tests in tests/test_gui_additive.py.
Adhere strictly to:
- Zero-disk streaming: st.download_button reads directly from io.BytesIO.
- Ergonomic controls: st.multiselect with composite labels for route selection.
- Complete regression safety for existing tabs.
</mandate>

<tasks>
1. Author tests/test_gui_additive.py asserting state isolation and session defaults for Tab 3.
2. Extend src/bbs_pipeline/gui.py:
   - Implement Tab 3: "📍 Stop-Level Linear Spatial View".
   - Sub-route slider, spatial filter input, map viewer, and download buttons.
</tasks>

<execution>
Execute tasks sequentially:
1. Author tests and implementation.
2. Run verification gates:
   - ruff check src/ tests/
   - ruff format --check src/ tests/
   - pytest (100% pass across all baseline and additive tests)
   - git status (Clean tree)
3. Record atomic Conventional Commit on feature branch:
   git add src/ tests/
   git commit -m "feat(gui): implement stop-level linear spatial view and in-memory export tab"
4. Merge to main:
   git checkout main && git merge --ff-only feature/phase-6-gui-extension
5. Update docs/04_TASKS.md marking Phase 6 complete.
6. Tag v2.0.0 release:
   git tag -a v2.0.0 -m "release: USGS BBS Pipeline v2.0.0 additive upgrade complete"
7. STOP.
</execution>
```