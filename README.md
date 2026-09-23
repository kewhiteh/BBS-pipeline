# USGS Breeding Bird Survey (BBS) Pipeline

A high-performance, in-memory scientific ETL and spatial serialization pipeline for the North American Breeding Bird Survey (USGS BBS).

Designed to replace fragile, memory-leaking scripts with a deterministic, mathematically sound data engine. The pipeline handles end-to-end extraction from remote or local archives, Cartesian zero-filling, longitudinal observer and traffic covariate calculations, and multi-format spatial exports (Parquet, OGC GeoPackage, GeoJSON, CSV).

---

## Key Capabilities

* **Zero-Disk In-Memory Mandate:** Streams compressed remote archives (`States.zip`, `50-StopData.zip`, `weather.csv`, `VehicleData.csv`) directly into RAM buffers (`io.BytesIO`). Zero intermediate disk thrashing, temp CSVs, or scratch files.
* **Dual Ingestion Resolution:** 
  * **10-Stop Summary:** Historical surveys from 1966–present (`States.zip`).
  * **50-Stop Detailed:** Modern high-resolution stop data from 1997–present (`50-StopData.zip`).
* **Extirpation-Preserving Zero-Filling:** Implements a 4-step Cartesian grid engine ($Y_r \times S_r$) per route. Confirmed historical species receive true non-detection zeros for unsighted survey runs, while unrecorded species are excluded to eliminate false-absence geographic leakage.
* **Longitudinal & Environmental Covariates:**
  * **Observer Experience:** Computes `CareerSurveysCompleted`, route-level `RouteTenure`, binary `IsFirstYearObserver` (Kendall first-year observer effect), and analytical cohorts (`Novice`, `Intermediate`, `Veteran`).
  * **Traffic & Disturbance:** Calculates `CarTotal` and `CarsPerStop` traffic rates with active pruning filters.
* **Effort Guards & Stop Slicing:** Arbitrary sub-route stop slicing (1..50) and discrete completed-stop guards (45, 48, 50). Stops beyond `TotalStops` evaluate strictly to `NULL`, never `0`.
* **Output Shapes & Diversity Metrics:**
  * **Tabular Shapes:** `wide` (Stop1..Stop50 / Count10..Count50), `long` (unpivoted records), or `route` (collapsed run summary per Route-Year-Species).
  * **Community Diversity:** Optional run-level calculation of Species Richness ($S$), Total Individuals, Shannon Diversity ($H'$), and Shannon Evenness ($J'$).
* **Spatial Anchoring & Reprojection:** Anchors records 1:1 to official route starting origins (Point geometry in NAD83) and dynamically reprojects to WGS 84 (`EPSG:4326`), CONUS Albers (`EPSG:5070`), NC State Plane (`EPSG:32119`), or user-defined projections.

---

## Installation & Environment

This project enforces strict lockfile pinning via `uv`.

```bash
# Clone repository
git clone <repo-url>
cd v2_bbs_etl

# Sync frozen virtual environment
uv sync --frozen
```

### Dependencies
* **Core Engine:** `polars>=0.20.0,<1.0.0`, `pyarrow>=14.0.0`
* **Spatial:** `geopandas>=0.14.0`, `pyproj>=3.6.0`, `shapely>=2.0.0`
* **Interface:** `streamlit>=1.30.0`
* **Network:** `requests>=2.31.0`, `urllib3>=2.0.0`

---

## Usage

The pipeline exposes a dual interface: an interactive web dashboard and a scriptable CLI.

### 1. Reactive Web GUI (Streamlit)

Launch the interactive dashboard with linked taxonomic filters, route map previews, and streaming in-memory browser downloads:

```bash
uv run streamlit run src/bbs_pipeline/gui.py
```

### 2. Standalone CLI (`bbs_pipeline.cli`)

Run headless extractions with flexible spatial, taxonomic, and temporal flags.

#### Basic Extraction (Auto-Derives Destination in `data/processed/`):
```bash
uv run python -m bbs_pipeline.cli -s NC --species 07610 05980 --start-year 2000 --end-year 2023 -f parquet
```

#### Historical 10-Stop Run with Route Summary & Diversity Metrics:
```bash
uv run python -m bbs_pipeline.cli \
  -s NC \
  --routes 840_63_012 840_63_225 \
  --resolution 10stop \
  --shape route \
  --community-metrics \
  --start-year 1966 \
  --end-year 2025 \
  -f csv \
  -o data/processed/jordan_durham_community_summary.csv
```

#### Spatial Vector Export (GeoPackage in CONUS Albers):
```bash
uv run python -m bbs_pipeline.cli \
  --bcr 28 \
  --guild wetland \
  --crs EPSG:5070 \
  -f gpkg \
  -o data/processed/appalachian_wetlands.gpkg
```

### Common CLI Options

| Flag | Argument | Description |
| :--- | :--- | :--- |
| `-s`, `--states` | `NC VA SC` | State abbreviations, full names, or 2-digit codes. |
| `--routes` | `840_63_001` | Specific composite route keys (`CountryNum_StateNum_Route`). |
| `--species` | `07610 "Wood Thrush"` | 5-digit AOU codes or English common names. |
| `--guild` | `forest_interior` | Ecological guilds defined in `data/guilds.json`. |
| `--all-species` | *(flag)* | Full breeding avifauna matrix (minus non-breeding migrants). |
| `--resolution` | `50stop` \| `10stop` | Ingestion source (`50-StopData.zip` vs. `States.zip`). |
| `--shape` | `wide` \| `long` \| `route` | Grid columns, unpivoted rows, or collapsed run summaries. |
| `--community-metrics`| *(flag)* | Appends run-level Richness, Abundance, Shannon H', and Evenness. |
| `--min-completeness-pct` | `60.0` | Proportional route continuity threshold across eligible years. |
| `--min-stops` | `45` \| `48` \| `50` | Discrete completed-stop effort guard. |
| `--exclude-first-year` | *(flag)* | Drops first-year observer runs (Kendall bias control). |
| `-f`, `--format` | `parquet` \| `csv` \| `gpkg` \| `geojson` | Export serialization format. |
| `-o`, `--output` | `path/to/file` | Target file destination (omitting auto-saves to `data/processed/`). |

---

## Architectural & Domain Invariants

This codebase strictly adheres to the engineering standards detailed in `docs/`:

1. **The Arithmetic Principle:** Only direct mathematical operands are numeric (`Int32`, `Float64`). Identifiers, state/route codes, years, and dates are strictly zero-padded `pl.String`. `pl.Object` is completely banned.
2. **Tool Lock:** Data ingestion, transforms, Cartesian zero-filling, and filtering use `polars` exclusively. `pandas` and `geopandas` are restricted strictly to terminal vector layer generation (`spatial.py`).
3. **Zero Schema Inference:** All CSV parsing requires explicit `schema_overrides` mapping dictionaries.
4. **Dynamic Horizon Discovery:** Survey temporal bounds are extracted dynamically from raw survey run records; the 2020 COVID-19 hiatus is automatically excluded without hardcoding date limits.
5. **Deterministic Provenance:** All serialized exports stamp the active Git commit hash, dataset year bounds, and UTC extraction timestamp directly into file and schema headers.

---

## Verification & Testing

The test suite enforces 100% pass rates and zero unhandled warnings:

```bash
uv run pytest -v --tb=short tests/
```
