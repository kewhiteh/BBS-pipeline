"""Polars Ecological Engine for BBS-Pipeline v3.

Implements predicate-first protocol filtering, observer tenure classification,
route continuity ratio filtering, start-point boundary selection, 10-stop/50-stop
temporal harmonization, extirpation-aware zero-filling over each route's historical
species pool, and a-la-carte metadata joins.
"""

from __future__ import annotations

from dataclasses import dataclass

import polars as pl

from bbs_pipeline.contracts import (
    FunnelAuditMetrics,
    GuildRegistryContract,
    JoinDimension,
    ObserverTenureMode,
    ProtocolConfigContract,
    StopAggregationMode,
    TemporalRoutingMode,
)
from bbs_pipeline.spatial.selection import routes_within_boundary

# Canonical BBS subspecies/unidentified/hybrid AOU to parent AOU mappings
SUBSPECIES_PARENT_AOU_MAP: dict[str, str] = {
    "04123": "04120",  # Yellow-shafted Flicker -> Northern Flicker
    "04130": "04120",  # Red-shafted Flicker -> Northern Flicker
    "05660": "05670",  # White-winged Junco -> Dark-eyed Junco
    "05671": "05670",  # Oregon Junco -> Dark-eyed Junco
    "05677": "05670",  # Slate-colored Junco -> Dark-eyed Junco
    "06556": "06550",  # Myrtle Warbler -> Yellow-rumped Warbler
    "06560": "06550",  # Audubon's Warbler -> Yellow-rumped Warbler
}

SPECIES_JOIN_COLS: tuple[str, ...] = (
    "English_Common_Name",
    "Order",
    "Family",
    "Genus",
    "Species",
)

ROUTES_JOIN_COLS: tuple[str, ...] = (
    "StateNum",
    "Route",
    "RouteName",
    "Latitude",
    "Longitude",
    "Stratum",
)

BCR_JOIN_COLS: tuple[str, ...] = ("BCR",)

# One harmonized observation row per survey run (RouteDataID) and species
OBSERVATION_KEYS: tuple[str, ...] = ("RouteDataID", "route_id", "Year", "AOU")

WEATHER_JOIN_COLS: tuple[str, ...] = (
    "RouteDataID",
    "ObsN",
    "RunType",
    "QualityCurrentID",
    "StartTemp",
    "EndTemp",
    "StartWind",
    "EndWind",
    "is_first_year",
    "tenure_tier",
)


@dataclass(frozen=True)
class EcologicalEngineResult:
    """Output bundle from the Polars Ecological Engine."""

    observations_df: pl.DataFrame
    funnel_metrics: FunnelAuditMetrics
    active_joins: list[str]


def _to_lazy(frame: pl.DataFrame | pl.LazyFrame) -> pl.LazyFrame:
    return frame.lazy() if isinstance(frame, pl.DataFrame) else frame


def _match_return_type[FrameT: (pl.DataFrame, pl.LazyFrame)](
    result_lf: pl.LazyFrame,
    original: FrameT,
) -> FrameT:
    if isinstance(original, pl.DataFrame):
        return result_lf.collect()
    return result_lf


def normalize_canonical_keys[FrameT: (pl.DataFrame, pl.LazyFrame)](
    frame: FrameT,
) -> FrameT:
    """Ensure canonical `route_id` ('SS_RRR'), 5-digit `AOU`, and Int64 `Year`."""
    lf = _to_lazy(frame)
    schema_names = set(lf.collect_schema().names())
    exprs: list[pl.Expr] = []

    if "StateNum" in schema_names and "Route" in schema_names:
        exprs.append(
            (
                pl.col("StateNum").cast(pl.Int64).cast(pl.String).str.zfill(2)
                + pl.lit("_")
                + pl.col("Route").cast(pl.Int64).cast(pl.String).str.zfill(3)
            ).alias("route_id")
        )
    elif "route_id" in schema_names:
        exprs.append(pl.col("route_id").cast(pl.String).str.strip_chars().alias("route_id"))

    if "AOU" in schema_names:
        exprs.append(pl.col("AOU").cast(pl.String).str.strip_chars().str.zfill(5).alias("AOU"))

    if "Year" in schema_names:
        exprs.append(pl.col("Year").cast(pl.Int64).alias("Year"))

    if exprs:
        lf = lf.with_columns(exprs)

    return _match_return_type(lf, frame)


