# docs/06_OPERATOR_RUNBOOK.md

# Operator Steering Runbook: Phased Prompts

Use the following pre-scripted XML prompts to drive the autonomous agent step-by-step[cite: 1, 2]. Do not execute Phase $(N+1)$ until Phase $N$ completes and its verification gate passes[cite: 1, 2].

---

### Prompt: Phase 1 — Repository Foundation & Environment
```xml
<context>
Active Milestone: Phase 1
Blueprints: AGENTS.md, docs/01_PLAYBOOK.md, docs/02_ARCHITECTURE.md, docs/04_TASKS.md, docs/05_CONSTRAINTS.md
Repository State: Uninitialized
</context>

<mandate>
Initialize the project repository structure, build configuration, and testing harness.
Strictly adhere to the Toolchain Whitelist in docs/02_ARCHITECTURE.md.
</mandate>

<tasks>
1. Initialize git repository on branch main and establish production .gitignore.
2. Author pyproject.toml specifying Python >=3.10 and exact dependencies: polars>=0.20.0,<1.0.0, pyarrow>=14.0.0, requests>=2.31.0, urllib3>=2.0.0, geopandas>=0.14.0, pyproj>=3.6.0, shapely>=2.0.0, streamlit>=1.30.0, pytest>=8.0.0, requests-mock>=1.11.0.
3. Establish package directory layout in src/bbs_pipeline/ and tests/ with empty __init__.py files.
4. Configure pytest.ini and conftest.py with baseline fixtures.
5. Create CLAUDE.md and .cursorrules as pointers/symlinks to AGENTS.md.
</tasks>

<execution>
Execute commands:
  git init -b main
  pytest tests/
Verify git status is clean. Include at least two failure-path test assertions.
Commit: "feat(core): initialize repository layout, packaging, and testing harness"
Mark Phase 1 complete in docs/04_TASKS.md and STOP. Do not proceed to Phase 2.
</execution>
```

---

### Prompt: Phase 2 — In-Memory ScienceBase Client & Tabular Parsers
```xml
<context>
Active Milestone: Phase 2
Active Files: src/bbs_pipeline/client/sciencebase.py, src/bbs_pipeline/client/parser.py, tests/test_client.py
Repository State: Phase 1 committed.
</context>

<mandate>
Implement the streaming ScienceBase HTTP client and in-memory ZIP/CSV Polars parser.
Strictly enforce the Zero-Disk In-Memory Mandate (docs/05_CONSTRAINTS.md) and explicit schema overrides (docs/03_SCHEMAS.md).
</mandate>

<tasks>
1. Implement sciencebase.py: Fetch archives from ScienceBase API (default item 64ad9c3dd34e70357a292cee) streaming raw bytes directly into io.BytesIO with urllib3/requests exponential backoff on HTTP 429, 500, 502, 503, 504.
2. Implement parser.py: Decompress ZIP streams in RAM and ingest CSVs into Polars DataFrames using exact schema_overrides defined in docs/03_SCHEMAS.md (FIFTY_STOP_SCHEMA, TEN_STOP_SCHEMA, WEATHER_SCHEMA, VEHICLE_SCHEMA, ROUTES_SCHEMA).
3. Implement test_client.py with requests_mock: Validate end-to-end memory streaming, retry backoff on 503, schema enforcement, and verify that no disk files are created. Include at least 2 negative failure assertions.
</tasks>

<execution>
Run pytest -v tests/test_client.py. Verify 100% pass and no unhandled warnings.
Commit: "feat(client): implement in-memory streaming sciencebase client and polars parsers"
Mark Phase 2 complete in docs/04_TASKS.md and STOP. Do not proceed to Phase 3.
</execution>
```

---

### Prompt: Phase 3 — Domain Filters, Continuity & Dynamic Horizon Discovery
```xml
<context>
Active Milestone: Phase 3
Active Files: src/bbs_pipeline/core/discovery.py, src/bbs_pipeline/core/filters.py, tests/test_filters.py
Repository State: Phase 2 committed.
</context>

<mandate>
Implement dynamic temporal discovery, composite route keying, COVID-19 hiatus exclusion, and proportional route continuity filtering.
</mandate>

<tasks>
1. Implement discovery.py: Discover max_observed_year dynamically from weather dataset. Calculate eligible survey year set excluding 2020.
2. Implement filters.py:
   - Compute composite RouteKey: CountryNum + "_" + StateNum + "_" + Route.
   - Apply proportional continuity thresholding: required_runs = ceil(|eligible_years| * min_completeness_pct / 100).
   - Enforce Discrete Stop Effort Guards: Filter routes by TotalStops >= min_stops. Impute NULL for stops > TotalStops.
   - Implement sub-route stop range slicing (start_stop to end_stop).
3. Author test_filters.py validating boundary conditions, 2020 exclusion, and stop nullification. Include at least 2 negative failure assertions.
</tasks>

<execution>
Run pytest -v tests/test_filters.py. Verify 100% pass and no unhandled warnings.
Commit: "feat(core): implement temporal discovery, continuity filtering, and stop guards"
Mark Phase 3 complete in docs/04_TASKS.md and STOP. Do not proceed to Phase 4.
</execution>
```

---

