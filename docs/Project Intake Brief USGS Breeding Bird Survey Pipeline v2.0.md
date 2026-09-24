# Project Intake Brief: USGS Breeding Bird Survey (BBS) Pipeline v2.0 (Track B Additive Upgrade)

## 1. Upstream Data Contracts, Schemas & Provenance
- Source Format & Payload Structure:
  * 50-Stop Observation Records (`50-StopData.zip` containing `fifty1.csv` through `fifty10.csv`):
    `RouteDataID,CountryNum,StateNum,Route,RPID,Year,AOU,Stop1,Stop2,Stop3,Stop4,Stop5,Stop6,Stop7,Stop8,Stop9,Stop10,Stop11,Stop12,Stop13,Stop14,Stop15,Stop16,Stop17,Stop18,Stop19,Stop20,Stop21,Stop22,Stop23,Stop24,Stop25,Stop26,Stop27,Stop28,Stop29,Stop30,Stop31,Stop32,Stop33,Stop34,Stop35,Stop36,Stop37,Stop38,Stop39,Stop40,Stop41,Stop42,Stop43,Stop44,Stop45,Stop46,Stop47,Stop48,Stop49,Stop50`
  * Route Geographic & Operational Attributes (`routes.csv`):
    `CountryNum,StateNum,Route,RouteName,Active,Latitude,Longitude,Stratum,BCR,RouteTypeID,RouteTypeDetailID`
  * Route GIS Line Geometries (Continuous Route Polylines):
    `CountryNum,StateNum,Route,geometry` (LineString in NAD83 / EPSG:4269 or WGS84 / EPSG:4326)
  * Spatial Boundary Filter Inputs:
    `GeoJSON / WKT Polygon / Bounding Box Coordinates [min_lon, min_lat, max_lon, max_lat]`
  * Baseline v1.0 Authority Contracts (Preserved Read-Only):
    - 10-Stop Summaries (`States.zip` -> `{State}.csv`): `RouteDataID,CountryNum,StateNum,Route,RPID,Year,AOU,Count10..Count50,StopTotal,SpeciesTotal`
    - Weather & Survey History (`weather.csv`): `RouteDataID,CountryNum,StateNum,Route,RPID,Year,Month,Day,ObsN,TotalSpp,StartTemp,EndTemp,TempScale,StartWind,EndWind,StartSky,EndSky,StartTime,EndTime,Assistant,QualityCurrentID,RunType`
    - Automobile & Noise (`VehicleData.csv`): `RouteDataID,CountryNum,StateNum,Route,RPID,Year,RecordedCar,Car1..Car50,Noise1..Noise50`
    - Taxonomic Authority (`SpeciesList.txt` / `SpeciesList.csv`): `Seq,AOU,English_Common_Name,French_Common_Name,Spanish_Common_Name,ORDER,Family,Genus,Species`
    - Migrant Exclusion Authority (`MigrantNonBreeder.zip` -> `Migrants.csv`): `AOU`
    - Ecological Guilds (`guilds.json`): JSON mapping 5-digit AOU strings to `breeding_habitat`, `foraging_guild`, and `migratory_status`

- Asset Acquisition & Provenance Manifest:
  * `50-StopData.zip` (`fifty1.csv`..`fifty10.csv`): Streamed (USGS ScienceBase REST Catalog API via `io.BytesIO` RAM buffer; exponential backoff on HTTP 429, 500, 502, 503, 504)
  * `routes.csv`: Streamed (USGS ScienceBase REST Catalog API via `io.BytesIO` RAM buffer)
  * `weather.csv`: Streamed (USGS ScienceBase REST Catalog API via `io.BytesIO` RAM buffer)
  * `VehicleData.csv`: Streamed (USGS ScienceBase REST Catalog API via `io.BytesIO` RAM buffer)
  * `SpeciesList.txt` / `SpeciesList.csv`: Streamed (USGS ScienceBase REST Catalog API via `io.BytesIO` RAM buffer)
  * `MigrantNonBreeder.zip`: Streamed (USGS ScienceBase REST Catalog API via `io.BytesIO` RAM buffer)
  * `guilds.json`: Pre-Seeded Local (`assets/guilds.json` / `src/bbs_pipeline/assets/guilds.json`, version-controlled static reference)
  * 50-Stop Discrete Point Geometries: Generated (Derived in RAM by `src/bbs_pipeline/processing/linear_referencing.py` via 0.5-mile interval route interpolation)
  * Granular Spatial Masks & Intersections: Generated (Derived in RAM by `src/bbs_pipeline/spatial/filtering.py` via two-tier bounding box and Shapely STRtree point-in-polygon querying)

