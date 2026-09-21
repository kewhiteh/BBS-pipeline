# docs/04_TASKS.md

# Phased Implementation Roadmap & Verification Gates

## Phase 1: Repository Foundation, Invariants & Environment Setup
- [x] Task 1.1: Initialize Git repository on branch `main` and generate production `.gitignore`[cite: 1].
- [x] Task 1.2: Generate `pyproject.toml` with strict dependencies and sync locked environment (`uv.lock`)[cite: 1].
- [x] Task 1.3: Create source package layout (`src/bbs_pipeline/`) and test tree (`tests/`) with package initializers.
- [x] Task 1.4: Establish test runner configuration (`pytest.ini`, `conftest.py`) and pre-commit linting checks (`ruff`).
- [x] Phase 1 Gate: `pytest tests/` runs cleanly; `git status` shows clean tree; atomic commit tagged.

## Phase 2: In-Memory ScienceBase Client & Tabular Parsers
- [x] Task 2.1: Implement `sciencebase.py` HTTP client streaming remote archives into `io.BytesIO` with exponential backoff on 429/5xx[cite: 1, 3].
- [x] Task 2.2: Implement `parser.py` parsing nested in-memory ZIP/CSV streams using explicit Polars schema overrides[cite: 1, 3].
- [x] Task 2.3: Implement unit and failure-path tests in `test_client.py` using mocked byte streams (verify zero disk access and schema compliance)[cite: 1, 3].
- [x] Phase 2 Gate: 100% test pass on mocked network streaming and parsing; zero disk writes verified; atomic commit tagged[cite: 1].

## Phase 3: Domain Filters, Continuity & Dynamic Temporal Discovery
- [x] Task 3.1: Implement `discovery.py` to extract `max_observed_year` from weather records and compute eligible year sets excluding 2020[cite: 3].
- [x] Task 3.2: Implement `filters.py` for composite route keying (`CountryNum_StateNum_Route`) and proportional continuity thresholding[cite: 3].
- [x] Task 3.3: Implement stop effort guards and sub-route slicing with strict NULL bounds for stops exceeding `TotalStops`[cite: 3].
- [x] Task 3.4: Implement unit and failure-path tests in `test_filters.py` verifying mathematical completeness bounds[cite: 1].
- [x] Phase 3 Gate: 100% test pass on continuity logic and stop filtering; atomic commit tagged[cite: 1].

## Phase 4: Taxonomic Set-Union Resolver & Observer Covariates
- [x] Task 4.1: Implement `taxonomy.py` evaluating set-unions across orders, families, guilds, and individual AOUs, applying migrant exclusions[cite: 1, 3].
- [x] Task 4.2: Implement `covariates.py` calculating career survey counts, route tenure, first-year Kendall bias indicators, and traffic metrics[cite: 3].
- [x] Task 4.3: Implement unit and negative tests in `test_taxonomy.py` and `test_covariates.py`[cite: 1].
- [x] Phase 4 Gate: 100% test pass on taxonomic set algebra and covariate computation; atomic commit tagged[cite: 1].

## Phase 5: 4-Step Cartesian Zero-Filling Engine
- [x] Task 5.1: Implement `zero_fill.py` Step 1 (scan 1966–present history for route-confirmed species $\mathcal{S}_r$)[cite: 3].
- [x] Task 5.2: Implement Steps 2–4 (construct $\mathcal{Y}_r \times \mathcal{S}_r$ Cartesian grid, left-join counts, impute zeros for $1 \dots \text{TotalStops}$, NULL for $> \text{TotalStops}$)[cite: 3].
- [x] Task 5.3: Implement failure-path and property tests in `test_zero_fill.py` ensuring zero geographic species leakage[cite: 1, 3].
- [x] Phase 5 Gate: 100% test pass; verified that extirpated/absent species never leak into unconfirmed routes; atomic commit tagged[cite: 1].

## Phase 6: Spatial Anchoring, Serialization & Provenance Metadata
- [x] Task 6.1: Implement `spatial.py` converting NAD83 route starting coordinates into target projections (`EPSG:4326`, `EPSG:5070`, `EPSG:32119`, arbitrary EPSG)[cite: 3].
- [x] Task 6.2: Implement `serializer.py` outputting wide/long tabular shapes to OGC GeoPackage, GeoJSON, Parquet, and CSV[cite: 3].
- [x] Task 6.3: Implement Git hash extraction (`git rev-parse --short HEAD`) and provenance metadata header stamping[cite: 1, 3].
- [x] Task 6.4: Implement serialization test suite in `test_export.py`[cite: 1].
- [x] Phase 6 Gate: 100% test pass across all export formats; provenance fields verified in metadata; atomic commit tagged[cite: 1].

## Phase 7: Dual Interface (CLI & Reactive Streamlit Dashboard)
- [x] Task 7.1: Implement CLI (`cli.py` / `extract_bbs.py`) exposing all spatial, temporal, taxonomic, and export arguments[cite: 3].
- [x] Task 7.2: Implement reactive Streamlit dashboard (`gui.py` / `run_gui.py`) with synchronized filter pills, map preview, and streaming download button[cite: 3].
- [x] Task 7.3: Implement end-to-end integration tests in `test_cli.py` executing a full in-memory pipeline dry-run[cite: 1].
- [x] Phase 7 Gate: All CLI options verified; Streamlit app verified; full test suite passes with zero warnings; final commit tagged[cite: 1].