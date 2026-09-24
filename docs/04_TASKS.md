# Phased Milestone Backlog & Verification Gates (v2.0)
## Track B Additive Implementation Roadmap

This backlog defines the sequential phases for building USGS Breeding Bird Survey (BBS) Pipeline v2.0.
In accordance with Track B invariants, repository initialization, lockfile creation, and `.gitignore` setup are already complete at baseline `v1.0.0`. Implementation starts directly at Phase 1.

---

## Milestone Summary

- [x] **Phase 1: In-Memory 50-Stop Ingestion Engine** (`feature/phase-1-stops-ingestion`) ✅ *Completed 2026-09-23 — commit `d878db7`*
- [x] **Phase 2: High-Precision Linear Referencing Engine** (`feature/phase-2-linear-referencing`) ✅ *Completed 2026-09-23 — commit `0f12a04`*
- [x] **Phase 3: Two-Tier Granular Spatial Filtering** (`feature/phase-3-spatial-filtering`) ✅ *Completed 2026-09-23*
- [x] **Phase 4: In-Memory Multi-Format Vector Export Engine** (`feature/phase-4-vector-export`) ✅ *Completed 2026-09-23*
- [x] **Phase 5: CLI Extension & Dual-Interface Orchestration** (`feature/phase-5-cli-integration`) ✅ *Completed 2026-09-23*
- [x] **Phase 6: Reactive Web GUI Linear Spatial Explorer** (`feature/phase-6-gui-extension`)

---

## Detailed Phase Breakdown

### Phase 1: In-Memory 50-Stop Ingestion Engine ✅ COMPLETE
**Branch:** `feature/phase-1-stops-ingestion`
**Target Modules:**
- `src/bbs_pipeline/ingestion/stops.py`
- `tests/test_stops_ingestion.py`
- `tests/conftest.py` (Additive fixtures only)

**Tasks:**
1. Author synthetic 50-stop test fixtures in `tests/conftest.py` simulating multi-member `50-StopData.zip` (`fifty1.csv` through `fifty10.csv`) with known null tokens, varying `TotalStops` (45 and 50), and ragged rows.
2. Implement `tests/test_stops_ingestion.py` validating:
   - Ingestion with `infer_schema_length=0` asserting all raw columns are `pl.String`.
   - Automatic header whitespace trimming.
   - Comprehensive null token mapping (`["", "NA", "null", "NULL", "*", "None"]`).
   - Deferred casting of `Stop1`..`Stop50` to `pl.Int32` via `strict=False`.
   - Scope-restricted zero-filling for surveyed stops ($1 \dots \text{TotalStops}$).
   - **Negative Assertion 1:** Exception raised when given an invalid/corrupt zip stream.
   - **Negative Assertion 2:** Exception raised or null preservation when stop count columns contain un-parseable corrupt strings under strict evaluation.
   - **Negative Assertion 3:** Verify stops beyond `TotalStops` ($k > \text{TotalStops}$) strictly remain `NULL` and are NOT zero-filled.
3. Implement `src/bbs_pipeline/ingestion/stops.py`:
   - `read_stops_data(stream: io.BytesIO) -> pl.LazyFrame`
   - In-memory extraction of zip members from `io.BytesIO`.
   - Streaming Polars ingestion adhering to string ingestion and deferred casting invariants.
   - Composite key construction (`RouteKey`, `StopKey`).

**Exit Criteria:**
- `ruff check src/ tests/` passes with 0 warnings.
- `ruff format --check src/ tests/` passes.
- Baseline tests (`test_routes_ingestion.py`, `test_aggregations.py`) pass 100%.
- Additive tests (`test_stops_ingestion.py`) pass 100%.
- Working tree clean; atomic commit merged via `git merge --ff-only`.

---

### Phase 2: High-Precision Linear Referencing Engine ✅ COMPLETE
**Branch:** `feature/phase-2-linear-referencing`
**Target Modules:**
- `src/bbs_pipeline/processing/linear_referencing.py`
- `tests/test_linear_referencing.py`

**Tasks:**
1. Implement test suite in `tests/test_linear_referencing.py`:
   - Interpolation of exactly 50 stops at 0.5-mile intervals along a known polyline.
   - Verification that Stop 1 measure is $0.0\text{ mi}$ and Stop 50 measure is $24.5\text{ mi}$.
   - Assertion of monotonic distance progression.
   - Validation of fallback bearing generation when polyline is missing (`GeometrySource = "origin_linear_fallback"`).
   - **Negative Assertion 1:** Raise `ValueError` if route polyline length is less than 24.0 miles (insufficient transect length).
   - **Negative Assertion 2:** Raise `ValueError` if coordinate transformation encounters invalid CRS or unprojectable coordinates.
2. Implement `src/bbs_pipeline/processing/linear_referencing.py`:
   - `interpolate_stops_along_route(route_line: shapely.LineString, origin_lat: float, origin_lon: float, crs: str = "EPSG:4269") -> pl.DataFrame`
   - Geodesic projection to EPSG:5070 via `pyproj.Transformer`.
   - Euclidean interpolation via `line.interpolate(measure_meters)`.
   - Coordinate re-projection to EPSG:4326.
   - Fallback generator for missing geometries.

**Exit Criteria:**
- `ruff check src/ tests/` passes.
- `ruff format --check src/ tests/` passes.
- 100% pass across all baseline and additive tests.
- Atomic commit merged via `git merge --ff-only`.

---

