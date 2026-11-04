"""Observations: harmonize 10-/50-stop counts per run, historical pool, zero-fill, rolling."""

from __future__ import annotations

import polars as pl

from bbs_pipeline.contracts import ProtocolConfigContract, StopAggregationMode, TemporalRoutingMode
from bbs_pipeline.stages.common import (
    OBSERVATION_KEYS,
    normalize_canonical_keys,
    null_aware_sum_horizontal,
    to_lazy,
)
from bbs_pipeline.stages.species import subspecies_lump_map


def _null_aware_sum(col: str) -> pl.Expr:
    """Group sum that stays NULL when every value in the group is NULL."""
    return (
        pl.when(pl.col(col).is_null().all())
        .then(pl.lit(None, dtype=pl.Int64))
        .otherwise(pl.col(col).sum())
        .alias(col)
    )


def _require_run_id(lf: pl.LazyFrame, table_name: str) -> None:
    if "RouteDataID" not in lf.collect_schema().names():
        raise KeyError(
            f"Table '{table_name}' has no 'RouteDataID' column; observations must be keyed "
            "to individual survey runs."
        )


def _aggregate_per_run(lf: pl.LazyFrame, val_cols: list[str]) -> pl.LazyFrame:
    """Sum counts per (run, species), so subspecies lumped into a parent AOU combine."""
    return (
        lf.with_columns(pl.col("RouteDataID").cast(pl.Int64))
        .group_by(list(OBSERVATION_KEYS))
        .agg([_null_aware_sum(c) for c in val_cols])
        .select([*OBSERVATION_KEYS, *val_cols])
    )


def _prepare_50_stop_frame(
    fifty_stop: pl.LazyFrame,
    need_bins: bool,
    need_raw_stops: bool,
) -> pl.LazyFrame:
    """Normalize a 50-stop LazyFrame to compute 10-stop bins and `route_total`.

    Stop values that are missing stay NULL; they are never treated as zero counts.
    """
    lf = normalize_canonical_keys(fifty_stop)
    schema_names = set(lf.collect_schema().names())

    all_stop_names = [f"Stop{i}" for i in range(1, 51)]
    present_stops = [c for c in all_stop_names if c in schema_names]

    if need_raw_stops:
        missing_stop_exprs = [
            pl.lit(None, dtype=pl.Int64).alias(c) for c in all_stop_names if c not in schema_names
        ]
        if missing_stop_exprs:
            lf = lf.with_columns(missing_stop_exprs)

    cast_stop_exprs = [pl.col(c).cast(pl.Int64).alias(c) for c in present_stops]
    if cast_stop_exprs:
        lf = lf.with_columns(cast_stop_exprs)

    bin_exprs: list[pl.Expr] = []
    if need_bins or not present_stops:
        for b in range(1, 6):
            bin_col = f"Count{b * 10}"
            stop_slice = [
                f"Stop{i}"
                for i in range((b - 1) * 10 + 1, b * 10 + 1)
                if f"Stop{i}" in schema_names
            ]
            if stop_slice:
                bin_exprs.append(null_aware_sum_horizontal(stop_slice).alias(bin_col))
            elif bin_col in schema_names:
                bin_exprs.append(pl.col(bin_col).cast(pl.Int64).alias(bin_col))
            else:
                bin_exprs.append(pl.lit(None, dtype=pl.Int64).alias(bin_col))
        if bin_exprs:
            lf = lf.with_columns(bin_exprs)

    # Compute route_total
    if present_stops:
        lf = lf.with_columns(null_aware_sum_horizontal(present_stops).alias("route_total"))
    elif "route_total" in schema_names:
        lf = lf.with_columns(pl.col("route_total").cast(pl.Int64).alias("route_total"))
    else:
        lf = lf.with_columns(
            null_aware_sum_horizontal([f"Count{b * 10}" for b in range(1, 6)]).alias("route_total")
        )

    return lf


def _prepare_10_stop_frame(ten_stop: pl.LazyFrame) -> pl.LazyFrame:
    """Normalize a 10-stop LazyFrame to ensure `Count10`..`Count50` and `route_total`."""
    lf = normalize_canonical_keys(ten_stop)
    schema_names = set(lf.collect_schema().names())

    bin_cols = [f"Count{b * 10}" for b in range(1, 6)]
    bin_exprs: list[pl.Expr] = []
    for c in bin_cols:
        if c in schema_names:
            bin_exprs.append(pl.col(c).cast(pl.Int64).alias(c))
        else:
            bin_exprs.append(pl.lit(None, dtype=pl.Int64).alias(c))
    lf = lf.with_columns(bin_exprs)

    # SpeciesTotal is the number of birds; StopTotal is only the number of stops with a
    # detection, so it must never be used as abundance.
    if "SpeciesTotal" in schema_names:
        lf = lf.with_columns(pl.col("SpeciesTotal").cast(pl.Int64).alias("route_total"))
    elif "route_total" in schema_names:
        lf = lf.with_columns(pl.col("route_total").cast(pl.Int64).alias("route_total"))
    else:
        lf = lf.with_columns(null_aware_sum_horizontal(bin_cols).alias("route_total"))

    return lf


