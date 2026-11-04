"""Runs: observer tenure, RunType/Quality/year filters, continuity, one run per route-year."""

from __future__ import annotations

from dataclasses import dataclass

import polars as pl

from bbs_pipeline.contracts import ObserverTenureMode, ProtocolConfigContract
from bbs_pipeline.stages.common import match_return_type, normalize_canonical_keys, to_lazy
from bbs_pipeline.stages.routes import RouteSelection


def compute_observer_tenure[FrameT: (pl.DataFrame, pl.LazyFrame)](
    weather: FrameT,
    protocol: ProtocolConfigContract,
) -> FrameT:
    """Compute observer tenure metrics across `weather` and apply tenure filter policy."""
    lf = to_lazy(normalize_canonical_keys(weather))
    schema_names = set(lf.collect_schema().names())

    if "ObsN" not in schema_names:
        lf = lf.with_columns(pl.lit(1, dtype=pl.Int64).alias("ObsN"))

    lf = lf.sort(["route_id", "ObsN", "Year"])

    lf = lf.with_columns(
        [
            pl.col("Year")
            .rank(method="min")
            .over(["route_id", "ObsN"])
            .cast(pl.Int64)
            .alias("route_obs_year_rank"),
            pl.col("Year").n_unique().over("ObsN").cast(pl.Int64).alias("obs_total_years"),
        ]
    ).with_columns(
        [
            (pl.col("route_obs_year_rank") == 1).alias("is_first_year"),
            pl.when(pl.col("obs_total_years") < protocol.observer_min_years)
            .then(pl.lit("novice"))
            .when(pl.col("obs_total_years") >= protocol.veteran_min_years)
            .then(pl.lit("veteran"))
            .otherwise(pl.lit("intermediate"))
            .alias("tenure_tier"),
        ]
    )

    mode = protocol.observer_tenure_mode
    if mode == ObserverTenureMode.DROP_FIRST_YEAR_ON_ROUTE:
        lf = lf.filter(~pl.col("is_first_year"))
    elif mode == ObserverTenureMode.DROP_NOVICE_OBSERVERS:
        lf = lf.filter((pl.col("tenure_tier") != "novice") & (~pl.col("is_first_year")))
    elif mode == ObserverTenureMode.VETERAN_ONLY:
        lf = lf.filter(pl.col("tenure_tier") == "veteran")

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
    tenure_lf: pl.LazyFrame  # weather with tenure columns, after the tenure filter
    runs_lf: pl.LazyFrame  # surviving runs: RouteDataID, route_id, Year, route_continuity_ratio
    total_runs: int
    runs_after_filters: int
    runs_after_continuity: int
    routes_with_runs: int


def select_runs(
    weather_raw_lf: pl.LazyFrame, routes: RouteSelection, protocol: ProtocolConfigContract
) -> RunSelection:
    """Tenure, then run filters on the selected routes, then continuity."""
    total_runs = int(weather_raw_lf.select(pl.len()).collect().item())
    weather_tenure_lf = compute_observer_tenure(weather_raw_lf, protocol)
    weather_schema = set(weather_tenure_lf.collect_schema().names())

    # Avoid duplicate non-key columns during weather + routes join
    overlap_cols = [c for c in routes.meta_columns if c != "route_id" and c in weather_schema]
    events_lf = weather_tenure_lf.drop(overlap_cols).join(
        routes.selected_lf, on="route_id", how="inner"
    )
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
        tenure_lf=weather_tenure_lf,
        runs_lf=runs_lf,
        total_runs=total_runs,
        runs_after_filters=runs_after_filters,
        runs_after_continuity=int(runs_lf.select(pl.len()).collect().item()),
        routes_with_runs=int(runs_lf.select(pl.col("route_id").n_unique()).collect().item()),
    )