### Phase 3: Two-Tier Granular Spatial Filtering ? COMPLETE
**Branch:** `feature/phase-3-spatial-filtering`
**Target Modules:**
- `src/bbs_pipeline/spatial/filtering.py`
- `tests/test_spatial_filtering.py`

**Tasks:**
1. Implement test suite in `tests/test_spatial_filtering.py`:
   - Tier 1 bounding box pruning via `shapely.STRtree`.
   - Tier 2 point-in-polygon stop containment checks.
   - Testing spatial filter modes: `STRICT_CONTAINMENT`, `ANY_STOP_INTERSECT`, and `SPATIAL_STOP_MASK`.
   - Recomputing route totals when stops are masked.
   - **Negative Assertion 1:** Raise `ValueError` on invalid or unclosed polygon geometries.
   - **Negative Assertion 2:** Raise `ValueError` on unrecognized `spatial_filter_mode`.
2. Implement `src/bbs_pipeline/spatial/filtering.py`:
   - `build_route_spatial_index(routes_with_stops: pl.DataFrame) -> tuple[shapely.STRtree, ...]`
   - `filter_stops_by_boundary(stops_lf: pl.LazyFrame, polygon: shapely.Polygon, mode: str) -> pl.LazyFrame`
   - High-performance vectorized spatial filtering using Shapely 2.0 ufuncs.

**Exit Criteria:**
- `ruff check src/ tests/` passes.
- `ruff format --check src/ tests/` passes.
- 100% test pass rate across all suites.
- Atomic commit merged via `git merge --ff-only`.

---

### Phase 4: In-Memory Multi-Format Vector Export Engine ? COMPLETE
**Branch:** `feature/phase-4-vector-export`
**Target Modules:**
- `src/bbs_pipeline/export/vector.py`
- `tests/test_vector_export.py`

**Tasks:**
1. Implement test suite in `tests/test_vector_export.py`:
   - In-memory export of OGC GeoPackage (`.gpkg`) with dual layers: `routes` and `stops`.
   - RFC 7946 GeoJSON export to `io.BytesIO`.
   - Apache Parquet / GeoParquet columnar export to `io.BytesIO`.
   - Verification of embedded provenance metadata (`pipeline_version`, `baseline_tag`, `git_commit_hash`, `timestamp_utc`).
   - Reshaping: `wide` vs `long`.
   - **Negative Assertion 1:** Raise `ValueError` if unsupported output format requested.
   - **Negative Assertion 2:** Raise `ValueError` if required coordinate columns are missing during spatial serialization.
2. Implement `src/bbs_pipeline/export/vector.py`:
   - `export_vector_dataset(df: pl.DataFrame, format: str, shape: str = "long", metadata: dict = None) -> io.BytesIO`
   - Terminal GeoPandas boundary restricted exclusively to this serialization module.

**Exit Criteria:**
- `ruff check src/ tests/` passes.
- `ruff format --check src/ tests/` passes.
- 100% test pass rate across all test suites.
- Atomic commit merged via `git merge --ff-only`.

---

### Phase 5: CLI Extension & Dual-Interface Orchestration ✅ COMPLETE
**Branch:** `feature/phase-5-cli-extension`
**Target Modules:**
- `src/bbs_pipeline/cli.py`
- `tests/test_cli_additive.py`

**Tasks:**
1. Implement test suite in `tests/test_cli_additive.py`:
   - Assert that existing v1.0.0 CLI invocations execute without changes or failures.
   - Test additive flags: `--include-stops`, `--stop-geometry`, `--stop-range`, `--spatial-boundary`, `--spatial-filter-mode`, `--shape`.
   - **Negative Assertion 1:** Raise CLI argument error when `--stop-range` bounds are invalid (e.g., `start > end` or `start < 1` or `end > 50`).
   - **Negative Assertion 2:** Raise CLI argument error when `--spatial-boundary` file does not exist or contains invalid polygon data.
2. Extend `src/bbs_pipeline/cli.py`:
   - Wire additive arguments via `argparse` preserving existing option defaults.
   - Stream output directly to stdout or specified file buffer.

**Exit Criteria:**
- `ruff check src/ tests/` passes.
- `ruff format --check src/ tests/` passes.
- 100% test pass rate across baseline and additive tests.
- Atomic commit merged via `git merge --ff-only`.

---

### Phase 6: Reactive Web GUI Linear Spatial Explorer ✅ COMPLETE
**Branch:** `feature/phase-6-gui-extension`
**Target Modules:**
- `src/bbs_pipeline/gui.py`
- `tests/test_gui_additive.py`

**Tasks:**
1. Implement tests in `tests/test_gui_additive.py` verifying state isolation of Tab 3 components and session state defaults.
2. Extend `src/bbs_pipeline/gui.py`:
   - Add dedicated Tab 3: "📍 Stop-Level Linear Spatial View".
   - Sub-route range dual slider ($1 \dots 50$).
   - Polygon GeoJSON file uploader and bounding box coordinate inputs.
   - In-memory Stop Point Map display color-coded by detection intensity with hover metadata.
   - In-memory download buttons (`st.download_button`) for GeoPackage, GeoJSON, Parquet, and CSV streaming directly from `io.BytesIO`.
   - Preserve existing Tabs 1 and 2 intact without regressions.

**Exit Criteria:**
- `ruff check src/ tests/` passes.
- `ruff format --check src/ tests/` passes.
- Full regression suite and all additive tests pass 100%.
- Atomic commit merged via `git merge --ff-only`.
- Project tagged at `v2.0.0`.