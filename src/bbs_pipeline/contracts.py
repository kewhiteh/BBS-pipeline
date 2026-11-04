"""Frozen Pydantic v2 JSON Contracts for BBS-Pipeline v3.

All state between the Streamlit GUI, CLI, and Polars/Shapely Backend flows
strictly through three validated JSON files backed by frozen=True models:
1. config/guilds.json   -> GuildRegistryContract
2. config/protocol.json -> ProtocolConfigContract
3. config/spatial.json  -> SpatialExportConfigContract
"""

from __future__ import annotations

import json
from enum import StrEnum
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class IngressMode(StrEnum):
    """Three-Tier Ingress Gate execution mode."""

    AUTO_THREE_TIER = "auto_three_tier"
    CACHE_ONLY = "cache_only"
    LOCAL_RAW_VIA_INTAKE = "local_raw_via_intake"
    SCIENCEBASE_STREAM = "sciencebase_stream"


class Covid2020Mode(StrEnum):
    """Strategy for handling the 2020 COVID-19 survey cancellation year."""

    EXCLUDE_GAP = "exclude_gap"
    SUBSTITUTE_2019 = "substitute_2019"
    INTERPOLATE_2019_2021 = "interpolate_2019_2021"


class ObserverTenureMode(StrEnum):
    """Observer experience/tenure filtering policy."""

    INCLUDE_ALL = "include_all"
    DROP_FIRST_YEAR_ON_ROUTE = "drop_first_year_on_route"
    DROP_NOVICE_OBSERVERS = "drop_novice_observers"
    VETERAN_ONLY = "veteran_only"


class TemporalRoutingMode(StrEnum):
    """Temporal harmonization mode between 10-stop (1966+) and 50-stop (1997+) data."""

    FIFTY_STOP_1997_PLUS = "50_stop_1997_plus"
    TEN_STOP_1966_PLUS = "10_stop_1966_plus"
    HYBRID_HARMONIZED_10_STOP = "hybrid_harmonized_10_stop"


class StopAggregationMode(StrEnum):
    """Output granularity for route stop counts."""

    ROUTE_TOTAL = "route_total"
    TEN_STOP_BINS = "ten_stop_bins"
    RAW_50_STOPS = "raw_50_stops"


class JoinDimension(StrEnum):
    """Optional dimension tables to join a-la-carte onto observations."""

    SPECIES = "species"
    ROUTES = "routes"
    WEATHER = "weather"
    BCR = "bcr"


class GeometrySource(StrEnum):
    """Primary geometry source for spatial joins."""

    ROUTE_POLYLINES = "route_polylines"
    ROUTE_START_POINTS_FALLBACK = "route_start_points_fallback"


class ExportFormat(StrEnum):
    """Supported vector and tabular output sinks."""

    GEOPARQUET = "geoparquet"
    GPKG = "gpkg"
    FLATGEOBUF = "flatgeobuf"
    GEOJSON = "geojson"
    PARQUET = "parquet"
    CSV = "csv"
    DUCKDB = "duckdb"


