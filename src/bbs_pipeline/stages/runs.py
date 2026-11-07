"""Runs: observer first year, RunType/Quality/year filters, continuity, one run per route-year."""

from __future__ import annotations

from dataclasses import dataclass

import polars as pl

from bbs_pipeline.contracts import ProtocolConfigContract
from bbs_pipeline.stages.common import match_return_type, normalize_canonical_keys, to_lazy
from bbs_pipeline.stages.routes import RouteSelection


def flag_observer_first_year[FrameT: (pl.DataFrame, pl.LazyFrame)](
    weather: FrameT,
    protocol: ProtocolConfigContract,
) -> FrameT:
    """Add ``is_first_year`` and, if asked, drop those runs.

    A run is a first year when it is in the earliest year the observer (ObsN) surveyed
    that route anywhere in the release: the start-up effect USGS trend analyses model
    (Kendall, Peterjohn & Sauer 1996; Link & Sauer 2002). Experience is route-specific.
    """
    lf = to_lazy(normalize_canonical_keys(weather))
    if "ObsN" not in lf.collect_schema().names():
        lf = lf.with_columns(pl.lit(1, dtype=pl.Int64).alias("ObsN"))

    lf = lf.sort(["route_id", "ObsN", "Year"]).with_columns(
        (pl.col("Year") == pl.col("Year").min().over(["route_id", "ObsN"])).alias("is_first_year")
    )
    if protocol.drop_observer_first_year:
        lf = lf.filter(~pl.col("is_first_year"))
    return match_return_type(lf, weather)


def _assert_one_run_per_route_year(runs_lf: pl.LazyFrame) -> None:
    """Raise if more than one run survives the filters for any route-year."""
    dupes = (
        runs_lf.group_by(["route_id", "Year"])
        .agg(pl.col("RouteDataID").sort().alias("RouteDataIDs"))
        .filter(pl.col("RouteDataIDs").list.len() > 1)
        .sort(["route_id", "Year"])
        .collect()
    )
    if dupes.height:
        examples = "; ".join(
            f"{r['route_id']} {r['Year']}: {r['RouteDataIDs']}"
            for r in dupes.head(5).iter_rows(named=True)
        )
        raise ValueError(
            f"{dupes.height} route-year(s) have more than one run after filtering "
            f"(e.g. {examples}). Narrow run_types/quality_current_ids so one run remains."
        )


@dataclass(frozen=True)
class RunSelection:
    weather_lf: pl.LazyFrame  # weather with is_first_year, after the first-year filter
    runs_lf: pl.LazyFrame  # surviving runs: RouteDataID, route_id, Year, route_continuity_ratio
    total_runs: int
    runs_after_filters: int
    runs_after_continuity: int
    routes_with_runs: int


def select_runs(
    weather_raw_lf: pl.LazyFrame, routes: RouteSelection, protocol: ProtocolConfigContract
) -> RunSelection:
    """First-year flag, then run filters on the selected routes, then continuity."""
    total_runs = int(weather_raw_lf.select(pl.len()).collect().item())
    weather_lf = flag_observer_first_year(weather_raw_lf, protocol)
    weather_schema = set(weather_lf.collect_schema().names())

    # Avoid duplicate non-key columns during weather + routes join
    overlap_cols = [c for c in routes.meta_columns if c != "route_id" and c in weather_schema]
    events_lf = weather_lf.drop(overlap_cols).join(routes.selected_lf, on="route_id", how="inner")
    events_schema = set(events_lf.collect_schema().names())

    def _run_column(name: str, setting: str) -> pl.Expr:
        if name not in events_schema:
            raise KeyError(f"{setting} filter needs column '{name}' in weather.csv.")
        return pl.col(name).cast(pl.Int64)

    start_yr, end_yr = protocol.year_range
    predicates: list[pl.Expr] = [(pl.col("Year") >= start_yr) & (pl.col("Year") <= end_yr)]
    if protocol.run_types:
        predicates.append(_run_column("RunType", "run_types").is_in(protocol.run_types))
    if protocol.quality_current_ids:
        predicates.append(
            _run_column("QualityCurrentID", "quality_current_ids").is_in(
                protocol.quality_current_ids
            )
        )
    filtered_events_lf = events_lf.filter(pl.all_horizontal(predicates))
    runs_after_filters = int(filtered_events_lf.select(pl.len()).collect().item())

    # Route continuity: share of non-cancelled years in range with a surviving run
    denom = float(max(1, len(protocol.expected_active_years())))
    events_with_continuity_lf = filtered_events_lf.with_columns(
        (pl.col("Year").n_unique().over("route_id").cast(pl.Float64) / pl.lit(denom)).alias(
            "route_continuity_ratio"
        )
    ).filter(pl.col("route_continuity_ratio") >= protocol.min_route_continuity_ratio)

    if "RouteDataID" not in events_schema:
        raise KeyError("Table 'weather' has no 'RouteDataID' column; runs cannot be identified.")
    runs_lf = events_with_continuity_lf.select(
        [pl.col("RouteDataID").cast(pl.Int64), "route_id", "Year", "route_continuity_ratio"]
    )
    _assert_one_run_per_route_year(runs_lf)
    return RunSelection(
        weather_lf=weather_lf,
        runs_lf=runs_lf,
        total_runs=total_runs,
        runs_after_filters=runs_after_filters,
        runs_after_continuity=int(runs_lf.select(pl.len()).collect().item()),
        routes_with_runs=int(runs_lf.select(pl.col("route_id").n_unique()).collect().item()),
    )
