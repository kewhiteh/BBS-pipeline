# docs/02_ARCHITECTURE.md

# System Architecture & Approved Toolchain Manifest

## 1. Directory Structure
```text
usgs-bbs-pipeline/
├── AGENTS.md
├── CLAUDE.md -> AGENTS.md
├── .cursorrules -> AGENTS.md
├── pyproject.toml
├── uv.lock
├── README.md
├── docs/
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
│       │   ├── __init__.py
│       │   ├── sciencebase.py      # HTTP streaming client with backoff
│       │   └── parser.py           # In-memory ZIP/CSV parsing to Polars
│       ├── core/
│       │   ├── __init__.py
│       │   ├── discovery.py        # Dynamic temporal discovery & COVID Hiatus
│       │   ├── filters.py          # Spatial, route continuity & stop slicing
│       │   ├── taxonomy.py         # Set-union taxonomic resolver & exclusions
│       │   ├── zero_fill.py        # 4-step Cartesian zero-filling engine
│       │   └── covariates.py       # Weather, noise & observer tenure metrics
│       ├── export/
│       │   ├── __init__.py
│       │   ├── spatial.py          # Coordinate transformation & GeoDataFrame anchor
│       │   └── serializer.py       # GPKG, Parquet, GeoJSON, CSV & provenance metadata
│       ├── cli.py                  # Standalone CLI interface (extract_bbs.py)
│       └── gui.py                  # Reactive Streamlit interface (run_gui.py)
└── tests/
    ├── conftest.py                 # Synthetic in-memory byte generators
    ├── test_client.py
    ├── test_filters.py
    ├── test_taxonomy.py
    ├── test_zero_fill.py
    ├── test_covariates.py
    └── test_export.py
```

## 2. Approved Toolchain Manifest

| Category | Component / Dependency | Approved Version Scope | Purpose |
| :--- | :--- | :--- | :--- |
| **Runtime** | Python | `>=3.10, <3.13` | Base execution environment[cite: 3] |
| **Core Ingestion / ETL** | `polars` | `>=0.20.0, <1.0.0` | High-performance in-memory tabular processing[cite: 3] |
| **Data Types / Formats**| `pyarrow` | `>=14.0.0` | Polars underlying columnar format & Parquet IO[cite: 3] |
| **Network & Backoff** | `requests`, `urllib3` | `requests>=2.31.0`, `urllib3>=2.0.0` | ScienceBase API interaction with exponential retry[cite: 3] |
| **Spatial Transform** | `geopandas`, `pyproj`, `shapely` | `geopandas>=0.14.0`, `pyproj>=3.6.0`, `shapely>=2.0.0` | Terminal vector generation and CRS reprojection[cite: 3] |
| **User Interface** | `streamlit` | `>=1.30.0` | Reactive web interface with in-memory streaming export[cite: 3] |
| **Test Engine** | `pytest`, `requests-mock` | `pytest>=8.0.0`, `requests-mock>=1.11.0` | Unit, negative, and integration testing with mocked bytes[cite: 3] |
| **Packaging / Linting** | `uv`, `ruff`, `mypy` | Latest stable | Deterministic environment, formatting, and type-checks |

### Strict Boundary Rules:
- Under no circumstance may `duckdb`, `sqlite3`, `pandas` (in ETL routines), or `fastparquet` be added to dependencies[cite: 3].