class GuildProfileContract(BaseModel):
    """Single ecological guild definition with taxonomic & AOU filters."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    citation: str = Field(
        default="",
        description="Bibliographic citation defining this ecological guild.",
    )
    description: str = Field(
        default="",
        description="Ecological description and habitat affinity notes.",
    )
    aou_codes: list[str] = Field(
        default_factory=list,
        description="Explicit list of 5-digit zero-padded string AOU codes (e.g., '07550').",
    )
    orders: list[str] = Field(
        default_factory=list,
        description="Optional taxonomic Order names to include.",
    )
    families: list[str] = Field(
        default_factory=list,
        description="Optional taxonomic Family names to include.",
    )
    english_names: list[str] = Field(
        default_factory=list,
        description="Optional English common names to include.",
    )
    lump_subspecies_and_hybrids: bool = Field(
        default=True,
        description="Whether to consolidate subspecies/unidentified/hybrid forms into parent species.",
    )

    @field_validator("aou_codes", mode="before")
    @classmethod
    def _normalize_aou_codes(cls, v: list[Any]) -> list[str]:
        normalized: list[str] = []
        for item in v:
            code_str = str(item).strip()
            if not code_str.isdigit():
                raise ValueError(f"AOU code must contain only digits, got '{item}'")
            padded = code_str.zfill(5)
            if len(padded) != 5:
                raise ValueError(f"AOU code must be 5 digits when zero-padded, got '{padded}'")
            normalized.append(padded)
        return normalized


class GuildRegistryContract(BaseModel):
    """Contract for config/guilds.json mapping guild keys to GuildProfileContract."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    active_guild: str | None = Field(
        default="whitcomb_interior_obligates",
        description="Currently selected guild profile key (or None for all profiles/unfiltered).",
    )
    profiles: dict[str, GuildProfileContract] = Field(
        default_factory=dict,
        description="Mapping of guild profile keys to their definitions.",
    )

    @model_validator(mode="before")
    @classmethod
    def _coerce_flat_mapping(cls, data: Any) -> Any:
        if isinstance(data, dict) and "profiles" not in data:
            # Support flat dict of guild profiles for ergonomic loading
            active = data.get("active_guild")
            profiles = {
                k: v
                for k, v in data.items()
                if k != "active_guild" and isinstance(v, (dict, GuildProfileContract))
            }
            if profiles:
                if active is None:
                    active = next(iter(profiles.keys()))
                return {"active_guild": active, "profiles": profiles}
        return data

    @model_validator(mode="after")
    def _validate_active_guild(self) -> GuildRegistryContract:
        if (
            self.active_guild is not None
            and self.profiles
            and self.active_guild not in self.profiles
        ):
            raise ValueError(
                f"active_guild '{self.active_guild}' not found in profiles: {list(self.profiles.keys())}"
            )
        return self

    def save_json(self, path: Path | str) -> None:
        target = Path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(
            json.dumps(self.model_dump(mode="json"), indent=2) + "\n",
            encoding="utf-8",
        )

    @classmethod
    def load_json(cls, path: Path | str) -> GuildRegistryContract:
        raw = json.loads(Path(path).read_text(encoding="utf-8"))
        return cls.model_validate(raw)


