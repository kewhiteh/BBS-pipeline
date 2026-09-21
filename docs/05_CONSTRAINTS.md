# docs/05_CONSTRAINTS.md

# Negative Boundaries & Domain-Specific Invariants

## 1. Domain Profile A: Negative Boundaries

### 1.1 Strict Zero-Disk In-Memory Mandate
- **No Temporary Files:** Raw downloaded ZIP archives, unzipped CSVs, temporary parquet chunks, DuckDB instances, and SQLite tables are strictly banned from touching physical disks or local storage mounts (`/tmp`, `./cache`, etc.)[cite: 1, 3].
- **Streaming RAM Buffers:** All HTTP network streams must be consumed into `io.BytesIO` objects[cite: 1, 3]. ZipFile operations must read streams directly via context managers (`with zipfile.ZipFile(stream) as z:`)[cite: 1].
- **Export Boundary Only:** Physical file writes are permitted **solely** at the terminal export boundary when `--output` is provided[cite: 1, 3]. In GUI mode, downloads must stream directly to the browser via `st.download_button`[cite: 3].

### 1.2 Toolchain Isolation & Engine Blacklist
- **Pandas Restriction:** The use of `pandas` is prohibited inside all network streaming, parsing, data transformation, taxonomic resolution, and zero-filling modules[cite: 3]. Polars LazyFrame/DataFrame APIs must be used exclusively[cite: 3]. `pandas` and `geopandas` are restricted strictly to `spatial.py` for terminal vector layer creation and GeoPackage packaging[cite: 3].
- **Zero Schema Inference:** Ingesting CSV files via `pl.read_csv()` or `pl.scan_csv()` without explicit `schema_overrides` is strictly forbidden[cite: 1, 3]. Automatic type inference is completely banned[cite: 3].
- **Untyped Columns:** `pl.Object` types are strictly prohibited in all schemas[cite: 1].

### 1.3 The Arithmetic Typing Invariant
- A column is typed as numeric (`Int32`, `Float64`, etc.) **if and only if** it acts as a direct mathematical operand[cite: 1, 3].
- The following columns are identifiers and codes, and **must strictly be typed as zero-padded `pl.String`**[cite: 1, 3]:
  - `CountryNum` (3 digits), `StateNum` (2 digits), `Route` (3 digits), `AOU` (5 digits), `RouteDataID`, `RPID`, `Year`, `Month`, `Day`, `StartTime`, `EndTime`, `JulianDay`, `ObsN`, `Stratum`, `BCR`, `RouteTypeID`, `Active`[cite: 3].

### 1.4 Biological & Methodological Guards
- **Zero Geographic Leakage:** Zero-filling must never generate false-absence records for species that have never been documented on a given route throughout BBS history (1966–present)[cite: 3]. Extirpation-preserving logic is mandatory[cite: 3].
- **Discrete Stop Bounds:** Stops exceeding `TotalStops` for a route (e.g., stops 49 and 50 on a 48-stop route) must evaluate strictly to `NULL`, never `0`[cite: 3].
- **No Interpolated Geometries:** Route spatial features must anchor 1:1 to starting coordinates from `routes.csv`[cite: 3]. Interpolating intermediate stop coordinates is prohibited[cite: 3].

### 1.5 Network & Testing Invariants
- **No Live Network Calls in Tests:** Automated tests (`pytest`) are strictly forbidden from hitting remote ScienceBase endpoints[cite: 3]. All test routines must inject synthetic in-memory byte buffers via `requests_mock` or fixtures[cite: 3].