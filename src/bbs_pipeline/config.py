"""Run profile: one TOML file per project describing the whole extraction.

Relative paths in a profile are resolved against the current working directory.
"""

from __future__ import annotations

import json
import re
import tomllib
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

from bbs_pipeline.contracts import (
    DEFAULT_JOINS,
    FORMAT_BY_SUFFIX,
    GuildSettingsContract,
    OutputContract,
    OverlaySpec,
    PipelineConfigBundle,
    ProtocolConfigContract,
    SpeciesSelectionContract,
    StopAggregationMode,
    TemporalRoutingMode,
    normalize_aou,
    normalize_epsg,
    normalize_overlays,
)

SECTION_ORDER: tuple[str, ...] = (
    "source",
    "routes",
    "runs",
    "observations",
    "species",
    "guilds",
    "joins",
    "output",
)

OBSERVATION_SOURCES: dict[str, TemporalRoutingMode] = {
    "10_stop": TemporalRoutingMode.TEN_STOP_1966_PLUS,
    "hybrid": TemporalRoutingMode.HYBRID_HARMONIZED_10_STOP,
    "50_stop": TemporalRoutingMode.FIFTY_STOP_1997_PLUS,
}
STOP_LAYOUTS: dict[str, StopAggregationMode] = {
    "route_total": StopAggregationMode.ROUTE_TOTAL,
    "ten_stop_bins": StopAggregationMode.TEN_STOP_BINS,
    "stops": StopAggregationMode.RAW_50_STOPS,
}

_ROUTE_ID_RE = re.compile(r"^(\d{1,2})_(\d{1,3})$")


class ProfileError(ValueError):
    """A run profile could not be read or failed validation."""