class ProtocolConfigContract(BaseModel):
    """Contract for config/protocol.json governing ingress, filtering, COVID gap, and zero-fill."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    ingress_mode: IngressMode = Field(
        default=IngressMode.AUTO_THREE_TIER,
        description="Three-Tier Ingress Gate execution mode.",
    )
    sanitized_cache_dir: Path = Field(
        default=Path("data/sanitized_cache"),
        description="Path to sanitized intake_engine Parquet directory containing intake_manifest.json.",
    )
    raw_data_dir: Path = Field(
        default=Path("data/raw"),
        description="Fallback path to local raw USGS .zip/.csv/.txt files.",
    )
    sciencebase_doi: str = Field(
        default="10.5066/P14SNUV4",
        description="USGS ScienceBase release DOI for remote streaming.",
    )
    http_max_retries: int = Field(
        default=3,
        ge=0,
        le=10,
        description="Maximum HTTP retry attempts for ScienceBase streaming.",
    )
    http_backoff_seconds: float = Field(
        default=1.0,
        ge=0.0,
        description="Initial exponential backoff delay in seconds.",
    )
    http_timeout_seconds: float = Field(
        default=30.0,
        gt=0.0,
        description="HTTP request timeout in seconds.",
    )
    force_resanitize: bool = Field(
        default=False,
        description="Force IntakeEngine.assess() and IntakeEngine.execute_pipeline() rebuild.",
    )
    golden_protocol_preset: bool = Field(
        default=True,
        description="Master toggle enforcing run_types == [1] and quality_current_ids == [1].",
    )
    run_types: list[int] = Field(
        default_factory=lambda: [1],
        description="Permitted USGS RunType codes.",
    )
    quality_current_ids: list[int] = Field(
        default_factory=lambda: [1],
        description="Permitted USGS QualityCurrentID codes.",
    )
    year_range: tuple[int, int] = Field(
        default=(1997, 2024),
        description="Inclusive survey year range (1966 to 2026).",
    )
    states: list[int] = Field(
        default_factory=list,
        description="Filter by numeric USGS StateNum codes (empty = all states).",
    )
    bcrs: list[int] = Field(
        default_factory=list,
        description="Filter by Bird Conservation Region (BCR) integers (empty = all BCRs).",
    )
    strata: list[int] = Field(
        default_factory=list,
        description="Filter by physiographic stratum codes (empty = all strata).",
    )
    routes: list[str] = Field(
        default_factory=list,
        description="Filter by canonical route_id ('SS_RRR') strings (empty = all routes).",
    )
    covid_2020_mode: Covid2020Mode = Field(
        default=Covid2020Mode.EXCLUDE_GAP,
        description="Handling mode for the cancelled 2020 COVID-19 survey year.",
    )
    min_route_continuity_ratio: float = Field(
        default=0.0,
        ge=0.0,
        le=1.0,
        description="Minimum fraction of active calendar years a route must be surveyed (0.0..1.0).",
    )
    rolling_window_years: int | None = Field(
        default=1,
        ge=1,
        description="Optional rolling window length in years (1 or None for unsmoothed annual).",
    )
    observer_tenure_mode: ObserverTenureMode = Field(
        default=ObserverTenureMode.INCLUDE_ALL,
        description="Observer tenure filtering mode.",
    )
    observer_min_years: int = Field(
        default=2,
        ge=1,
        description="Minimum cumulative years for an observer not to be considered a novice.",
    )
    veteran_min_years: int = Field(
        default=5,
        ge=1,
        description="Minimum cumulative years for an observer to be classified as veteran.",
    )
    temporal_routing_mode: TemporalRoutingMode = Field(
        default=TemporalRoutingMode.FIFTY_STOP_1997_PLUS,
        description="Temporal source routing and 10-stop/50-stop harmonization mode.",
    )
    stop_aggregation: StopAggregationMode = Field(
        default=StopAggregationMode.ROUTE_TOTAL,
        description="Output stop aggregation granularity.",
    )
    enable_extirpation_zero_fill: bool = Field(
        default=True,
        description="Cross-join valid sampling events x target species and zero-fill missing observations.",
    )
    include_joins: list[JoinDimension] = Field(
        default_factory=lambda: [
            JoinDimension.SPECIES,
            JoinDimension.ROUTES,
            JoinDimension.WEATHER,
        ],
        description="Dimension tables to stitch a-la-carte onto observations.",
    )

    @model_validator(mode="before")
    @classmethod
    def _apply_golden_preset(cls, data: Any) -> Any:
        if isinstance(data, dict):
            copied = dict(data)
            if copied.get("golden_protocol_preset", True):
                copied["run_types"] = [1]
                copied["quality_current_ids"] = [1]
            return copied
        return data

    @model_validator(mode="after")
    def _validate_year_range(self) -> ProtocolConfigContract:
        start, end = self.year_range
        if start < 1966 or end > 2026 or start > end:
            raise ValueError(
                f"year_range must satisfy 1966 <= start <= end <= 2026, got {self.year_range}"
            )
        return self

    def expected_active_years(self) -> list[int]:
        """Return expected survey years in year_range, excluding 2020 in exclude_gap mode."""
        start, end = self.year_range
        years = list(range(start, end + 1))
        if self.covid_2020_mode == Covid2020Mode.EXCLUDE_GAP and 2020 in years:
            years.remove(2020)
        return years

    def save_json(self, path: Path | str) -> None:
        target = Path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(
            json.dumps(self.model_dump(mode="json"), indent=2) + "\n",
            encoding="utf-8",
        )

    @classmethod
    def load_json(cls, path: Path | str) -> ProtocolConfigContract:
        raw = json.loads(Path(path).read_text(encoding="utf-8"))
        return cls.model_validate(raw)


class SpatialExportConfigContract(BaseModel):
    """Contract for config/spatial.json governing linear referencing, overlays, CRS, and sinks."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    enable_spatial_join: bool = Field(
        default=True,
        description="Attach route/stop geometries and run spatial operations (False = pure tabular).",
    )
    geometry_source: GeometrySource = Field(
        default=GeometrySource.ROUTE_POLYLINES,
        description="Primary geometry representation for routes.",
    )
    route_geometry_path: Path = Field(
        default=Path("data/spatial/bbs_routes.parquet"),
        description="Path to cached USGS BBS route polylines (.parquet/.gpkg/.geojson).",
    )
    agol_fallback_url: str = Field(
        default="https://services.arcgis.com/P3ePLMYs2RVChkJx/arcgis/rest/services/USA_BBS_Routes/FeatureServer/0/query",
        description="Fallback ArcGIS Online Feature Service URL when local route geometries are absent.",
    )
    enable_stop_linear_referencing: bool = Field(
        default=False,
        description="Use Shapely linear referencing (line_interpolate_point / substring) for stops/bins.",
    )
    custom_overlay_paths: dict[str, Path] = Field(
        default_factory=dict,
        description="Named external polygon layers (e.g., watersheds, BCR polygons) for STRtree overlay.",
    )
    target_crs_list: list[str] = Field(
        default_factory=lambda: ["EPSG:4326", "EPSG:5070", "EPSG:32119"],
        description="List of EPSG coordinate reference systems to export.",
    )
    export_formats: list[ExportFormat] = Field(
        default_factory=lambda: [
            ExportFormat.GEOPARQUET,
            ExportFormat.PARQUET,
            ExportFormat.CSV,
        ],
        description="Output vector and tabular sink formats.",
    )
    output_dir: Path = Field(
        default=Path("output"),
        description="Target directory for exported spatial and tabular artifacts.",
    )

    @field_validator("target_crs_list", mode="before")
    @classmethod
    def _normalize_crs_list(cls, v: list[Any]) -> list[str]:
        normalized: list[str] = []
        for item in v:
            crs_str = str(item).strip().upper()
            if crs_str.isdigit():
                crs_str = f"EPSG:{crs_str}"
            if not crs_str.startswith("EPSG:") or not crs_str.split(":", 1)[1].isdigit():
                raise ValueError(f"Invalid EPSG code '{item}'. Expected format 'EPSG:XXXX'.")
            normalized.append(crs_str)
        return normalized

    def save_json(self, path: Path | str) -> None:
        target = Path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(
            json.dumps(self.model_dump(mode="json"), indent=2) + "\n",
            encoding="utf-8",
        )

    @classmethod
    def load_json(cls, path: Path | str) -> SpatialExportConfigContract:
        raw = json.loads(Path(path).read_text(encoding="utf-8"))
        return cls.model_validate(raw)


