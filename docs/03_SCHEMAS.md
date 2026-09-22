# docs/03_SCHEMAS.md

# Upstream Data Schemas & Transformation Contracts

## 1. Upstream Data Schemas & Strict Polars Type Maps

All tabular ingestion must supply explicit `schema_overrides` dictionaries[cite: 1, 3].

### 1.1 50-Stop Observation Files (`Fifty*.zip` -> `fifty*.csv`)[cite: 3]
```python
FIFTY_STOP_SCHEMA = {
    "RouteDataID": pl.String,
    "CountryNum": pl.String,
    "StateNum": pl.String,
    "Route": pl.String,
    "RPID": pl.String,
    "Year": pl.String,
    "AOU": pl.String,
    **{f"Stop{i}": pl.Int32 for i in range(1, 51)},
}
```

### 1.2 10-Stop Summary Files (`States.zip` -> `{State}.zip` -> `{State}.csv`)[cite: 3]
```python
TEN_STOP_SCHEMA = {
    "RouteDataID": pl.String,
    "CountryNum": pl.String,
    "StateNum": pl.String,
    "Route": pl.String,
    "RPID": pl.String,
    "Year": pl.String,
    "AOU": pl.String,
    "Count10": pl.String,
    "Count20": pl.String,
    "Count30": pl.String,
    "Count40": pl.String,
    "Count50": pl.String,
    "StopTotal": pl.String,
    "SpeciesTotal": pl.String,
}
```

### 1.3 Weather & Operational History (`weather.csv`)[cite: 3]
```python
WEATHER_SCHEMA = {
    "RouteDataID": pl.String,
    "CountryNum": pl.String,
    "StateNum": pl.String,
    "Route": pl.String,
    "RPID": pl.String,
    "Year": pl.String,
    "Month": pl.String,
    "Day": pl.String,
    "ObsN": pl.String,
    "TotalSpp": pl.Int32,
    "StartTemp": pl.Float64,
    "EndTemp": pl.Float64,
    "TempScale": pl.String,
    "StartWind": pl.String,
    "EndWind": pl.String,
    "StartSky": pl.String,
    "EndSky": pl.String,
    "StartTime": pl.String,
    "EndTime": pl.String,
    "Assistant": pl.String,
    "QualityCurrentID": pl.String,
    "RunType": pl.String,
}
```

### 1.4 Vehicle & Noise Records (`VehicleData.csv`)[cite: 3]
```python
VEHICLE_SCHEMA = {
    "RouteDataID": pl.String,
    "CountryNum": pl.String,
    "StateNum": pl.String,
    "Route": pl.String,
    "RPID": pl.String,
    "Year": pl.String,
    "RecordedCar": pl.String,
    **{f"Car{i}": pl.String for i in range(1, 51)},
    **{f"Noise{i}": pl.String for i in range(1, 51)},
}
```

### 1.5 Route Geographic Directory (`routes.csv`)[cite: 3]
```python
ROUTES_SCHEMA = {
    "CountryNum": pl.String,
    "StateNum": pl.String,
    "Route": pl.String,
    "RouteName": pl.String,
    "Active": pl.String,
    "Latitude": pl.Float64,
    "Longitude": pl.Float64,
    "Stratum": pl.String,
    "BCR": pl.String,
    "RouteTypeID": pl.String,
    "RouteTypeDetailID": pl.String,
}
```

### 1.6 Taxonomic Authorities
- `SpeciesList.txt` / `SpeciesList.csv`: `Seq`, `AOU`, `English_Common_Name`, `French_Common_Name`, `Spanish_Common_Name`, `ORDER`, `Family`, `Genus`, `Species` (all typed `pl.String`)[cite: 3].
- `MigrantNonBreeder.zip`: `AOU` codes cast to `pl.String` (fixed 5-digit padding)[cite: 3].
- `guilds.json`: Keyed by 5-digit zero-padded AOU string: `{"breeding_habitat": str, "foraging_guild": str, "migratory_status": str}`[cite: 3].

---

## 2. Mathematical Specifications & Core Algorithms

