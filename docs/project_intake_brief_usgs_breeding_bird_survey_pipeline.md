# Project Intake Brief: USGS Breeding Bird Survey (BBS) Pipeline

## 1. Upstream Data Contracts & Schemas
- Source Format & Payload Structure:
  - 50-Stop Observation Files (`Fifty1.zip`..`Fifty10.zip` -> `fifty1.csv`..`fifty10.csv`):
    `RouteDataID,CountryNum,StateNum,Route,RPID,Year,AOU,Stop1..Stop50`
  - 10-Stop Summary Files (`States.zip` -> `{State}.zip` -> `{State}.csv`):
    `RouteDataID,CountryNum,StateNum,Route,RPID,Year,AOU,Count10,Count20,Count30,Count40,Count50,StopTotal,SpeciesTotal`
  - Weather & Survey History (`weather.csv`):
    `RouteDataID,CountryNum,StateNum,Route,RPID,Year,Month,Day,ObsN,TotalSpp,StartTemp,EndTemp,TempScale,StartWind,EndWind,StartSky,EndSky,StartTime,EndTime,Assistant,QualityCurrentID,RunType`
  - Automobile & Noise Monitoring (`VehicleData.csv`):
    `RouteDataID,CountryNum,StateNum,Route,RPID,Year,RecordedCar,Car1..Car50,Noise1..Noise50`
  - Route Geographic & Operational Attributes (`routes.csv`):
    `CountryNum,StateNum,Route,RouteName,Active,Latitude,Longitude,Stratum,BCR,RouteTypeID,RouteTypeDetailID`
  - Taxonomic Authority (`SpeciesList.txt` / `SpeciesList.csv`):
    `Seq,AOU,English_Common_Name,French_Common_Name,Spanish_Common_Name,ORDER,Family,Genus,Species`
  - Migrant & Non-Breeder Authority (`MigrantNonBreeder.zip` -> `Migrants.csv`, `MigrantSummary.csv`):
    Used as an in-memory exclusion set of non-breeding/vagrant AOU codes.
  - Ecological Guild Authority (`guilds.json`):
    Keyed by 5-digit AOU string with fields `breeding_habitat`, `foraging_guild`, and `migratory_status`.
- Numeric Operands (Math Only):
  - `Stop1` through `Stop50`: `pl.Int32` (Discrete 3-minute bird point counts).
  - `Count10`, `Count20`, `Count30`, `Count40`, `Count50`, `StopTotal`, `SpeciesTotal`, `TotalStops`: `pl.Int32` (Stop aggregations).
  - `Car1` through `Car50`: `pl.Int32` (Passing vehicle counts per stop).
  - `Noise1` through `Noise50`: `pl.UInt8` (Binary excessive noise impairment indicators).
  - `Latitude`, `Longitude`: `pl.Float64` (NAD83 route origin coordinates).
  - `StartTemp`, `EndTemp`: `pl.Float64` (Survey start and end ambient temperatures).
  - `BirdsPerStop`, `CarsPerStop`: `pl.Float64` (Derived detection and traffic rates).
  - `CareerSurveysCompleted`, `RouteTenure`: `pl.Int32` (Longitudinal observer metrics).
  - `IsFirstYearObserver`: `pl.UInt8` (Binary Kendall first-year baseline flag).
- String Identifiers & Categoricals:
  - `CountryNum`: `pl.String` (Fixed 3-digit zero-padded: e.g., `"840"`, `"124"`).
  - `StateNum`: `pl.String` (Fixed 2-digit zero-padded: e.g., `"63"`, `"88"`).
  - `Route`: `pl.String` (Fixed 3-digit zero-padded: e.g., `"003"`, `"042"`).
  - `AOU`: `pl.String` (Fixed 5-digit zero-padded: e.g., `"07550"`, `"05980"`).
  - `RouteDataID`, `RPID`, `Year`, `Month`, `Day`, `StartTime`, `EndTime`, `JulianDay`: `pl.String` (Temporal and run keys).
  - `ObsN`, `Assistant`, `QualityCurrentID`, `RunType`, `Active`: `pl.String` (Operational and quality indicators).
  - `Stratum`, `BCR`, `RouteTypeID`, `RouteTypeDetailID`, `RouteName`: `pl.String` (Geographic attributes).
  - `RecordedCar`, `TempScale`, `StartWind`, `EndWind`, `StartSky`, `EndSky`: `pl.String` (Sampling conditions).
  - `Seq`, `English_Common_Name`, `French_Common_Name`, `Spanish_Common_Name`, `ORDER`, `Family`, `Genus`, `Species`: `pl.String` (Taxonomic labels).

