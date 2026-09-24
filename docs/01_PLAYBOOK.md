# Master Engineering Playbook (v2.0)
## Track B Additive Engineering Invariants & Execution Protocols

This document establishes the mandatory engineering standards, data handling contracts, quality assurance gates, and Git workflow requirements for USGS Breeding Bird Survey (BBS) Pipeline v2.0.

---

## 1. Track B Additive Upgrade Governance

### 1.1 Blast-Radius & Anti-Regression Contract
- Code verified in release `v1.0.0` is strictly immutable.
- All additive v2.0 capabilities (50-stop ingestion, linear referencing, two-tier spatial filtering, long/wide spatial vector export, and additive UI controls) must reside in dedicated additive modules:
  - `src/bbs_pipeline/ingestion/stops.py`
  - `src/bbs_pipeline/processing/linear_referencing.py`
  - `src/bbs_pipeline/spatial/filtering.py`
  - `src/bbs_pipeline/export/vector.py`
  - Extension hooks in `src/bbs_pipeline/cli.py` and `src/bbs_pipeline/gui.py`
- Baseline files under strict read-only lock:
  - `src/bbs_pipeline/client/parser.py`
  - `src/bbs_pipeline/client/sciencebase.py`
  - `src/bbs_pipeline/core/discovery.py`
  - `src/bbs_pipeline/core/zero_fill.py`
  - `src/bbs_pipeline/ingestion/routes.py`
  - `src/bbs_pipeline/processing/aggregation.py`

### 1.2 Zero-Touch Test Fixture Mandate
- Baseline unit and integration test suites (`tests/test_routes_ingestion.py`, `tests/test_aggregations.py`) cannot be modified or relaxed.
- Existing tests must execute cleanly and pass 100% at every milestone verification gate.
- New features must be tested in dedicated additive test modules (`tests/test_stops_ingestion.py`, `tests/test_linear_referencing.py`, `tests/test_spatial_filtering.py`, `tests/test_vector_export.py`, `tests/test_cli_additive.py`).

### 1.3 Backward-Compatible Public Interfaces
- Existing CLI flags, arguments, class signatures, and return structures established in v1.0.0 must remain fully functional.
- Additive CLI flags (`--include-stops`, `--stop-geometry`, `--stop-range`, `--spatial-boundary`, `--spatial-filter-mode`, `--shape`) default to preserving v1.0.0 behavior when omitted.
- The web GUI adds an isolated tab ("📍 Stop-Level Linear Spatial View") while preserving the layout and behavior of existing tabs.

---

## 2. Universal Data Engineering Invariants (Domain Profile A)

### 2.1 Ingestion Boundary Isolation & Zero-Inference Mandate
- Flat-file tabular readers (`pl.read_csv`, `pl.scan_csv`, or batched readers) must disable type inference (`infer_schema_length=0` or `infer_schema=False`).
- Every column at the physical ingestion boundary must be read natively as `pl.String`.
- Immediate header sanitization is mandatory:
  ```python
  df = df.rename({c: c.strip() for c in df.columns})
  ```
- Exhaustive null tokens must be explicitly declared upon ingestion:
  ```python
  null_values = ["", "NA", "null", "NULL", "*", "None"]
  ```
  with `truncate_ragged_lines=True`.

### 2.2 Deferred Arithmetic Principle
- String-based identifiers (`CountryNum`, `StateNum`, `Route`, `AOU`, `RouteDataID`, `RPID`, `Year`, `StopID`) must NEVER be converted to numeric types.
- Numeric operands (`Stop1`..`Stop50`, `StopDistanceMiles`, `StopLatitude`, `StopLongitude`, `SegmentCount`) must be cast downstream at computation, spatial interpolation, or export points using non-strict casting:
  ```python
  lf = lf.with_columns(
      [pl.col(f"Stop{i}").cast(pl.Int32, strict=False) for i in range(1, 51)]
  )
  ```
- Raw ingestion unit tests must explicitly assert `pl.String` for all columns.

