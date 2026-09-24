# System Architecture & Component Design (v2.0)
## Track B Additive Architectural Specification

This document details the modular structure, streaming dataflow, approved dependencies, and component boundaries for USGS Breeding Bird Survey (BBS) Pipeline v2.0.

---

## 1. High-Level Architecture Overview

The v2.0 upgrade introduces fine-grained 50-stop observation ingestion, sub-mile linear referencing, two-tier spatial querying, and multi-format vector exports on top of the established v1.0.0 core.

```
                           [ USGS ScienceBase REST Catalog ]
                                          │
                  ┌───────────────────────┴───────────────────────┐
                  ▼                                               ▼
         [ 50-StopData.zip ]                             [ routes.csv / GIS Lines ]
                  │                                               │
                  ▼ (In-Memory io.BytesIO Stream)                 ▼ (In-Memory io.BytesIO Stream)
     ┌─────────────────────────┐                     ┌─────────────────────────┐
     │ Ingestion: stops.py     │                     │ Ingestion: routes.py    │ (Baseline v1.0)
     │ (infer_schema_length=0) │                     │ (Route Attributes)      │
     └────────────┬────────────┘                     └────────────┬────────────┘
                  │                                               │
                  ▼                                               ▼
     ┌─────────────────────────┐                     ┌─────────────────────────┐
     │ Core: zero_fill.py      │ (Baseline v1.0)     │ Processing:             │
     │ (Stop-Level Expansion)  │                     │ linear_referencing.py   │ (v2.0 Additive)
     └────────────┬────────────┘                     │ (0.5-mi Stop Geometries)│
                  │                                  └────────────┬────────────┘
                  └───────────────────────┬───────────────────────┘
                                          ▼
                             ┌─────────────────────────┐
                             │ Spatial: filtering.py   │ (v2.0 Additive)
                             │ Tier 1: STRtree Envelope│
                             │ Tier 2: Point-in-Polygon│
                             └────────────┬────────────┘
                                          │
                  ┌───────────────────────┴───────────────────────┐
                  ▼                                               ▼
     ┌─────────────────────────┐                     ┌─────────────────────────┐
     │ Export: vector.py       │ (v2.0 Additive)     │ Presentation Layer      │
     │ - GeoPackage (.gpkg)    │                     │ - CLI: cli.py           │
     │ - GeoJSON (.geojson)    │                     │ - GUI: gui.py (Tab 3)   │
     │ - GeoParquet (.parquet) │                     │ (In-Memory Streaming)   │
     └─────────────────────────┘                     └─────────────────────────┘
```

---

## 2. Directory Layout & Module Responsibilities

```text
bbs_pipeline/
├── AGENTS.md                          # Universal root agent directives
├── CLAUDE.md                          # Pointer to AGENTS.md
├── pyproject.toml                     # Project configuration and dependency lock
├── uv.lock                            # Deterministic frozen dependency lockfile
├── docs/                              # Formal system documentation suite (v2.0)
│   ├── 01_PLAYBOOK.md
│   ├── 02_ARCHITECTURE.md
│   ├── 03_SCHEMAS.md
│   ├── 04_TASKS.md
│   ├── 05_CONSTRAINTS.md
│   └── 06_OPERATOR_RUNBOOK.md
├── src/
│   └── bbs_pipeline/
│       ├── __init__.py
│       ├── client/
│       │   ├── parser.py              # [LOCKED v1.0] Read-only parsing helpers
│       │   └── sciencebase.py         # [LOCKED v1.0] ScienceBase API client
│       ├── core/
│       │   ├── discovery.py           # [LOCKED v1.0] Catalog release discovery
│       │   └── zero_fill.py           # [LOCKED v1.0] Core 4-step Cartesian zero-filling
│       ├── ingestion/
│       │   ├── routes.py              # [LOCKED v1.0] Route attribute ingestion
│       │   └── stops.py               # [ADDITIVE v2.0] In-memory 50-stop ingestion engine
│       ├── processing/
│       │   ├── aggregation.py         # [LOCKED v1.0] Route-level aggregations
│       │   └── linear_referencing.py  # [ADDITIVE v2.0] 0.5-mile stop geometry interpolation
│       ├── spatial/
│       │   ├── __init__.py
│       │   └── filtering.py           # [ADDITIVE v2.0] Two-tier STRtree spatial filtering
│       ├── export/
│       │   ├── __init__.py
│       │   └── vector.py              # [ADDITIVE v2.0] In-memory GPKG, GeoJSON, Parquet export
│       ├── cli.py                     # [EXTENDED v2.0] CLI entry point with additive flags
│       └── gui.py                     # [EXTENDED v2.0] Streamlit UI with Stop-Level tab
└── tests/
    ├── conftest.py                    # Shared test fixtures and in-memory zip generators
    ├── test_routes_ingestion.py       # [LOCKED v1.0] Baseline route ingestion tests
    ├── test_aggregations.py           # [LOCKED v1.0] Baseline aggregation tests
    ├── test_stops_ingestion.py        # [ADDITIVE v2.0] 50-stop ingestion & deferred casting tests
    ├── test_linear_referencing.py     # [ADDITIVE v2.0] Monotonic linear referencing tests
    ├── test_spatial_filtering.py      # [ADDITIVE v2.0] Two-tier spatial filter tests
    ├── test_vector_export.py          # [ADDITIVE v2.0] In-memory spatial serialization tests
    ├── test_cli_additive.py           # [ADDITIVE v2.0] CLI flag backward-compatibility tests
    └── test_gui_additive.py           # [ADDITIVE v2.0] GUI widget integration tests
```