## 2. Business Logic & Mathematical Specifications
- Core Algorithms / Formulas:
  - Dynamic Horizon Discovery:
    `max_observed_year = max(int(Year) for Year in weather.csv)`. Upper temporal bound is never hardcoded.
  - COVID-19 Hiatus Exclusion:
    `eligible_years = {y in [start_year, min(end_year, max_observed_year)] | y != "2020"}`.
  - Proportional Route Continuity:
    `required_runs = ceil(|eligible_years| * (min_completeness_pct / 100))`. Drop routes where `valid_runs < required_runs`.
  - Discrete Stop Effort Guards:
    Filter routes by `TotalStops >= min_stops` (where `min_stops` in `{50, 48, 45}`). For valid runs, stops `1..TotalStops` contain counts or imputed zeros; stops `> TotalStops` evaluate strictly to `NULL`.
  - Sub-Route Stop Slicing:
    `SegmentCount = sum(Stop_i for i in [start_stop, end_stop])` where `1 <= start_stop <= end_stop <= 50`.
  - Traffic Rate Metrics:
    `CarTotal = sum(Car_i for i in 1..TotalStops)`; `CarsPerStop = CarTotal / TotalStops`.
  - Observer Covariates & Kendall Bias:
    `CareerSurveysCompleted = |{y <= CurrentYear | ObsN surveyed any BBS route in y}|`.
    `RouteTenure = |{y <= CurrentYear | ObsN surveyed Route R in y}|`.
    `IsFirstYearObserver = 1 if RouteTenure == 1 else 0`.
  - Taxonomic Set-Union Resolver:
    `S_target = S_guild U S_order U S_family U S_custom_species`. If `--all-species` / `--community` is flagged, retain all valid breeding species. Exclude all taxa present in `MigrantNonBreeder.zip`.
  - 4-Step Extirpation-Preserving Zero-Filling Engine:
    1. Scan full lifetime history (1966–present) for confirmed breeding species per route: `S_r = {s in S_target | exists y in [1966, max_year] with C_raw(r, y, s) >= 1}`.
    2. Extract valid survey run years passing quality/continuity filters for route `r`: `Y_r`.
    3. Construct Cartesian grid: `G_r = Y_r x S_r`.
    4. Left-join `G_r` with observed counts: emit observed `Stop1..Stop50` and `SpeciesTotal` on detection; impute `SpeciesTotal = 0` and `Stop1..Stop_TotalStops = 0` (with stops `> TotalStops` as `NULL`) on non-detection.
- Boundary Conditions & Edge Cases:
  - Route Key Collision: Identical route numbers exist across states; routes must always be keyed compositely as `CountryNum + "_" + StateNum + "_" + Route`.
  - Extirpation Integrity: Species never confirmed historically on route `r` are strictly excluded from route `r`'s zero-filled matrix (prevents geographic leakage).
  - Incomplete Transects: If a route has 48 valid stops, `Stop49` and `Stop50` remain `NULL`, not `0`.
  - Network Failure Recovery: HTTP requests employ exponential backoff retry across HTTP status codes 429, 500, 502, 503, 504.

## 3. Approved Toolchain & Domain Profile
- Target Domain Profile:
  Profile A: Data (High-Performance Scientific ETL & Spatial Pipeline).
- Candidate Whitelist:
  - Runtime: Python >=3.10.
  - Core Processing Engine: `polars>=0.20.0,<1.0.0`, `pyarrow>=14.0.0`.
  - Ingestion & Network: `requests>=2.31.0`, `urllib3>=2.0.0`.
  - Spatial & Coordinate Systems: `geopandas>=0.14.0`, `pyproj>=3.6.0`, `shapely>=2.0.0`.
  - Web Interface: `streamlit>=1.30.0`.
  - Testing Framework: `pytest>=8.0.0`, `requests_mock` (or `unittest.mock`).
