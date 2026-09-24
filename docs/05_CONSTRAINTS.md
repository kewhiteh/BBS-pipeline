# Negative Boundaries & Non-Functional Constraints (v2.0)
## Track B Tool-Lock, Blast-Radius & Architectural Blacklist

This document enforces strict negative boundaries, banned design patterns, resource ceilings, and blast-radius rules for USGS Breeding Bird Survey (BBS) Pipeline v2.0.

---

## 1. Track B Blast-Radius & File Immutability Constraints

The following baseline files are **STRICTLY READ-ONLY**. Modifying, renaming, deleting, or refactoring these files is a critical violation of Track B governance:

1. `src/bbs_pipeline/client/parser.py`
2. `src/bbs_pipeline/client/sciencebase.py`
3. `src/bbs_pipeline/core/discovery.py`
4. `src/bbs_pipeline/core/zero_fill.py`
5. `src/bbs_pipeline/ingestion/routes.py`
6. `src/bbs_pipeline/processing/aggregation.py`
7. `tests/test_routes_ingestion.py`
8. `tests/test_aggregations.py`

All v2.0 features must be implemented in new modules or isolated additive hooks.

---

## 2. Zero-Disk In-Memory Mandate

- **Strict Ban on Disk Buffering:**
  - Do NOT write uncompressed raw CSVs (`fifty1.csv`, etc.) to `/tmp`, the project root, or disk caches.
  - Do NOT write intermediate Parquet or Arrow files to disk during extraction, filtering, or transformation.
  - Do NOT use SQLite, DuckDB, or embedded database files.
- **Mandatory Memory Staging:**
  - All network payloads from ScienceBase must be ingested via `io.BytesIO`.
  - All zip file unpack operations must stream members directly into Polars via `zipfile.ZipFile.open()`.
  - All vector exports (GeoPackage, GeoJSON, Parquet) must serialize to `io.BytesIO` buffers.

---

## 3. Tool-Lock & Dependency Boundaries

### 3.1 Banned Libraries & Runtimes
- **Pandas Restriction:** `import pandas` is strictly prohibited across all data ingestion, transformations, aggregations, filtering, and zero-filling logic. It is permitted solely inside `src/bbs_pipeline/export/vector.py` as an internal conversion requirement for GeoPandas vector serialization.
- **Banned Spatial Tools:** `arcpy`, `qgis`, `fiona`, `gdal` (standalone C-bindings), `osgeo`.
- **Banned Data Engines:** `dask`, `duckdb`, `pyspark`, `sqlite3`, `modin`.
- **Banned Visual Tools in Pipeline:** `matplotlib`, `seaborn`, `plotly` inside data processing modules (plotting is isolated to GUI).

### 3.2 Banned Design Patterns & Syntaxes
- **Zero Schema Inference:** Using `infer_schema_length > 0` or omitting `infer_schema_length=0` during CSV reads is strictly forbidden.
- **Premature Numeric Casting:** Converting ID columns (`CountryNum`, `StateNum`, `Route`, `AOU`, `RouteDataID`, `RPID`, `Year`) to integer types is forbidden.
- **Silent Exception Swallowing:** Naked `try...except:` or `except Exception: pass` blocks are strictly forbidden. All caught exceptions must re-raise or log full tracebacks to `stderr`.
- **Global Mutable State:** Module-level mutable variables or singletons for storing survey data are prohibited.
- **UI Antipattens:** Do NOT use horizontal pills (`st.pills`) for high-cardinality collections; use `st.multiselect` with composite keys (`f"{RouteKey} - {RouteName}"`).

---

## 4. Zero-Filling & Mathematical Invariants

- **Zero-Filling Scope:**
  - `0` may only be imputed for taxonomic species counts (`Stop1`..`Stop50`).
  - Imputation is valid ONLY for species with documented historical breeding records on the specific route.
  - Imputation is valid ONLY for surveyed stops $1 \dots \text{TotalStops}$.
  - Stops beyond survey completion ($k > \text{TotalStops}$) MUST remain `NULL`.
  - Imputing `0` for weather, wind, temperature, sky, or noise is strictly prohibited.

---

## 5. Linear Referencing & Spatial Geometry Invariants

- **Monotonicity Requirement:** Interpolated stop distances along a route must be strictly non-decreasing.
- **Standardized Measure:** Stop distances are fixed at $(i - 1) \times 0.5\text{ miles}$, terminating at $24.5\text{ miles}$ for Stop 50.
- **Coordinate Precision:** Intermediate spatial calculations must execute in conformal equidistant projected coordinates (`EPSG:5070`); export coordinates must be standardized to `EPSG:4326`.