def compute_observer_tenure[FrameT: (pl.DataFrame, pl.LazyFrame)](
    weather: FrameT,
    protocol: ProtocolConfigContract,
) -> FrameT:
    """Compute observer tenure metrics across `weather` and apply tenure filter policy."""
    lf = _to_lazy(normalize_canonical_keys(weather))
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

    return _match_return_type(lf, weather)


def filter_target_species[FrameT: (pl.DataFrame, pl.LazyFrame)](
    species: FrameT,
    guilds: GuildRegistryContract,
) -> FrameT:
    """Resolve target species from the `species` lookup table using active guild rules."""
    lf = _to_lazy(normalize_canonical_keys(species))
    schema_names = set(lf.collect_schema().names())

    active_key = guilds.active_guild
    if active_key is not None and active_key in guilds.profiles:
        profile = guilds.profiles[active_key]
        if profile.aou_codes:
            lf = lf.filter(pl.col("AOU").is_in(profile.aou_codes))
        elif profile.english_names and "English_Common_Name" in schema_names:
            lf = lf.filter(pl.col("English_Common_Name").is_in(profile.english_names))
        elif profile.families or profile.orders:
            predicates: list[pl.Expr] = []
            if profile.families and "Family" in schema_names:
                predicates.append(pl.col("Family").is_in(profile.families))
            if profile.orders and "Order" in schema_names:
                predicates.append(pl.col("Order").is_in(profile.orders))
            if predicates:
                combined = predicates[0]
                for expr in predicates[1:]:
                    combined = combined | expr
                lf = lf.filter(combined)

    return _match_return_type(lf, species)