- Negative Boundaries:
  - Strict Zero-Disk In-Memory Mandate: No raw archives, unpacked CSVs, temporary cache files, intermediate Parquet buffers, or embedded databases (sqlite3, duckdb) may touch physical disk during ingestion, filtering, or transformation. All remote byte streams must process in RAM via `io.BytesIO`.
  - Engine Isolation: Pandas is strictly banned from ingestion, transformation, filtering, and zero-filling routines. Polars LazyFrame/DataFrame APIs must be used exclusively. Pandas/GeoPandas is permitted solely at the final boundary during OGC GeoPackage vector export.
  - Zero Schema Inference: Automatic Polars schema inference is prohibited; all CSV reads must supply explicit `schema_overrides`.
  - Test Isolation: Live HTTP requests during automated testing are strictly forbidden; all tests must execute against synthetic in-memory byte streams.

## 4. UI & Interaction Layer
- Interface Type:
  Dual Interface: Standalone CLI (`extract_bbs.py` / `cli.py`) and Reactive Web GUI (`run_gui.py` / `dashboard.py`).
- Commands & Controls:
  - CLI Flags (`nargs="+"` for multi-value inputs):
    - Spatial: `-s, --state` (e.g., `NC VA SC`), `--bcr` (e.g., `28 27`), `--stratum` (e.g., `04 11`), `--routes` (composite keys).
    - Taxonomic: `--species` (AOU or common names), `--guild` (guild names from `guilds.json`), `--family`, `--order`, `--all-species` / `--community`.
    - Temporal & Phenological: `--start-year`, `--end-year`, `--day-range`, `--months`.
    - Filtering & Slicing: `--stop-range` (sub-route slicing), `--min-completeness-pct` (continuity threshold), `--min-stops` (`50`, `48`, `45`).
    - Operational: `--item-id` (ScienceBase catalog ID override, defaulting to `64ad9c3dd34e70357a292cee`).
    - Output Controls: `--shape` (`wide` or `long`), `--crs` (`EPSG:4326`, `EPSG:5070`, named preset `nc_state_plane` / `EPSG:32119`, or arbitrary EPSG), `--format` (`gpkg`, `geojson`, `parquet`, `csv`), `--output` (target destination).
  - Streamlit GUI Controls:
    - Reactive multi-select pills for States, BCRs, Strata, and candidate Routes.
    - Clade and Guild selectors that dynamically populate and synchronize individual species multi-select pills.
    - Dynamic survey year dual-slider adapting to the discovered dataset temporal horizon.
    - Dual-slider controls for sub-route stop ranges ($1 \dots 50$) and route continuity percentage.
    - Live Map preview displaying 1:1 route starting coordinates.
    - Pure in-memory streaming export via `st.download_button`.
- Output Artifacts:
  - Tabular Shapes:
    - `wide`: Retains `Stop1` through `Stop50` as discrete integer columns.
    - `long`: Unpivots count columns into normalized records (`RouteDataID, RouteKey, Year, AOU, StopNumber, Count`).
  - Spatial Anchoring:
    - Route features anchored strictly 1:1 to starting coordinates (`Latitude`, `Longitude` from `routes.csv` in NAD83). No interpolated stop geometries.
    - Dynamic projection support for `EPSG:4326` (WGS 84), `EPSG:5070` (CONUS Albers), and pre-configured North Carolina State Plane (`EPSG:32119`), alongside arbitrary CRS strings.
  - Serialization Formats:
    - OGC GeoPackage (`.gpkg`), RFC 7946 GeoJSON (`.geojson`), Apache Parquet (`.parquet`), CSV (`.csv`).
  - Provenance Stamping:
    - Embed `pipeline_git_hash`, `dataset_min_year`, `dataset_max_year`, `extraction_timestamp`, and `pipeline_version` directly into layer and file metadata headers.