- Numeric Operands (Math Only):
  * `Stop1` through `Stop50`: `pl.Int32` (Point counts per 3-minute stop; non-strict downstream casting)
  * `StopNumber`: `pl.Int32` (Discrete stop index `1` through `50`)
  * `StopDistanceMiles`: `pl.Float64` (Linear referencing distance: `(StopNumber - 1) * 0.5`, spanning `0.0` to `24.5` miles)
  * `StopDistanceKm`: `pl.Float64` (Metric linear referencing distance: `StopDistanceMiles * 1.609344`, spanning `0.0` to `39.428928` km)
  * `StopLatitude`, `StopLongitude`: `pl.Float64` (Interpolated stop point coordinates in EPSG:4326 / EPSG:4269)
  * `RouteOriginLat`, `RouteOriginLon`: `pl.Float64` (Route starting coordinates from `routes.csv`)
  * `SegmentCount`: `pl.Int32` (Sum of species counts across custom stop slicing intervals)
  * `StopTotal`, `SpeciesTotal`: `pl.Int32` (Route aggregate counts)
  * Retained Baseline Operands: `Count10`..`Count50`, `Car1`..`Car50`, `Noise1`..`Noise50`, `StartTemp`, `EndTemp`, `TotalStops`, `CareerSurveysCompleted`, `RouteTenure`

- String Identifiers & Categoricals:
  * `CountryNum`: `pl.String` (Fixed 3-digit zero-padded, e.g., `"840"`, `"124"`)
  * `StateNum`: `pl.String` (Fixed 2-digit zero-padded, e.g., `"63"`, `"88"`)
  * `Route`: `pl.String` (Fixed 3-digit zero-padded, e.g., `"003"`, `"042"`)
  * `RouteKey`: `pl.String` (Composite key: `CountryNum + "_" + StateNum + "_" + Route`)
  * `StopKey`: `pl.String` (Composite key: `CountryNum + "_" + StateNum + "_" + Route + "_" + StopID_2digit`)
  * `AOU`: `pl.String` (Fixed 5-digit zero-padded, e.g., `"07550"`, `"05980"`)
  * `RouteDataID`: `pl.String` (Unique survey run event key)
  * `RPID`: `pl.String` (Run protocol ID, e.g., `"101"`)
  * `Year`: `pl.String` (Fixed 4-digit string, e.g., `"1995"`, `"2025"`)
  * `StopID`: `pl.String` (Categorical stop label: `"Stop1"` through `"Stop50"`, or normalized `"01"` through `"50"`)
  * `GeometrySource`: `pl.String` (`"route_vector_interpolated"` or `"origin_linear_fallback"`)
  * Retained Baseline Categoricals: `Month`, `Day`, `ObsN`, `Assistant`, `QualityCurrentID`, `RunType`, `Active`, `Stratum`, `BCR`, `RouteTypeID`, `RouteTypeDetailID`, `RouteName`, `ORDER`, `Family`, `Genus`, `Species`, `English_Common_Name`