def _apply_subspecies_lumping(lf: pl.LazyFrame, mapping: dict[str, str]) -> pl.LazyFrame:
    """Replace form AOUs with their species AOU."""
    if not mapping:
        return lf
    return lf.with_columns(pl.col("AOU").replace(mapping).alias("AOU"))


def harmonize_observations(
    tables: dict[str, pl.LazyFrame | pl.DataFrame],
    protocol: ProtocolConfigContract,
    lump_subspecies_and_hybrids: bool = True,
) -> pl.LazyFrame:
    """Harmonize 10-stop and/or 50-stop tables to one row per (run, species).

    Output is keyed on ``OBSERVATION_KEYS`` (``RouteDataID``, ``route_id``, ``Year``,
    ``AOU``). Counts from different runs in the same route-year are never combined.
    """
    routing = protocol.temporal_routing_mode
    agg_mode = protocol.stop_aggregation

    if agg_mode == StopAggregationMode.RAW_50_STOPS and routing != (
        TemporalRoutingMode.FIFTY_STOP_1997_PLUS
    ):
        raise ValueError(
            f"stop_aggregation '{agg_mode.value}' needs per-stop counts, which only the "
            f"50-stop data has; temporal_routing_mode '{routing.value}' cannot provide them."
        )

    need_bins = (
        agg_mode == StopAggregationMode.TEN_STOP_BINS
        or routing == TemporalRoutingMode.HYBRID_HARMONIZED_10_STOP
    )
    need_raw_stops = agg_mode == StopAggregationMode.RAW_50_STOPS
    mapping = subspecies_lump_map(tables, lump_subspecies_and_hybrids)

    if routing == TemporalRoutingMode.FIFTY_STOP_1997_PLUS:
        if "fifty_stop" not in tables:
            raise KeyError("Table 'fifty_stop' is required for FIFTY_STOP_1997_PLUS mode.")
        lf = _prepare_50_stop_frame(
            to_lazy(tables["fifty_stop"]),
            need_bins=need_bins,
            need_raw_stops=need_raw_stops,
        )
        _require_run_id(lf, "fifty_stop")
        lf = _apply_subspecies_lumping(lf, mapping)

        if agg_mode == StopAggregationMode.RAW_50_STOPS:
            val_cols = [*[f"Stop{i}" for i in range(1, 51)], "route_total"]
        elif agg_mode == StopAggregationMode.TEN_STOP_BINS:
            val_cols = [*[f"Count{b * 10}" for b in range(1, 6)], "route_total"]
        else:
            val_cols = ["route_total"]

        return _aggregate_per_run(lf, val_cols)

    if routing == TemporalRoutingMode.TEN_STOP_1966_PLUS:
        if "ten_stop" not in tables:
            raise KeyError("Table 'ten_stop' is required for TEN_STOP_1966_PLUS mode.")
        lf = _prepare_10_stop_frame(to_lazy(tables["ten_stop"]))
        _require_run_id(lf, "ten_stop")
        lf = _apply_subspecies_lumping(lf, mapping)

        if agg_mode == StopAggregationMode.TEN_STOP_BINS:
            val_cols = [*[f"Count{b * 10}" for b in range(1, 6)], "route_total"]
        else:
            val_cols = ["route_total"]

        return _aggregate_per_run(lf, val_cols)

    # HYBRID_HARMONIZED_10_STOP
    bin_val_cols = [*[f"Count{b * 10}" for b in range(1, 6)], "route_total"]
    frames: list[pl.LazyFrame] = []

    if "fifty_stop" in tables:
        fifty_lf = _prepare_50_stop_frame(
            to_lazy(tables["fifty_stop"]),
            need_bins=True,
            need_raw_stops=False,
        )
        _require_run_id(fifty_lf, "fifty_stop")
        fifty_lf = _apply_subspecies_lumping(fifty_lf, mapping)
        fifty_agg = _aggregate_per_run(fifty_lf, bin_val_cols).with_columns(
            pl.when(pl.col("Year") >= 1997)
            .then(pl.lit(1, dtype=pl.Int64))
            .otherwise(pl.lit(2, dtype=pl.Int64))
            .alias("_source_priority")
        )
        frames.append(fifty_agg)

    if "ten_stop" in tables:
        ten_lf = _prepare_10_stop_frame(to_lazy(tables["ten_stop"]))
        _require_run_id(ten_lf, "ten_stop")
        ten_lf = _apply_subspecies_lumping(ten_lf, mapping)
        ten_agg = _aggregate_per_run(ten_lf, bin_val_cols).with_columns(
            pl.when(pl.col("Year") < 1997)
            .then(pl.lit(1, dtype=pl.Int64))
            .otherwise(pl.lit(2, dtype=pl.Int64))
            .alias("_source_priority")
        )
        frames.append(ten_agg)

    if not frames:
        raise KeyError(
            "At least one of 'fifty_stop' or 'ten_stop' is required for HYBRID_HARMONIZED_10_STOP."
        )

    combined = pl.concat(frames, how="vertical")
    deduped = (
        combined.sort([*OBSERVATION_KEYS, "_source_priority"])
        .unique(subset=["RouteDataID", "AOU"], keep="first", maintain_order=True)
        .drop("_source_priority")
    )

    if agg_mode == StopAggregationMode.ROUTE_TOTAL:
        return deduped.select([*OBSERVATION_KEYS, "route_total"])
    return deduped.select([*OBSERVATION_KEYS, *bin_val_cols])