class _Section(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class SourceSection(_Section):
    release: str = "10.5066/P144YU3S"  # 2026 release, data 1966-2025
    raw_dir: Path = Path("data/raw")
    cache_dir: Path = Path("data/sanitized_cache")
    offline: bool = False
    rebuild_cache: bool = False
    http_timeout_seconds: float = Field(default=30.0, gt=0.0)
    http_retries: int = Field(default=3, ge=0, le=10)
    http_backoff_seconds: float = Field(default=1.0, ge=0.0)


class RoutesSection(_Section):
    states: list[int | str] = Field(default_factory=list)
    bcrs: list[int] = Field(default_factory=list)
    strata: list[int] = Field(default_factory=list)
    route_ids: list[str] = Field(default_factory=list)
    boundary: Path | None = None

    @field_validator("states", mode="before")
    @classmethod
    def _states(cls, v: list[Any]) -> list[int | str]:
        out: list[int | str] = []
        for item in v:
            text = str(item).strip()
            if not text:
                raise ValueError("empty state value")
            out.append(int(text) if text.isdigit() else text)
        return out

    @field_validator("route_ids", mode="before")
    @classmethod
    def _route_ids(cls, v: list[Any]) -> list[str]:
        out: list[str] = []
        for item in v:
            match = _ROUTE_ID_RE.match(str(item).strip())
            if match is None:
                raise ValueError(f"route id must look like '63_001' (StateNum_Route), got '{item}'")
            out.append(f"{int(match.group(1)):02d}_{int(match.group(2)):03d}")
        return out

    @field_validator("boundary", mode="before")
    @classmethod
    def _boundary(cls, v: Any) -> Any:
        return None if v is None or str(v).strip() == "" else v


class RunsSection(_Section):
    start_year: int = 1997
    end_year: int = 2024
    run_types: list[int] = Field(default_factory=lambda: [1])
    quality_current_ids: list[int] = Field(default_factory=lambda: [1])
    min_continuity: float = Field(default=0.0, ge=0.0, le=1.0)
    drop_observer_first_year: bool = False  # USGS start-up effect: first year on a route

    @model_validator(mode="before")
    @classmethod
    def _old_tenure_keys(cls, data: Any) -> Any:
        old = {"observer_tenure", "observer_min_years", "veteran_min_years"}
        if isinstance(data, dict) and old & set(data):
            raise ValueError(
                "runs.observer_tenure, observer_min_years and veteran_min_years were "
                "replaced by drop_observer_first_year = true | false (observer experience "
                "now matches USGS trend analysis: first year on a route or not). For "
                'observer_tenure = "drop_first_year_on_route" use '
                "drop_observer_first_year = true; for include_all, false."
            )
        return data

    @model_validator(mode="after")
    def _years(self) -> RunsSection:
        if not 1966 <= self.start_year <= self.end_year <= 2026:
            raise ValueError(
                f"need 1966 <= start_year <= end_year <= 2026, got {self.start_year}-{self.end_year}"
            )
        return self


class ObservationsSection(_Section):
    source: Literal["50_stop", "10_stop", "hybrid"] = "10_stop"
    stops: Literal["route_total", "ten_stop_bins", "stops"] = "route_total"
    zero_fill: bool = True
    lump_subspecies: bool = True
    rolling_years: int = Field(default=0, ge=0)  # 0 = off; N >= 2 = mean over N calendar years


class SpeciesSection(_Section):
    aous: list[str] = Field(default_factory=list)
    common_names: list[str] = Field(default_factory=list)
    families: list[str] = Field(default_factory=list)
    orders: list[str] = Field(default_factory=list)

    @field_validator("aous", mode="before")
    @classmethod
    def _aous(cls, v: list[Any]) -> list[str]:
        return [normalize_aou(item) for item in v]


class GuildsSection(_Section):
    file: Path | None = None  # "" = no guild file
    filters: dict[str, list[str]] = Field(default_factory=dict)
    unmatched: Literal["report", "error"] = "report"

    @field_validator("file", mode="before")
    @classmethod
    def _file(cls, v: Any) -> Any:
        return None if v is None or str(v).strip() == "" else v

    @field_validator("filters", mode="before")
    @classmethod
    def _filters(cls, v: dict[str, Any]) -> dict[str, list[str]]:
        out: dict[str, list[str]] = {}
        for trait, values in v.items():
            items = values if isinstance(values, list) else [values]
            if not items:
                raise ValueError(f"filter '{trait}' needs at least one value")
            out[trait] = [str(item) for item in items]
        return out


def _join_default(table: str) -> Any:
    return Field(default_factory=lambda: list(DEFAULT_JOINS[table]))


class JoinsSection(_Section):
    """Columns to join per table: [] = none, ["*"] = every column, or a list of names."""

    routes: list[str] = _join_default("routes")
    weather: list[str] = _join_default("weather")
    observer: list[str] = _join_default("observer")
    vehicle: list[str] = _join_default("vehicle")
    species: list[str] = _join_default("species")
    guilds: list[str] = _join_default("guilds")

    @model_validator(mode="before")
    @classmethod
    def _old_tables_key(cls, data: Any) -> Any:
        if isinstance(data, dict) and "tables" in data:
            raise ValueError(
                "joins.tables was replaced by one column list per table, e.g. "
                'routes = ["RouteName", "BCR"], weather = [], species = ["*"] '
                "(see examples/profile_template.toml)"
            )
        return data


class OutputSection(_Section):
    path: Path = Path("output/bbs_observations.parquet")
    crs: str = "EPSG:4326"
    layer_name: str = "bbs_observations"
    overlays: dict[str, OverlaySpec] = Field(default_factory=dict)
    manifest: bool = True
    shape: Literal["long", "wide"] = "long"
    wide_value: Literal["route_total", "rolling_mean"] = "route_total"

    @field_validator("crs", mode="before")
    @classmethod
    def _crs(cls, v: Any) -> str:
        return normalize_epsg(v)

    @field_validator("overlays", mode="before")
    @classmethod
    def _overlays(cls, v: Any) -> Any:
        return normalize_overlays(v)

    @field_validator("path")
    @classmethod
    def _path(cls, v: Path) -> Path:
        if v.suffix.lower() not in FORMAT_BY_SUFFIX:
            raise ValueError(f"extension must be one of {sorted(FORMAT_BY_SUFFIX)}, got '{v.name}'")
        return v


class RunProfile(_Section):
    """One project's extraction. Unknown keys are errors."""

    source: SourceSection = Field(default_factory=SourceSection)
    routes: RoutesSection = Field(default_factory=RoutesSection)
    runs: RunsSection = Field(default_factory=RunsSection)
    observations: ObservationsSection = Field(default_factory=ObservationsSection)
    species: SpeciesSection = Field(default_factory=SpeciesSection)
    guilds: GuildsSection = Field(default_factory=GuildsSection)
    joins: JoinsSection = Field(default_factory=JoinsSection)
    output: OutputSection = Field(default_factory=OutputSection)

    @model_validator(mode="after")
    def _check(self) -> RunProfile:
        # Build the internal settings once so any remaining inconsistency fails at load time
        try:
            self.to_bundle()
        except ValidationError as exc:
            raise ValueError(str(exc)) from None
        return self

    def to_bundle(self) -> PipelineConfigBundle:
        """Convert to the internal settings the engine, sources, and exporter use."""
        src, rt, rn, obs = self.source, self.routes, self.runs, self.observations
        if obs.stops == "stops" and obs.source != "50_stop":
            raise ValueError(
                "observations.stops = 'stops' needs per-stop counts, which only "
                "observations.source = '50_stop' has"
            )
        if obs.source == "50_stop" and rn.start_year < 1997:
            raise ValueError(
                f"observations.source = '50_stop' starts in 1997 but runs.start_year is "
                f"{rn.start_year}; use '10_stop' or 'hybrid' for earlier years"
            )
        if obs.rolling_years == 1:
            raise ValueError("observations.rolling_years must be 0 (off) or at least 2")
        if obs.rolling_years and not obs.zero_fill:
            raise ValueError(
                "observations.rolling_years needs observations.zero_fill = true; without "
                "zeros the mean would cover only years the species was detected"
            )
        if self.guilds.file is None and (
            self.guilds.filters or self.joins.guilds not in ([], ["*"])
        ):
            raise ValueError("guilds.filters and joins.guilds column names need guilds.file")
        if self.output.wide_value == "rolling_mean" and not obs.rolling_years:
            raise ValueError("output.wide_value = 'rolling_mean' needs observations.rolling_years")
        protocol = ProtocolConfigContract(
            sanitized_cache_dir=src.cache_dir,
            raw_data_dir=src.raw_dir,
            sciencebase_doi=src.release,
            offline=src.offline,
            force_resanitize=src.rebuild_cache,
            http_timeout_seconds=src.http_timeout_seconds,
            http_max_retries=src.http_retries,
            http_backoff_seconds=src.http_backoff_seconds,
            states=rt.states,
            bcrs=rt.bcrs,
            strata=rt.strata,
            routes=rt.route_ids,
            boundary_path=rt.boundary,
            year_range=(rn.start_year, rn.end_year),
            run_types=rn.run_types,
            quality_current_ids=rn.quality_current_ids,
            min_route_continuity_ratio=rn.min_continuity,
            drop_observer_first_year=rn.drop_observer_first_year,
            temporal_routing_mode=OBSERVATION_SOURCES[obs.source],
            stop_aggregation=STOP_LAYOUTS[obs.stops],
            enable_extirpation_zero_fill=obs.zero_fill,
            lump_subspecies_and_hybrids=obs.lump_subspecies,
            rolling_window_years=obs.rolling_years,
            join_columns=self.joins.model_dump(),
        )
        species = SpeciesSelectionContract(**self.species.model_dump())
        guilds = GuildSettingsContract(**self.guilds.model_dump())
        output = OutputContract(**self.output.model_dump())
        return PipelineConfigBundle(
            species=species, guilds=guilds, protocol=protocol, output=output
        )


def _format_validation_error(exc: ValidationError, origin: str) -> str:
    lines = [f"Invalid run profile {origin}:"]
    for err in exc.errors():
        key = ".".join(str(part) for part in err["loc"]) or "(profile)"
        message = err["msg"]
        if err["type"] == "extra_forbidden":
            message = "unknown key"
        lines.append(f"  {key}: {message}")
    return "\n".join(lines)


def parse_override(assignment: str) -> tuple[list[str], Any]:
    """Split 'runs.start_year=2000' into (['runs', 'start_year'], 2000).

    The value is read as a TOML value (numbers, booleans, quoted strings, arrays);
    anything that is not valid TOML is taken as a plain string, so states=NC works.
    """
    key, sep, raw = assignment.partition("=")
    keys = [k.strip() for k in key.split(".")]
    if not sep or not all(keys):
        raise ProfileError(f"--set expects dotted.key=value, got '{assignment}'")
    try:
        value = tomllib.loads(f"v = {raw.strip()}")["v"]
    except tomllib.TOMLDecodeError:
        value = raw.strip()
    return keys, value


def apply_overrides(data: dict[str, Any], assignments: list[str]) -> dict[str, Any]:
    """Return a copy of raw profile data with --set assignments applied."""
    merged = json.loads(json.dumps(data))
    for assignment in assignments:
        keys, value = parse_override(assignment)
        node = merged
        for k in keys[:-1]:
            child = node.setdefault(k, {})
            if not isinstance(child, dict):
                raise ProfileError(f"--set {assignment}: '{k}' is not a section")
            node = child
        node[keys[-1]] = value
    return merged


def profile_from_data(data: dict[str, Any], origin: str = "(data)") -> RunProfile:
    try:
        return RunProfile.model_validate(data)
    except ValidationError as exc:
        raise ProfileError(_format_validation_error(exc, origin)) from None


def load_profile(path: Path, overrides: list[str] | None = None) -> RunProfile:
    """Read a TOML run profile, apply --set overrides, and validate it."""
    try:
        data = tomllib.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise ProfileError(f"Run profile not found: '{path}'") from None
    except tomllib.TOMLDecodeError as exc:
        raise ProfileError(f"Run profile '{path}' is not valid TOML: {exc}") from None
    return profile_from_data(apply_overrides(data, overrides or []), origin=f"'{path}'")


def _toml_value(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int | float):
        return repr(value)
    if isinstance(value, str):
        return json.dumps(value, ensure_ascii=False)
    if isinstance(value, list):
        return "[" + ", ".join(_toml_value(v) for v in value) + "]"
    if isinstance(value, dict):
        inner = ", ".join(f"{json.dumps(k)} = {_toml_value(v)}" for k, v in value.items())
        return "{" + (f" {inner} " if inner else "") + "}"
    raise TypeError(f"Cannot write {type(value).__name__} to TOML")


def dump_profile(profile: RunProfile) -> str:
    """Render a profile as TOML (every key, in section order)."""
    data = profile.model_dump(mode="json")
    blocks: list[str] = []
    for section in SECTION_ORDER:
        lines = [f"[{section}]"]
        for key, value in data[section].items():
            lines.append(f"{key} = {_toml_value('' if value is None else value)}")
        blocks.append("\n".join(lines))
    return "\n\n".join(blocks) + "\n"


def save_profile(profile: RunProfile, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(dump_profile(profile), encoding="utf-8")