## 2. Business Logic & Mathematical Specifications
- Core Algorithms / Formulas:
  * 50-Stop Ingestion Engine (`src/bbs_pipeline/ingestion/stops.py`):
    - Function signature: `read_stops_data(stream: io.BytesIO) -> pl.LazyFrame`.
    - Enforce `infer_schema_length=0` with all incoming columns read natively as `pl.String`.
    - Strip whitespace from all CSV headers via `c.strip()`.
    - Apply exhaustive null token mapping: `["", "NA", "null", "NULL", "*", "None"]`.
    - Deferred arithmetic casting: Species counts across `Stop1`–`Stop50` cast to `pl.Int32` via `.cast(pl.Int32, strict=False)` strictly at computation or export points.
    - Zero-filling & effort constraints:
      * Zero imputation (`0`) is strictly applied to confirmed historical breeding species for surveyed stops $1 \dots \text{TotalStops}$ (where $\text{TotalStops} \in \{45..50\}$).
      * Stops beyond route completion ($k > \text{TotalStops}$ up to $50$) must preserve native `NULL` states (never impute `0` for unconducted stops).
  * 50-Stop Linear Referencing (`src/bbs_pipeline/processing/linear_referencing.py`):
    - Transect standard: Fixed 24.5-mile (39.4289 km) route consisting of 50 discrete 3-minute listening stops spaced at 0.5-mile (804.672 m) intervals.
    - Stop measure: For stop $i \in \{1, \dots, 50\}$, cumulative measure $M_i = (i - 1) \times 0.5\text{ miles}$.
      * Stop 1 is anchored at $M_1 = 0.0\text{ mi}$ (matching `routes.csv` origin point).
      * Stop 50 is anchored at $M_{50} = 24.5\text{ mi}$.
    - Interpolation pipeline:
      1. Project continuous route line geometry from geographic coordinates (NAD83 / EPSG:4269) into a conformal equidistant projection (EPSG:5070 CONUS Albers or regional State Plane).
      2. Interpolate discrete stop point along route polyline: $P_i = \text{line.interpolate}(M_i \times 1609.344)$.
      3. Transform interpolated points back to target geographic/projected CRS (e.g., EPSG:4326).
      4. Validate monotonic distance progression: $\text{distance}(P_1, P_i) \le \text{distance}(P_1, P_{i+1})$.
      5. Geometry Fallback: If route polyline is missing or corrupt, generate estimated stop points along the route start bearing, flagged explicitly as `GeometrySource = "origin_linear_fallback"`.
  * Two-Tier Granular Spatial Filtering (`src/bbs_pipeline/spatial/filtering.py`):
    - Tier 1 (Route Envelope Coarse Filter): Rapid candidate route pruning via bounding box intersection (`[min_x, min_y, max_x, max_y]`) against query boundary using Shapely STRtree.
    - Tier 2 (Stop-Level Point-in-Polygon Fine Filter): Vectorized point-in-polygon evaluation (`shapely.contains(query_polygon, stop_point)`).
    - Spatial modes supported:
      * `STRICT_CONTAINMENT`: Retain route if and only if all surveyed stops ($1 \dots \text{TotalStops}$) fall completely inside the boundary polygon.
      * `ANY_STOP_INTERSECT`: Retain full route if $\ge 1$ stop intersects the polygon.
      * `SPATIAL_STOP_MASK`: Retain route, but mask counts for stops falling outside the polygon to `NULL` (or filter long records to within-boundary stops), recomputing route aggregate totals.
  * Extirpation-Preserving Integration:
    - Stop-level zero-filling preserves the baseline 4-step Cartesian matrix ($Y_r \times S_r \times \text{Stops}_{1..50}$).
    - Taxa not observed historically on route $r$ are strictly excluded from zero-filling on route $r$, preventing geographic distribution leakage.

- Boundary Conditions & Edge Cases:
  * Truncated / Incomplete Surveys: If an active survey event completes fewer than 50 stops (`TotalStops < 50`), stops beyond completion remain strictly `NULL`.
  * Cross-State Route Polylines: Multi-state routes crossing jurisdictional lines must resolve via composite keys (`CountryNum_StateNum_Route`) to prevent incorrect state assignments.
  * RAM Memory Ceiling: Multi-state 50-stop archives (`fifty1.csv` through `fifty10.csv`, ~100MB+ compressed) must stream directly through `io.BytesIO` into Polars LazyFrames with streaming collection. No intermediate files written to disk.
  * Malformed CSV Records: Fail-loud diagnostics on malformed rows or missing headers; non-numeric values in count columns cast to `NULL` via `strict=False` and log diagnostic warnings.

## 3. Approved Toolchain & Domain Profile
- Target Domain Profile:
  Profile A: Data (High-Performance Scientific ETL & Spatial Pipeline)

- Candidate Whitelist:
  * Runtime: Python >=3.10
  * Data Processing & Tables: `polars>=0.20.0,<1.0.0`, `pyarrow>=14.0.0`
  * Spatial & Vector Geometry: `shapely>=2.0.0`, `geopandas>=0.14.0`, `pyproj>=3.6.0`
  * Network & Ingestion: `requests>=2.31.0`, `urllib3>=2.0.0`
  * Reactive Web UI: `streamlit>=1.30.0`
  * Quality, Formatting & Testing: `pytest>=8.0.0`, `requests_mock>=1.11.0`, `ruff>=0.2.0`