def _null_aware_sum_horizontal(cols: list[str]) -> pl.Expr:
    """Sum across columns, ignoring NULLs, but stay NULL when every input is NULL."""
    exprs = [pl.col(c) for c in cols]
    return (
        pl.when(pl.all_horizontal([e.is_null() for e in exprs]))
        .then(pl.lit(None, dtype=pl.Int64))
        .otherwise(pl.sum_horizontal(exprs).cast(pl.Int64))
    )


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
                bin_exprs.append(_null_aware_sum_horizontal(stop_slice).alias(bin_col))
            elif bin_col in schema_names:
                bin_exprs.append(pl.col(bin_col).cast(pl.Int64).alias(bin_col))
            else:
                bin_exprs.append(pl.lit(None, dtype=pl.Int64).alias(bin_col))
        if bin_exprs:
            lf = lf.with_columns(bin_exprs)

    # Compute route_total
    if present_stops:
        lf = lf.with_columns(_null_aware_sum_horizontal(present_stops).alias("route_total"))
    elif "route_total" in schema_names:
        lf = lf.with_columns(pl.col("route_total").cast(pl.Int64).alias("route_total"))
    elif "StopTotal" in schema_names:
        lf = lf.with_columns(pl.col("StopTotal").cast(pl.Int64).alias("route_total"))
    else:
        lf = lf.with_columns(
            _null_aware_sum_horizontal([f"Count{b * 10}" for b in range(1, 6)]).alias("route_total")
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

    if "StopTotal" in schema_names:
        lf = lf.with_columns(pl.col("StopTotal").cast(pl.Int64).alias("route_total"))
    elif "route_total" in schema_names:
        lf = lf.with_columns(pl.col("route_total").cast(pl.Int64).alias("route_total"))
    else:
        lf = lf.with_columns(_null_aware_sum_horizontal(bin_cols).alias("route_total"))

    return lf


def _apply_subspecies_lumping(
    lf: pl.LazyFrame,
    lump: bool,
) -> pl.LazyFrame:
    """Map subspecies/hybrid AOUs to parent AOUs when enabled."""
    if not lump:
        return lf
    schema_names = set(lf.collect_schema().names())
    if "Parent_AOU" in schema_names:
        lf = lf.with_columns(
            pl.coalesce([pl.col("Parent_AOU"), pl.col("AOU")])
            .cast(pl.String)
            .str.strip_chars()
            .str.zfill(5)
            .alias("AOU")
        )
    lf = lf.with_columns(pl.col("AOU").replace(SUBSPECIES_PARENT_AOU_MAP).alias("AOU"))
    return lf


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

    if routing == TemporalRoutingMode.FIFTY_STOP_1997_PLUS:
        if "fifty_stop" not in tables:
            raise KeyError("Table 'fifty_stop' is required for FIFTY_STOP_1997_PLUS mode.")
        lf = _prepare_50_stop_frame(
            _to_lazy(tables["fifty_stop"]),
            need_bins=need_bins,
            need_raw_stops=need_raw_stops,
        )
        _require_run_id(lf, "fifty_stop")
        lf = _apply_subspecies_lumping(lf, lump_subspecies_and_hybrids)

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
        lf = _prepare_10_stop_frame(_to_lazy(tables["ten_stop"]))
        _require_run_id(lf, "ten_stop")
        lf = _apply_subspecies_lumping(lf, lump_subspecies_and_hybrids)

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
            _to_lazy(tables["fifty_stop"]),
            need_bins=True,
            need_raw_stops=False,
        )
        _require_run_id(fifty_lf, "fifty_stop")
        fifty_lf = _apply_subspecies_lumping(fifty_lf, lump_subspecies_and_hybrids)
        fifty_agg = _aggregate_per_run(fifty_lf, bin_val_cols).with_columns(
            pl.when(pl.col("Year") >= 1997)
            .then(pl.lit(1, dtype=pl.Int64))
            .otherwise(pl.lit(2, dtype=pl.Int64))
            .alias("_source_priority")
        )
        frames.append(fifty_agg)

    if "ten_stop" in tables:
        ten_lf = _prepare_10_stop_frame(_to_lazy(tables["ten_stop"]))
        _require_run_id(ten_lf, "ten_stop")
        ten_lf = _apply_subspecies_lumping(ten_lf, lump_subspecies_and_hybrids)
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
            _to_lazy(tables["fifty_stop"]), need_bins=False, need_raw_stops=False
        )
        frames.append(lf)
    if "ten_stop" in tables:
        frames.append(_prepare_10_stop_frame(_to_lazy(tables["ten_stop"])))
    if not frames:
        raise KeyError("No observation table ('fifty_stop' or 'ten_stop') to build history from.")

    pools = [
        _apply_subspecies_lumping(f, lump_subspecies_and_hybrids)
        .filter(pl.col("route_total") > 0)
        .select(["route_id", "AOU"])
        for f in frames
    ]
    return pl.concat(pools, how="vertical").unique()


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


