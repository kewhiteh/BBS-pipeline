"""Internal, frozen settings consumed by the engine, sources, and exporter.

Users never write these directly: ``bbs_pipeline.config.RunProfile`` (one TOML file per
project) is validated and converted into a ``PipelineConfigBundle``.
"""

from __future__ import annotations

from enum import StrEnum
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

JOIN_TABLES: tuple[str, ...] = ("routes", "weather", "observer", "vehicle", "species", "guilds")

# Defaults reproduce the columns the pipeline joined before per-table lists existed
DEFAULT_JOINS: dict[str, list[str]] = {
    "routes": ["StateNum", "Route", "RouteName", "Latitude", "Longitude", "Stratum"],
    "weather": [
        "ObsN",
        "RunType",
        "QualityCurrentID",
        "StartTemp",
        "EndTemp",
        "StartWind",
        "EndWind",
    ],
    "observer": ["is_first_year", "tenure_tier"],
    "vehicle": [],
    "species": ["English_Common_Name", "Order", "Family", "Genus", "Species"],
    "guilds": ["*"],
}

# The 2020 survey was cancelled (COVID-19); the release has no 2020 runs
CANCELLED_SURVEY_YEARS: frozenset[int] = frozenset({2020})


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


class ExportFormat(StrEnum):
    """Supported vector and tabular output formats, chosen by the output file extension."""

    GEOPARQUET = "geoparquet"
    GPKG = "gpkg"
    FLATGEOBUF = "flatgeobuf"
    GEOJSON = "geojson"
    PARQUET = "parquet"
    CSV = "csv"


FORMAT_BY_SUFFIX: dict[str, ExportFormat] = {
    ".parquet": ExportFormat.PARQUET,
    ".csv": ExportFormat.CSV,
    ".geoparquet": ExportFormat.GEOPARQUET,
    ".gpkg": ExportFormat.GPKG,
    ".fgb": ExportFormat.FLATGEOBUF,
    ".geojson": ExportFormat.GEOJSON,
}

VECTOR_FORMATS: frozenset[ExportFormat] = frozenset(
    {ExportFormat.GEOPARQUET, ExportFormat.GPKG, ExportFormat.FLATGEOBUF, ExportFormat.GEOJSON}
)


def normalize_aou(value: Any) -> str:
    """Return a 5-digit, zero-padded AOU code; raise on anything else."""
    text = str(value).strip()
    if not text.isdigit() or len(text) > 5:
        raise ValueError(f"AOU code must be 1-5 digits, got '{value}'")
    return text.zfill(5)


def normalize_epsg(value: Any) -> str:
    """Return 'EPSG:XXXX' for '4326', 4326, or 'epsg:4326'; raise on anything else."""
    text = str(value).strip().upper()
    if text.isdigit():
        text = f"EPSG:{text}"
    if not text.startswith("EPSG:") or not text.split(":", 1)[1].isdigit():
        raise ValueError(f"Invalid EPSG code '{value}'. Expected format 'EPSG:XXXX'.")
    return text