---

## 3. Approved Toolchain Manifest

Downstream coding agents are strictly bound to this manifest. Introducing unlisted packages is a tool-lock violation.

| Component / Layer | Approved Technology | Approved Version Constraint | Purpose / Justification |
| :--- | :--- | :--- | :--- |
| **Language Runtime** | Python | `>=3.10,<3.13` | Modern typing, pattern matching, performance. |
| **Tabular Engine** | `polars` | `>=0.20.0,<1.0.0` | High-performance LazyFrame processing, zero-copy RAM operations. |
| **Columnar Memory** | `pyarrow` | `>=14.0.0` | Parquet buffer serialization and Arrow data exchange. |
| **Spatial Geometry** | `shapely` | `>=2.0.0` | Vectorized 2D geometry manipulation, STRtree spatial indexing. |
| **Coordinate Projection** | `pyproj` | `>=3.6.0` | Geodesic transformations (EPSG:4269/4326 to EPSG:5070). |
| **Spatial Vector Export** | `geopandas` | `>=0.14.0` | Terminal OGC GeoPackage and GeoJSON serialization (restricted to export boundary). |
| **HTTP Client** | `requests` | `>=2.31.0` | ScienceBase REST API communication. |
| **HTTP Transport** | `urllib3` | `>=2.0.0` | Connection pooling and retry backoff. |
| **Web GUI** | `streamlit` | `>=1.30.0` | Interactive reactive exploration dashboard. |
| **Test Runner** | `pytest` | `>=8.0.0` | Automated unit, regression, and integration testing. |
| **HTTP Mocking** | `requests_mock` | `>=1.11.0` | Mocking ScienceBase HTTP responses in memory. |
| **Linter / Formatter** | `ruff` | `>=0.2.0` | Universal deterministic code analysis and formatting. |

### Negative Tooling Boundary:
- `pandas` is banned from all ETL, joining, filtering, aggregation, and zero-filling logic. It is permitted solely inside `src/bbs_pipeline/export/vector.py` at the terminal boundary required by GeoPandas.
- Disk-staging engines (`duckdb`, `sqlite3`, temporary directory libraries) are strictly banned.

---

## 4. Subsystem Interactions & In-Memory Dataflow

### 4.1 Ingestion Subsystem (`stops.py`)
- Streams `50-StopData.zip` from ScienceBase into memory.
- Uses Python `zipfile.ZipFile` against an `io.BytesIO` buffer to read members `fifty1.csv` through `fifty10.csv`.
- Each member is read via `pl.read_csv(member_stream, infer_schema_length=0, null_values=...)`.
- Column names are stripped of whitespace; composite keys (`RouteKey`, `StopKey`) are derived as strings.

### 4.2 Linear Referencing Subsystem (`linear_referencing.py`)
- Ingests route continuous polyline coordinates (`EPSG:4269` / `EPSG:4326`).
- Projects lines to `EPSG:5070` using PyProj `Transformer`.
- Performs 50 discrete interpolations along the line:
  $$M_i = (i - 1) \times 804.672\text{ meters}$$
- Verifies that distances along the route are monotonically non-decreasing.
- Returns a structured mapping of `(RouteKey, StopNumber) -> (Latitude, Longitude, DistanceMiles)`.
- If polyline geometry is absent, calculates linear points along route heading and marks `GeometrySource = "origin_linear_fallback"`.

### 4.3 Spatial Filtering Subsystem (`filtering.py`)
- Builds a Shapely `STRtree` over route bounding boxes for Tier 1 candidate pruning.
- Performs Tier 2 point-in-polygon checks on discrete stop coordinates.
- Implements spatial masking modes:
  - `STRICT_CONTAINMENT`: drops routes with any outside stop.
  - `ANY_STOP_INTERSECT`: keeps full route if at least 1 stop intersects.
  - `SPATIAL_STOP_MASK`: masks non-intersecting stops to null and re-aggregates.

### 4.4 Vector Export Subsystem (`vector.py`)
- Transforms Polars DataFrames into GeoDataFrames strictly at the final serialization step.
- Writes OGC GeoPackage (`.gpkg`), RFC 7946 GeoJSON, or GeoParquet into an in-memory `io.BytesIO` buffer.
- Attaches standard provenance metadata (`pipeline_version`, `baseline_tag`, `git_commit_hash`, `timestamp_utc`).