### 2.1 Composite Route Keying
$$\text{RouteKey} = \text{CountryNum} \parallel \text{"\_"} \parallel \text{StateNum} \parallel \text{"\_"} \parallel \text{Route}$$
[cite: 3]

### 2.2 Dynamic Temporal Horizon & Hiatus Exclusion
$$\text{max\_observed\_year} = \max(\{ \text{int}(y) \mid y \in \text{weather.Year} \})$$
[cite: 3]
$$\mathcal{Y}_{\text{eligible}} = \{ y \in [\text{start\_year}, \min(\text{end\_year}, \text{max\_observed\_year})] \mid y \neq 2020 \}$$
[cite: 3]

### 2.3 Proportional Route Continuity
$$\text{required\_runs} = \left\lceil \vert{}\mathcal{Y}_{\text{eligible}}\vert{} \times \frac{P_{\text{comp}}}{100} \right\rceil$$
[cite: 3]
Exclude routes where $\text{valid\_survey\_runs} < \text{required\_runs}$[cite: 3].

### 2.4 Stop Effort Guard & Imputation Rules
Valid runs must meet $\text{TotalStops} \ge \text{min\_stops}$ (where $\text{min\_stops} \in \{45, 48, 50\}$)[cite: 3].
- $\text{Stop}_1 \dots \text{Stop}_{\text{TotalStops}}$ retain counts or imputed zeros[cite: 3].
- $\text{Stop}_i$ where $i > \text{TotalStops}$ evaluate strictly to `NULL`[cite: 3].

### 2.5 Stop Slicing & Traffic Aggregations
$$\text{SegmentCount} = \sum_{i = \text{start\_stop}}^{\text{end\_stop}} \text{Stop}_i$$
[cite: 3]
$$\text{CarTotal} = \sum_{i=1}^{\text{TotalStops}} \text{Car}_i, \quad \text{CarsPerStop} = \frac{\text{CarTotal}}{\text{TotalStops}}$$
[cite: 3]

### 2.6 Longitudinal Observer Covariates & Kendall Bias
- $\text{CareerSurveysCompleted} = \vert{}\{ y \le \text{CurrentYear} \mid \text{ObsN surveyed any BBS route in year } y \}\vert{}$[cite: 3]
- $\text{RouteTenure} = \vert{}\{ y \le \text{CurrentYear} \mid \text{ObsN surveyed RouteKey in year } y \}\vert{}$[cite: 3]
- $\text{IsFirstYearObserver} = \begin{cases} 1 & \text{if } \text{RouteTenure} = 1 \\ 0 & \text{otherwise} \end{cases}$[cite: 3]

### 2.7 Taxonomic Set-Union Resolver
$$\mathcal{S}_{\text{target}} = \left( \bigcup \mathcal{S}_{\text{guild}} \cup \bigcup \mathcal{S}_{\text{order}} \cup \bigcup \mathcal{S}_{\text{family}} \cup \mathcal{S}_{\text{custom\_species}} \right) \setminus \mathcal{S}_{\text{migrant\_nonbreeder}}$$
[cite: 1, 3]

### 2.8 4-Step Extirpation-Preserving Zero-Filling Engine
1. **Lifetime Route Taxa Identification:** $\mathcal{S}_r = \{ s \in \mathcal{S}_{\text{target}} \mid \exists y \in [1966, \text{max\_year}] \text{ s.t. } \text{Count}(r, y, s) \ge 1 \}$[cite: 3]
2. **Target Survey Runs:** Extract eligible survey run years meeting criteria for route $r$: $\mathcal{Y}_r$[cite: 3].
3. **Cartesian Grid:** Form complete combinations $\mathcal{G}_r = \mathcal{Y}_r \times \mathcal{S}_r$[cite: 3].
4. **Imputation Join:** Left-join $\mathcal{G}_r$ with observed count records[cite: 3]:
   - On match: Emit observed $\text{Stop}_1 \dots \text{Stop}_{50}$ and $\text{SpeciesTotal}$[cite: 3].
   - On missing: Emit $\text{SpeciesTotal} = 0$, $\text{Stop}_1 \dots \text{Stop}_{\text{TotalStops}} = 0$, and $\text{Stop}_i = \text{NULL}$ for $i > \text{TotalStops}$[cite: 3].