- Negative Boundaries:
  * Immutable Baseline Blast Radius (Strictly Read-Only):
    - `src/bbs_pipeline/client/parser.py`
    - `src/bbs_pipeline/client/sciencebase.py`
    - `src/bbs_pipeline/core/discovery.py`
    - `src/bbs_pipeline/core/zero_fill.py`
    - `src/bbs_pipeline/ingestion/routes.py`
    - `src/bbs_pipeline/processing/aggregation.py`
    - Existing baseline test fixtures and suites: `tests/test_routes_ingestion.py`, `tests/test_aggregations.py`
  * Strict Zero-Disk In-Memory Mandate: No raw zip archives, unzipped CSVs, SQLite databases, DuckDB files, or temporary Parquet buffers written to disk. All streaming ETL and spatial calculations must execute in RAM via `io.BytesIO`.
  * Tool Lock: Polars LazyFrame/DataFrame APIs exclusively for all data ingestion, transformations, aggregations, and zero-filling. Pandas is strictly banned except at the terminal vector serialization boundary inside GeoPandas.
  * Zero Schema Inference: Automatic schema inference (`infer_schema_length > 0`) is forbidden for stop ingestion; explicit string ingestion (`infer_schema_length=0`) with explicit downstream typing is enforced.
  * Zero Scaffolding: No git re-initialization, `.gitignore` creation, or directory restructuring. Build directly on top of the established module layout.

## 4. UI & Interaction Layer
- Interface Type:
  Dual Interface: Standalone CLI (`extract_bbs.py` / `cli.py`) and Reactive Web GUI (`run_gui.py` / `gui.py`).

- Commands & Controls:
  * Additive CLI Options:
    - `--include-stops`: Boolean flag enabling stop-level observation ingestion and processing.
    - `--stop-geometry`: Geometry generation mode (`none`, `start_point`, `linear_referenced_50`).
    - `--stop-range <start> <end>`: Slicing bounds for sub-route stops ($1 \le \text{start} \le \text{end} \le 50$).
    - `--spatial-boundary <path_or_geojson>`: Path to spatial polygon file or raw GeoJSON string for area-of-interest filtering.
    - `--spatial-filter-mode`: Filter behavior (`strict_all_stops`, `any_stop`, `mask_outside_stops`).
    - `--shape`: Output tabular structure (`wide` or `long`).
  * Additive Streamlit UI Controls:
    - Dedicated, isolated tab: "📍 Stop-Level Linear Spatial View" in `gui.py` (preserving existing Route Overview and Species Summary tabs).
    - Sub-route range slider: Dual-handle slider for selecting stop intervals ($1 \dots 50$).
    - Interactive Spatial Filter Selector: Polygon file uploader (`.geojson`) and bounding box coordinate inputs.
    - Stop Point Map Viewer: High-resolution map displaying discrete stop points color-coded by detection intensity, with hover metadata (Route Name, Stop Number, Distance along route, Species count).
    - In-Memory Download Controls: `st.download_button` streaming stop-level GeoPackage, GeoJSON, Parquet, or CSV directly from RAM buffers.

- Output Artifacts:
  * Tabular Encodings:
    - `wide`: `RouteDataID, CountryNum, StateNum, Route, Year, AOU, Stop1..Stop50, SpeciesTotal`
    - `long`: `RouteDataID, CountryNum, StateNum, Route, Year, AOU, StopNumber, StopDistanceMiles, Count, Latitude, Longitude`
  * Vector Spatial Formats:
    - OGC GeoPackage (`.gpkg`): Two distinct layers — `routes` (route line / origin points) and `stops` (50 point geometries with observation counts and attributes).
    - RFC 7946 GeoJSON (`.geojson`): FeatureCollection of point geometries with full stop-level attributes.
    - Apache Parquet (`.parquet`): Columnar tables with optional GeoParquet WKB geometry columns.
  * Provenance & Metadata Stamping:
    - Embedded metadata in vector and tabular exports: `pipeline_version="2.0.0"`, `baseline_tag="v1.0.0"`, `git_commit_hash`, `spatial_crs`, `referencing_mode`, and `extraction_timestamp_utc`.