def add_rolling_mean(lf: pl.LazyFrame, years: int) -> pl.LazyFrame:
    """Add rolling_mean and rolling_n_years over the ``years`` calendar years ending at Year.

    The window is calendar years, not rows: a route surveyed in 2010 and 2014 has a
    2014 window of 2010-2014 (5 years) holding 2 surveyed years.
    """
    rolled = (
        lf.select(["route_id", "AOU", "Year", "route_total"])
        .sort("Year")
        .rolling(index_column="Year", period=f"{years}i", group_by=["route_id", "AOU"])
        .agg(
            pl.col("route_total").cast(pl.Float64).mean().alias("rolling_mean"),
            pl.col("route_total").count().cast(pl.Int64).alias("rolling_n_years"),
        )
    )
    return lf.join(rolled, on=["route_id", "AOU", "Year"], how="left")


def historical_species_pool(
    tables: dict[str, pl.LazyFrame | pl.DataFrame],
    lump_subspecies_and_hybrids: bool = True,
) -> pl.LazyFrame:
    """Return every (route_id, AOU) ever recorded with a positive count, over all years.

    Draws on both the 50-stop and 10-stop tables when present, so species last seen
    before the 50-stop era (1997) still count as part of the route's history.
    """
    frames: list[pl.LazyFrame] = []
    if "fifty_stop" in tables:
        lf = _prepare_50_stop_frame(
            to_lazy(tables["fifty_stop"]), need_bins=False, need_raw_stops=False
        )
        frames.append(lf)
    if "ten_stop" in tables:
        frames.append(_prepare_10_stop_frame(to_lazy(tables["ten_stop"])))
    if not frames:
        raise KeyError("No observation table ('fifty_stop' or 'ten_stop') to build history from.")

    mapping = subspecies_lump_map(tables, lump_subspecies_and_hybrids)
    pools = [
        _apply_subspecies_lumping(f, mapping)
        .filter(pl.col("route_total") > 0)
        .select(["route_id", "AOU"])
        for f in frames
    ]
    return pl.concat(pools, how="vertical").unique()


def build_matrix(
    tables: dict[str, pl.LazyFrame | pl.DataFrame],
    runs_lf: pl.LazyFrame,
    run_obs_lf: pl.LazyFrame,
    count_cols: list[str],
    target_aous_df: pl.DataFrame,
    protocol: ProtocolConfigContract,
) -> pl.LazyFrame:
    """One row per surviving run and target species.

    Zero-fill covers each route's historical species pool. Observations attach to
    surviving runs by RouteDataID, so a run removed by any filter contributes nothing on
    either path.
    """
    if protocol.enable_extirpation_zero_fill:
        pool_lf = historical_species_pool(
            tables, lump_subspecies_and_hybrids=protocol.lump_subspecies_and_hybrids
        ).join(target_aous_df.lazy(), on="AOU", how="inner")
        skeleton_lf = runs_lf.join(pool_lf, on="route_id", how="inner")
        return (
            skeleton_lf.join(run_obs_lf, on=["RouteDataID", "AOU"], how="left")
            .with_columns(pl.col("route_total").is_null().alias("is_zero_filled"))
            .with_columns(
                [
                    pl.when(pl.col("is_zero_filled")).then(0).otherwise(pl.col(c)).alias(c)
                    for c in count_cols
                ]
            )
        )
    return (
        runs_lf.join(run_obs_lf, on="RouteDataID", how="inner")
        .join(target_aous_df.lazy(), on="AOU", how="inner")
        .with_columns(pl.lit(False).alias("is_zero_filled"))
    )
