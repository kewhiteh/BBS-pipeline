# Ground-Truth Schemas & Data Contracts (v2.0)
## Upstream Specifications, Internal Representations & Export Contracts

This document establishes the exact schemas, typing contracts, null handling rules, and composite keys for USGS Breeding Bird Survey (BBS) Pipeline v2.0.

---

## 1. Upstream Data Source Contracts (Raw Parser Boundary)

All upstream files are ingested with `infer_schema_length=0`. Every column arrives strictly as `pl.String`.

### 1.1 50-Stop Observation Files (`fifty1.csv` .. `fifty10.csv` in `50-StopData.zip`)
- **Null Tokens:** `["", "NA", "null", "NULL", "*", "None"]`
- **Raw Physical Schema:**
  ```python
  RAW_STOPS_SCHEMA = {
      "RouteDataID": pl.String,
      "CountryNum": pl.String,
      "StateNum": pl.String,
      "Route": pl.String,
      "RPID": pl.String,
      "Year": pl.String,
      "AOU": pl.String,
      # Stop1 through Stop50:
      **{f"Stop{i}": pl.String for i in range(1, 51)},
  }
  ```

### 1.2 Route Attributes (`routes.csv`) [Preserved from v1.0]
- **Raw Physical Schema:**
  ```python
  RAW_ROUTES_SCHEMA = {
      "CountryNum": pl.String,
      "StateNum": pl.String,
      "Route": pl.String,
      "RouteName": pl.String,
      "Active": pl.String,
      "Latitude": pl.String,
      "Longitude": pl.String,
      "Stratum": pl.String,
      "BCR": pl.String,
      "RouteTypeID": pl.String,
      "RouteTypeDetailID": pl.String,
  }
  ```

### 1.3 Route Polyline GIS Geometries
- **Format:** GeoJSON LineString / Shapefile / WKT in NAD83 (EPSG:4269) or WGS84 (EPSG:4326)
- **Raw Schema:**
  ```python
  RAW_ROUTE_GEOMETRY_SCHEMA = {
      "CountryNum": pl.String,
      "StateNum": pl.String,
      "Route": pl.String,
      "geometry": pl.Object,  # Shapely LineString
  }
  ```

---

## 2. Standardized Identifiers & Composite Keys

All identifiers are normalized zero-padded strings to prevent geographic misallocation across state and federal boundaries.

| Field Name | Target Type | Format / Normalization | Example | Description |
| :--- | :--- | :--- | :--- | :--- |
| `CountryNum` | `pl.String` | Fixed 3-digit zero-padded (`.str.zfill(3)`) | `"840"` | Country identifier (USA: 840, CAN: 124, MEX: 484) |
| `StateNum` | `pl.String` | Fixed 2-digit zero-padded (`.str.zfill(2)`) | `"63"` | State / Province / Territory numeric code |
| `Route` | `pl.String` | Fixed 3-digit zero-padded (`.str.zfill(3)`) | `"003"` | Route number within state |
| `RouteKey` | `pl.String` | `CountryNum + "_" + StateNum + "_" + Route` | `"840_63_003"` | Globally unique route identifier |
| `AOU` | `pl.String` | Fixed 5-digit zero-padded (`.str.zfill(5)`) | `"07550"` | American Ornithologists' Union species code |
| `RouteDataID`| `pl.String` | Raw string identifier | `"6300320251"` | Survey run event identifier |
| `RPID` | `pl.String` | Raw string code | `"101"` | Run protocol identifier (101: standard BBS) |
| `Year` | `pl.String` | Fixed 4-digit string | `"2024"` | Survey observation year |
| `StopNumber` | `pl.Int32` | Integer between `1` and `50` | `1` | Discrete stop index along transect |
| `StopID` | `pl.String` | `"Stop" + str(StopNumber)` | `"Stop1"` | Column identifier in wide format |
| `StopKey` | `pl.String` | `RouteKey + "_" + str(StopNumber).zfill(2)` | `"840_63_003_01"` | Globally unique stop identifier |
| `GeometrySource`| `pl.String` | Categorical string | `"route_vector_interpolated"` | Provenance of stop coordinate |

---

## 3. Downstream Typed Schemas (Computation & Analysis)

### 3.1 Stop-Level Long Representation (`STOPS_LONG_SCHEMA`)
Used for spatial modeling, sub-route slicing, and point-in-polygon filtering.

```python
STOPS_LONG_SCHEMA = {
    "RouteDataID": pl.String,
    "CountryNum": pl.String,
    "StateNum": pl.String,
    "Route": pl.String,
    "RouteKey": pl.String,
    "RPID": pl.String,
    "Year": pl.String,
    "AOU": pl.String,
    "StopNumber": pl.Int32,
    "StopID": pl.String,
    "StopKey": pl.String,
    "StopDistanceMiles": pl.Float64,
    "StopDistanceKm": pl.Float64,
    "Count": pl.Int32,  # NULL if stop unconducted (> TotalStops)
    "StopLatitude": pl.Float64,
    "StopLongitude": pl.Float64,
    "GeometrySource": pl.String,
}
```

### 3.2 Stop-Level Wide Representation (`STOPS_WIDE_SCHEMA`)
Used for traditional tabular analysis, export, and matrix operations.

```python
STOPS_WIDE_SCHEMA = {
    "RouteDataID": pl.String,
    "CountryNum": pl.String,
    "StateNum": pl.String,
    "Route": pl.String,
    "RouteKey": pl.String,
    "RPID": pl.String,
    "Year": pl.String,
    "AOU": pl.String,
    # Stop1 through Stop50:
    **{f"Stop{i}": pl.Int32 for i in range(1, 51)},
    "SpeciesTotal": pl.Int32,
    "TotalStops": pl.Int32,
}
```

---

## 4. Linear Referencing & Spatial Mathematical Contracts

### 4.1 Transect Spatial Geometry
- Transect total length: $24.5\text{ miles} = 39.428928\text{ km} = 39,428.928\text{ m}$.
- Stop interval: $0.5\text{ miles} = 804.672\text{ meters}$.
- Stop measure equation:
  $$M_i = (i - 1) \times 0.5\quad \text{for } i \in$$
- Monotonicity condition:
  $$\|P_{i+1} - P_1\| \ge \|P_i - P_1\| \quad \forall i \in$$

### 4.2 Provenance Metadata Contract
Every exported artifact (GeoPackage, GeoJSON, Parquet, CSV) must embed this metadata dictionary:
```json
{
  "pipeline_version": "2.0.0",
  "baseline_tag": "v1.0.0",
  "git_commit_hash": "e.g. 7a3f8c1",
  "spatial_crs": "EPSG:4326",
  "interpolation_crs": "EPSG:5070",
  "referencing_mode": "linear_referenced_50",
  "extraction_timestamp_utc": "2026-09-23T21:48:40Z"
}
```