def run_ecological_engine(
    tables: dict[str, pl.LazyFrame | pl.DataFrame],
    guilds: GuildRegistryContract,
    protocol: ProtocolConfigContract,
    ingress_tier_used: str = "tier1_cache",
) -> EcologicalEngineResult:
    """Execute the end-to-end Polars Ecological Engine pipeline."""
    for req_table in ("routes", "weather", "species"):
        if req_table not in tables:
            raise KeyError(f"Required table '{req_table}' missing from input tables dict.")

    routes_lf = normalize_canonical_keys(_to_lazy(tables["routes"]))
    weather_raw_lf = normalize_canonical_keys(_to_lazy(tables["weather"]))
    species_raw_lf = normalize_canonical_keys(_to_lazy(tables["species"]))

    total_routes_raw = int(routes_lf.select(pl.col("route_id").n_unique()).collect().item())
    total_sampling_events_raw = int(weather_raw_lf.select(pl.len()).collect().item())

    # Step 3: Compute observer tenure across weather and apply tenure mode filter
    weather_tenure_lf = compute_observer_tenure(weather_raw_lf, protocol)

    # Step 4: Join weather with routes and apply protocol predicates
    routes_schema = set(routes_lf.collect_schema().names())
    weather_schema = set(weather_tenure_lf.collect_schema().names())

    route_meta_cols = [
        c
        for c in (
            "route_id",
            "CountryNum",
            "StateNum",
            "Route",
            "RouteName",
            "Latitude",
            "Longitude",
            "Stratum",
            "BCR",
        )
        if c in routes_schema
    ]
    routes_dedup_lf = routes_lf.select(route_meta_cols).unique(subset=["route_id"], keep="first")

    # Avoid duplicate non-key columns during weather + routes join
    overlap_cols = [c for c in route_meta_cols if c != "route_id" and c in weather_schema]
    events_lf = weather_tenure_lf.drop(overlap_cols).join(
        routes_dedup_lf, on="route_id", how="inner"
    )
    events_schema = set(events_lf.collect_schema().names())

    start_yr, end_yr = protocol.year_range
    predicates: list[pl.Expr] = [(pl.col("Year") >= start_yr) & (pl.col("Year") <= end_yr)]
    if protocol.run_types and "RunType" in events_schema:
        predicates.append(pl.col("RunType").cast(pl.Int64).is_in(protocol.run_types))
    if protocol.quality_current_ids and "QualityCurrentID" in events_schema:
        predicates.append(
            pl.col("QualityCurrentID").cast(pl.Int64).is_in(protocol.quality_current_ids)
        )
    if protocol.states and "StateNum" in events_schema:
        predicates.append(pl.col("StateNum").cast(pl.Int64).is_in(protocol.states))
    if protocol.bcrs and "BCR" in events_schema:
        predicates.append(pl.col("BCR").cast(pl.Int64).is_in(protocol.bcrs))
    if protocol.strata and "Stratum" in events_schema:
        predicates.append(pl.col("Stratum").cast(pl.Int64).is_in(protocol.strata))
    if protocol.routes:
        predicates.append(pl.col("route_id").is_in(protocol.routes))
    if protocol.boundary_path is not None:
        inside_ids = routes_within_boundary(routes_dedup_lf.collect(), protocol.boundary_path)
        predicates.append(pl.col("route_id").is_in(inside_ids))

    combined_pred = predicates[0]
    for p in predicates[1:]:
        combined_pred = combined_pred & p

    filtered_events_lf = events_lf.filter(combined_pred)
    protocol_filtered_events = int(filtered_events_lf.select(pl.len()).collect().item())

    # Route Continuity Ratio
    expected_years = protocol.expected_active_years()
    denom = float(max(1, len(expected_years)))

    events_with_continuity_lf = filtered_events_lf.with_columns(
        (pl.col("Year").n_unique().over("route_id").cast(pl.Float64) / pl.lit(denom)).alias(
            "route_continuity_ratio"
        )
    ).filter(pl.col("route_continuity_ratio") >= protocol.min_route_continuity_ratio)

    if "RouteDataID" not in events_schema:
        raise KeyError("Table 'weather' has no 'RouteDataID' column; runs cannot be identified.")
    valid_sampling_events_lf = events_with_continuity_lf.select(
        [
            pl.col("RouteDataID").cast(pl.Int64),
            "route_id",
            "Year",
            "route_continuity_ratio",
        ]
    )
    _assert_one_run_per_route_year(valid_sampling_events_lf)

    continuity_filtered_routes = int(
        valid_sampling_events_lf.select(pl.col("route_id").n_unique()).collect().item()
    )

    # Step 5: Target Species Resolution
    target_species_lf = filter_target_species(species_raw_lf, guilds)
    target_aous_df = (
        target_species_lf.select(["AOU"]).unique(subset=["AOU"], keep="first").collect()
    )
    n_target_species = int(target_aous_df.height)

    lump_flag = True
    if guilds.active_guild is not None and guilds.active_guild in guilds.profiles:
        lump_flag = guilds.profiles[guilds.active_guild].lump_subspecies_and_hybrids

    # Step 6: 10-Stop vs 50-Stop Temporal Harmonization
    harmonized_obs_lf = harmonize_observations(
        tables=tables,
        protocol=protocol,
        lump_subspecies_and_hybrids=lump_flag,
    )
    obs_schema_names = list(harmonized_obs_lf.collect_schema().names())
    count_cols = [c for c in obs_schema_names if c not in OBSERVATION_KEYS]
    run_obs_lf = harmonized_obs_lf.select(["RouteDataID", "AOU", *count_cols])

    # Step 7: Extirpation-Aware Zero-Filling over each route's historical species pool.
    # Observations attach to surviving runs by RouteDataID, so a run removed by any
    # filter contributes nothing on either path.
    if protocol.enable_extirpation_zero_fill:
        pool_lf = historical_species_pool(tables, lump_subspecies_and_hybrids=lump_flag).join(
            target_aous_df.lazy(), on="AOU", how="inner"
        )
        skeleton_lf = valid_sampling_events_lf.join(pool_lf, on="route_id", how="inner")
        matrix_lf = (
            skeleton_lf.join(run_obs_lf, on=["RouteDataID", "AOU"], how="left")
            .with_columns(pl.col("route_total").is_null().alias("is_zero_filled"))
            .with_columns(
                [
                    pl.when(pl.col("is_zero_filled")).then(0).otherwise(pl.col(c)).alias(c)
                    for c in count_cols
                ]
            )
        )
    else:
        matrix_lf = (
            valid_sampling_events_lf.join(run_obs_lf, on="RouteDataID", how="inner")
            .join(target_aous_df.lazy(), on="AOU", how="inner")
            .with_columns(pl.lit(False).alias("is_zero_filled"))
        )

    zero_filled_matrix_rows = int(matrix_lf.select(pl.len()).collect().item())

    final_lf = matrix_lf

    # Step 8: A-La-Carte Joins
    active_joins: list[str] = []
    requested_joins = set(protocol.include_joins)

    if JoinDimension.SPECIES in requested_joins:
        sp_schema = set(target_species_lf.collect_schema().names())
        sp_cols = [c for c in SPECIES_JOIN_COLS if c in sp_schema]
        if sp_cols:
            sp_join_lf = target_species_lf.select(["AOU", *sp_cols]).unique(
                subset=["AOU"], keep="first"
            )
            final_lf = final_lf.join(sp_join_lf, on="AOU", how="left")
        active_joins.append(JoinDimension.SPECIES.value)

    route_cols_to_join: list[str] = []
    if JoinDimension.ROUTES in requested_joins:
        route_cols_to_join.extend([c for c in ROUTES_JOIN_COLS if c in routes_schema])
        active_joins.append(JoinDimension.ROUTES.value)

    if JoinDimension.BCR in requested_joins:
        for c in BCR_JOIN_COLS:
            if c in routes_schema and c not in route_cols_to_join:
                route_cols_to_join.append(c)
        active_joins.append(JoinDimension.BCR.value)

    if route_cols_to_join:
        rt_join_lf = routes_dedup_lf.select(["route_id", *route_cols_to_join])
        final_lf = final_lf.join(rt_join_lf, on="route_id", how="left")

    if JoinDimension.WEATHER in requested_joins:
        wx_schema = set(weather_tenure_lf.collect_schema().names())
        wx_cols = [c for c in WEATHER_JOIN_COLS if c in wx_schema and c != "RouteDataID"]
        if wx_cols:
            wx_join_lf = weather_tenure_lf.select([pl.col("RouteDataID").cast(pl.Int64), *wx_cols])
            final_lf = final_lf.join(wx_join_lf, on="RouteDataID", how="left")
        active_joins.append(JoinDimension.WEATHER.value)

    observations_df = final_lf.sort(["route_id", "Year", "AOU"]).collect()
    final_output_rows = int(observations_df.height)

    funnel_metrics = FunnelAuditMetrics(
        ingress_tier_used=ingress_tier_used,
        total_routes_raw=total_routes_raw,
        total_sampling_events_raw=total_sampling_events_raw,
        protocol_filtered_events=protocol_filtered_events,
        continuity_filtered_routes=continuity_filtered_routes,
        target_species_count=n_target_species,
        zero_filled_matrix_rows=zero_filled_matrix_rows,
        final_output_rows=final_output_rows,
    )

    return EcologicalEngineResult(
        observations_df=observations_df,
        funnel_metrics=funnel_metrics,
        active_joins=active_joins,
    )