### 2.3 Scope-Restricted Zero-Filling & Covariate Null Preservation
- Zero imputation (`0`) is strictly confined to observed historical breeding taxa on route $r$. Taxa never recorded on route $r$ are never zero-filled.
- For surveyed stops $1 \dots \text{TotalStops}$ (where $\text{TotalStops} \in \{45..50\}$), missing counts for target taxa are imputed as `0`.
- Stops beyond route completion ($k > \text{TotalStops}$ up to $50$) must preserve native `NULL` states. Unconducted stops must never be imputed as `0`.
- Environmental and weather covariates (`StartTemp`, `EndTemp`, `StartWind`, `EndWind`, `StartSky`, `EndSky`, `StartTime`, `EndTime`) must never be zero-filled.

### 2.4 Zero-Disk In-Memory Mandate
- Intermediate files must never be written to local storage.
- Archives streamed from the USGS ScienceBase API or uploaded via the GUI must be unpacked and processed in RAM buffers using `io.BytesIO`.
- Vector and tabular exports must serialize directly to memory buffers prior to streaming via CLI stdout or Streamlit `st.download_button`.

---

## 3. Spatial & Linear Referencing Invariants

### 3.1 50-Stop Linear Referencing Standard
- Routes follow a standardized 24.5-mile (39.4289 km) transect with 50 listening stops spaced at regular 0.5-mile (804.672 m) intervals.
- Cumulative distance measure $M_i$ for stop $i \in \{1, \dots, 50\}$:
  $$M_i = (i - 1) \times 0.5\text{ miles}$$
- Interpolation workflow:
  1. Project route polyline from NAD83/WGS84 (EPSG:4269/EPSG:4326) into conformal equidistant projection EPSG:5070 (CONUS Albers).
  2. Compute Euclidean arc length along polyline:
     $$P_i = \text{line.interpolate}(M_i \times 1609.344)$$
  3. Transform interpolated coordinates back into target CRS (e.g., EPSG:4326).
  4. Assert monotonic distance progression:
     $$\text{distance}(P_1, P_i) \le \text{distance}(P_1, P_{i+1})$$
  5. Fallback mechanism: If route line geometry is missing or corrupt, generate synthetic stops along the initial route bearing anchored at `(Latitude, Longitude)` from `routes.csv` and set `GeometrySource = "origin_linear_fallback"`.

### 3.2 Two-Tier Spatial Filtering Protocol
- **Tier 1 (Route Envelope Coarse Filter):** Rapidly reject out-of-boundary candidate routes using a Shapely STRtree indexing route polyline / stop bounding boxes against the query polygon envelope.
- **Tier 2 (Stop-Level Fine Filter):** Vectorized point-in-polygon containment evaluation (`shapely.contains(query_polygon, stop_point)`).
- Filter behaviors:
  - `STRICT_CONTAINMENT`: Retain route if and only if all surveyed stops ($1 \dots \text{TotalStops}$) fall inside the boundary.
  - `ANY_STOP_INTERSECT`: Retain entire route if $\ge 1$ stop intersects the boundary.
  - `SPATIAL_STOP_MASK`: Retain route, but set outside-polygon stop counts to `NULL` and recompute route totals.

---

## 4. Software Quality & Verification Standards

### 4.1 Test-Driven Development (TDD) Mandate
- Every phase must deliver unit and integration tests alongside or prior to feature implementation.
- Every phase test suite must include a minimum of two negative failure-path tests asserting explicit domain exceptions on invalid data (e.g., ragged stop records, non-monotonic geometries, out-of-range stop slicing).
- 100% test pass rate is mandatory across both baseline and additive suites.

### 4.2 Code Formatting & Static Analysis
- Universal linter and formatter: `ruff`.
- Formatting command: `ruff format --check src/ tests/`
- Linting command: `ruff check src/ tests/`
- Zero warnings, zero lint violations, and zero formatting discrepancies are permitted.

### 4.3 Git Feature Branching & Release Discipline
- Main branch protection: Direct commits to `main` are strictly prohibited.
- Branch naming convention: `feature/phase-<N>-<milestone-name>`.
- Commit discipline: Conventional Commits (`feat:`, `test:`, `docs:`, `fix:`, `refactor:`).
- Merge protocol: Fast-forward merge only (`git merge --ff-only`).
- Provenance stamping: Every export must record `git rev-parse --short HEAD` alongside pipeline version `2.0.0` and baseline tag `v1.0.0`.