class SpeciesSelectionContract(BaseModel):
    """Which species to keep. Selectors combine as a union; all empty keeps every species."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    aous: list[str] = Field(default_factory=list)
    common_names: list[str] = Field(default_factory=list)
    families: list[str] = Field(default_factory=list)
    orders: list[str] = Field(default_factory=list)

    @field_validator("aous", mode="before")
    @classmethod
    def _normalize_aous(cls, v: list[Any]) -> list[str]:
        return [normalize_aou(item) for item in v]

    @property
    def is_empty(self) -> bool:
        return not (self.aous or self.common_names or self.families or self.orders)


class ProtocolConfigContract(BaseModel):
    """Data sources, route/run filters, observation handling, and joins."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    sanitized_cache_dir: Path = Field(
        default=Path("data/sanitized_cache"),
        description="Typed Parquet cache built from raw_data_dir (with cache_manifest.json).",
    )
    raw_data_dir: Path = Field(
        default=Path("data/raw"),
        description="Release files downloaded by `bbs fetch`, with release.json.",
    )
    sciencebase_doi: str = Field(
        default="10.5066/P144YU3S",
        description="BBS release DOI (or ScienceBase item id); default: 2026 release, 1966-2025.",
    )
    offline: bool = Field(
        default=False,
        description="Never use the network; fail if raw_data_dir has no release.json.",
    )
    http_max_retries: int = Field(
        default=3,
        ge=0,
        le=10,
        description="Maximum HTTP retry attempts for ScienceBase downloads.",
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
        description="Rebuild the cache from raw files even if a valid cache exists.",
    )
    run_types: list[int] = Field(
        default_factory=lambda: [1],
        description="Permitted USGS RunType codes (default [1] = acceptable runs; empty = all).",
    )
    quality_current_ids: list[int] = Field(
        default_factory=lambda: [1],
        description="Permitted USGS QualityCurrentID codes (default [1]; empty = all).",
    )
    year_range: tuple[int, int] = Field(
        default=(1997, 2024),
        description="Inclusive survey year range (1966 to 2026).",
    )
    states: list[int | str] = Field(
        default_factory=list,
        description=(
            "Filter by StateNum code, region name, or postal abbreviation, e.g. [63], "
            '["North Carolina"], ["NC"] (empty = all states).'
        ),
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
    boundary_path: Path | None = Field(
        default=None,
        description=(
            "Polygon file (.gpkg/.shp/.geojson/.parquet). Keeps routes whose routes.csv start "
            "point (NAD83) lies inside or on the boundary. None = no spatial filter."
        ),
    )
    min_route_continuity_ratio: float = Field(
        default=0.0,
        ge=0.0,
        le=1.0,
        description="Minimum fraction of active calendar years a route must be surveyed (0.0..1.0).",
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
    lump_subspecies_and_hybrids: bool = Field(
        default=True,
        description="Count subspecies/unidentified/hybrid forms under their parent species.",
    )
    rolling_window_years: int = Field(
        default=0,
        ge=0,
        description=(
            "0 = off. N >= 2 adds rolling_mean (mean route_total over the N calendar years "
            "ending at Year, surveyed years only) and rolling_n_years."
        ),
    )
    enable_extirpation_zero_fill: bool = Field(
        default=True,
        description="Cross-join valid sampling events x target species and zero-fill missing observations.",
    )
    join_columns: dict[str, list[str]] = Field(
        default_factory=lambda: {k: list(v) for k, v in DEFAULT_JOINS.items()},
        description='Columns to join per table ([] = none, ["*"] = all); see engine.joins.',
    )

    @field_validator("join_columns")
    @classmethod
    def _join_tables(cls, v: dict[str, list[str]]) -> dict[str, list[str]]:
        unknown = sorted(set(v) - set(JOIN_TABLES))
        if unknown:
            raise ValueError(f"unknown join table(s) {unknown}; tables are {list(JOIN_TABLES)}")
        return {**{t: [] for t in JOIN_TABLES}, **v}

    @field_validator("rolling_window_years")
    @classmethod
    def _rolling(cls, v: int) -> int:
        if v == 1:
            raise ValueError("rolling_window_years must be 0 (off) or at least 2")
        return v

    @field_validator("states", mode="before")
    @classmethod
    def _normalize_states(cls, v: list[Any]) -> list[int | str]:
        normalized: list[int | str] = []
        for item in v:
            text = str(item).strip()
            if not text:
                raise ValueError("Empty state value.")
            normalized.append(int(text) if text.isdigit() else text)
        return normalized

    @model_validator(mode="after")
    def _validate_year_range(self) -> ProtocolConfigContract:
        start, end = self.year_range
        if start < 1966 or end > 2026 or start > end:
            raise ValueError(
                f"year_range must satisfy 1966 <= start <= end <= 2026, got {self.year_range}"
            )
        return self

    def expected_active_years(self) -> list[int]:
        """Return expected survey years in year_range, excluding cancelled survey years."""
        start, end = self.year_range
        return [y for y in range(start, end + 1) if y not in CANCELLED_SURVEY_YEARS]


class GuildSettingsContract(BaseModel):
    """Guild CSV and trait filters (OR within a trait, AND across traits)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    file: Path | None = None
    filters: dict[str, list[str]] = Field(default_factory=dict)
    unmatched: Literal["report", "error"] = "report"


class OutputContract(BaseModel):
    """One output file; its format comes from the file extension."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    path: Path = Field(default=Path("output/bbs_observations.parquet"))
    crs: str = Field(default="EPSG:4326", description="CRS for vector formats.")
    layer_name: str = Field(default="bbs_observations", description="GeoPackage layer name.")
    overlays: dict[str, Path] = Field(
        default_factory=dict,
        description="Named polygon layers whose attribute is attached to each route (vector).",
    )
    manifest: bool = Field(default=True, description="Write <path>.manifest.json.")
    shape: Literal["long", "wide"] = Field(
        default="long",
        description="long: one row per run and species; wide: one row per route and species.",
    )
    wide_value: Literal["route_total", "rolling_mean"] = Field(
        default="route_total", description="Value spread into one column per year when wide."
    )

    @field_validator("path")
    @classmethod
    def _known_suffix(cls, v: Path) -> Path:
        if v.suffix.lower() not in FORMAT_BY_SUFFIX:
            raise ValueError(
                f"Unknown output extension '{v.suffix}'; use one of {sorted(FORMAT_BY_SUFFIX)}"
            )
        return v

    @field_validator("crs", mode="before")
    @classmethod
    def _normalize_crs(cls, v: Any) -> str:
        return normalize_epsg(v)

    @property
    def format(self) -> ExportFormat:
        return FORMAT_BY_SUFFIX[self.path.suffix.lower()]

    @property
    def is_vector(self) -> bool:
        return self.format in VECTOR_FORMATS


class FunnelAuditMetrics(BaseModel):
    """Counts after each pipeline stage, from the raw release to the output table."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    data_source: str
    total_routes_raw: int
    total_sampling_events_raw: int
    routes_selected: int
    protocol_filtered_events: int
    runs_after_continuity: int
    continuity_filtered_routes: int
    target_species_count: int
    zero_filled_matrix_rows: int
    final_output_rows: int


class PipelineExecutionResult(BaseModel):
    """Summary of one pipeline run (or inspection, when nothing is exported)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    funnel_metrics: FunnelAuditMetrics
    output_columns: list[str] = Field(default_factory=list)
    output_rows: int = 0
    exported_files: list[Path] = Field(default_factory=list)
    active_joins: list[str] = Field(default_factory=list)
    preview_records: list[dict[str, Any]] = Field(default_factory=list)
    guild_report: dict[str, Any] | None = None


class PipelineConfigBundle(BaseModel):
    """All internal settings for one run."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    species: SpeciesSelectionContract = Field(default_factory=SpeciesSelectionContract)
    guilds: GuildSettingsContract = Field(default_factory=GuildSettingsContract)
    protocol: ProtocolConfigContract = Field(default_factory=ProtocolConfigContract)
    output: OutputContract = Field(default_factory=OutputContract)