class FunnelAuditMetrics(BaseModel):
    """Row-count funnel metrics from raw ingress through zero-filled matrix."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    ingress_tier_used: str
    total_routes_raw: int
    total_sampling_events_raw: int
    protocol_filtered_events: int
    continuity_filtered_routes: int
    target_species_count: int
    zero_filled_matrix_rows: int
    final_output_rows: int


class PipelineExecutionResult(BaseModel):
    """Summary artifact emitted by a full end-to-end BBS pipeline run."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    funnel_metrics: FunnelAuditMetrics
    exported_files: list[Path] = Field(default_factory=list)
    active_joins: list[str] = Field(default_factory=list)
    preview_records: list[dict[str, Any]] = Field(default_factory=list)
    guild_summary_records: list[dict[str, Any]] = Field(default_factory=list)


class PipelineConfigBundle(BaseModel):
    """Immutable container bundling all three configuration contracts."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    guilds: GuildRegistryContract = Field(default_factory=GuildRegistryContract)
    protocol: ProtocolConfigContract = Field(default_factory=ProtocolConfigContract)
    spatial: SpatialExportConfigContract = Field(default_factory=SpatialExportConfigContract)

    @classmethod
    def load_from_dir(cls, config_dir: Path | str = Path("config")) -> PipelineConfigBundle:
        base = Path(config_dir)
        return cls(
            guilds=GuildRegistryContract.load_json(base / "guilds.json"),
            protocol=ProtocolConfigContract.load_json(base / "protocol.json"),
            spatial=SpatialExportConfigContract.load_json(base / "spatial.json"),
        )

    def save_to_dir(self, config_dir: Path | str = Path("config")) -> None:
        base = Path(config_dir)
        base.mkdir(parents=True, exist_ok=True)
        self.guilds.save_json(base / "guilds.json")
        self.protocol.save_json(base / "protocol.json")
        self.spatial.save_json(base / "spatial.json")