### Prompt: Phase 4 — Taxonomic Set-Union Resolver & Observer Covariates
```xml
<context>
Active Milestone: Phase 4
Active Files: src/bbs_pipeline/core/taxonomy.py, src/bbs_pipeline/core/covariates.py, tests/test_taxonomy.py, tests/test_covariates.py
Repository State: Phase 3 committed.
</context>

<mandate>
Implement the taxonomic set-union resolver with migrant exclusion and observer experience covariate calculations.
</mandate>

<tasks>
1. Implement taxonomy.py: Parse SpeciesList and guilds.json (located at data/guilds.json). Evaluate set union across guild, order, family, and custom AOU inputs, excluding migrant/non-breeder AOUs. Handle --all-species / --community flag.
2. Implement covariates.py: Calculate CareerSurveysCompleted, RouteTenure, binary IsFirstYearObserver (Kendall bias), and traffic rate metrics (CarTotal, CarsPerStop).
3. Author test_taxonomy.py and test_covariates.py. Include at least 2 negative failure assertions.
</tasks>

<execution>
Run pytest -v tests/test_taxonomy.py tests/test_covariates.py. Verify 100% pass.
Commit: "feat(core): implement taxonomic set resolver and observer covariate engine"
Mark Phase 4 complete in docs/04_TASKS.md and STOP. Do not proceed to Phase 5.
</execution>
```

---

### Prompt: Phase 5 — 4-Step Extirpation-Preserving Zero-Filling Engine
```xml
<context>
Active Milestone: Phase 5
Active Files: src/bbs_pipeline/core/zero_fill.py, tests/test_zero_fill.py
Repository State: Phase 4 committed.
</context>

<mandate>
Implement the 4-step Cartesian zero-filling engine preserving historical extirpation boundaries and preventing geographic species leakage.
Strictly enforce the Zero-Disk Mandate and Polars DataFrame APIs (no Pandas).
</mandate>

<tasks>
1. Implement zero_fill.py:
   - Step 1: Scan full 1966–present history to determine confirmed route taxa S_r.
   - Step 2: Extract valid survey years Y_r for route r.
   - Step 3: Construct Cartesian product grid G_r = Y_r x S_r.
   - Step 4: Left-join G_r with observations; impute zeros for unobserved stops 1..TotalStops and NULL for stops > TotalStops.
2. Author test_zero_fill.py: Test extirpation boundary preservation, verification of zero geographic leakage, and stop-count boundary guards. Include at least 2 negative failure assertions.
</tasks>

<execution>
Run pytest -v tests/test_zero_fill.py. Verify 100% pass.
Commit: "feat(core): implement 4-step extirpation-preserving zero-filling engine"
Mark Phase 5 complete in docs/04_TASKS.md and STOP. Do not proceed to Phase 6.
</execution>
```

---

### Prompt: Phase 6 — Spatial Anchoring, Serialization & Provenance Stamping
```xml
<context>
Active Milestone: Phase 6
Active Files: src/bbs_pipeline/export/spatial.py, src/bbs_pipeline/export/serializer.py, tests/test_export.py
Repository State: Phase 5 committed.
</context>

<mandate>
Implement spatial anchoring 1:1 to route origins, coordinate transformations, tabular output shaping (wide/long), format serialization, and provenance metadata stamping.
</mandate>

<tasks>
1. Implement spatial.py: Join NAD83 coordinates from routes.csv 1:1 to route records. Reproject to target CRS (EPSG:4326, EPSG:5070, EPSG:32119, or user-supplied EPSG) via GeoPandas/PyProj.
2. Implement serializer.py:
   - Support 'wide' (Stop1..Stop50) and 'long' unpivoted output shapes.
   - Export to GeoPackage (.gpkg), GeoJSON (.geojson), Parquet (.parquet), and CSV (.csv).
   - Query git rev-parse --short HEAD and embed git hash, dataset year bounds, and export timestamp into dataset metadata/attributes.
3. Author test_export.py verifying spatial projections, wide/long transformations, and metadata headers. Include at least 2 negative failure assertions.
</tasks>

<execution>
Run pytest -v tests/test_export.py. Verify 100% pass.
Commit: "feat(export): implement spatial projections, format serializers, and provenance stamping"
Mark Phase 6 complete in docs/04_TASKS.md and STOP. Do not proceed to Phase 7.
</execution>
```

---

### Prompt: Phase 7 — Dual Interface (CLI & Reactive Streamlit Dashboard)
```xml
<context>
Active Milestone: Phase 7
Active Files: src/bbs_pipeline/cli.py, src/bbs_pipeline/gui.py, tests/test_cli.py
Repository State: Phase 6 committed.
</context>

<mandate>
Implement the dual CLI and Streamlit GUI interfaces wiring together all pipeline components.
</mandate>

<tasks>
1. Implement cli.py (extract_bbs.py): Parse spatial, taxonomic, temporal, slicing, and serialization CLI arguments using argparse.
2. Implement gui.py (run_gui.py): Build reactive Streamlit interface with state/route multi-select pills, dynamic taxonomic filters, year range dual-sliders, live map preview, and streaming st.download_button.
3. Author test_cli.py: Test complete pipeline integration using mocked ScienceBase data. Include at least 2 negative failure assertions.
</tasks>

<execution>
Run pytest -v tests/. Verify the entire repository test suite passes with 100% success.
Commit: "feat(interface): implement standalone CLI and reactive Streamlit web dashboard"
Mark Phase 7 complete in docs/04_TASKS.md and STOP.
</execution>
```
