"""Run the pipeline stages in order: routes -> runs -> species -> observations -> enrich."""

from __future__ import annotations

from dataclasses import dataclass, field

import polars as pl

from bbs_pipeline.contracts import (
    FunnelAuditMetrics,
    ProtocolConfigContract,
    SpeciesSelectionContract,
    TemporalRoutingMode,
)
from bbs_pipeline.guilds import GuildTable
from bbs_pipeline.stages.common import (
    FIFTY_STOP_FIRST_YEAR,
    OBSERVATION_KEYS,
    normalize_canonical_keys,
    to_lazy,
)
from bbs_pipeline.stages.enrich import enrich, join_sources
from bbs_pipeline.stages.observations import add_rolling_mean, build_matrix, harmonize_observations
from bbs_pipeline.stages.routes import select_routes
from bbs_pipeline.stages.runs import select_runs
from bbs_pipeline.stages.species import apply_guilds, resolve_target_species, subspecies_lump_map
from bbs_pipeline.usage import check_memory

OBSERVATION_TABLES = ("ten_stop", "fifty_stop")
MEMORY_SAMPLE_ROWS = 20_000  # rows sized to estimate the output's bytes per row


@dataclass(frozen=True)
class EcologicalEngineResult:
    """Output bundle from the Polars Ecological Engine."""

    observations_df: pl.DataFrame
    funnel_metrics: FunnelAuditMetrics
    active_joins: list[str]
    # Output columns constant per route or per species (kept by a wide pivot)
    wide_index: list[str] = field(default_factory=list)
    # Species recorded on surviving runs that have no guild row (when a guild file is used)
    species_without_guild: list[dict[str, str]] = field(default_factory=list)


def _observations_for_routes(
    tables: dict[str, pl.LazyFrame | pl.DataFrame], runs_lf: pl.LazyFrame
) -> dict[str, pl.LazyFrame | pl.DataFrame]:
    """The tables, with the observation tables limited to routes that have surviving runs.

    Filters on the raw integer StateNum / Route when present, so the filter applies while
    the table is read rather than after every row's route_id string is built.
    """
    route_ids = runs_lf.select("route_id").unique().collect()["route_id"]
    parts = route_ids.str.split_exact("_", 1).struct.unnest()
    route_keys = (
        parts["field_0"].cast(pl.Int64) * 1000 + parts["field_1"].cast(pl.Int64)
    ).implode()
    limited = dict(tables)
    for name in OBSERVATION_TABLES:
        if name not in tables:
            continue
        lf = to_lazy(tables[name])
        if {"StateNum", "Route"} <= set(lf.collect_schema().names()):
            lf = lf.filter(
                (pl.col("StateNum").cast(pl.Int64) * 1000 + pl.col("Route").cast(pl.Int64)).is_in(
                    route_keys
                )
            )
            limited[name] = normalize_canonical_keys(lf)
        else:
            limited[name] = normalize_canonical_keys(lf).filter(
                pl.col("route_id").is_in(route_ids.implode())
            )
    return limited


def run_ecological_engine(
    tables: dict[str, pl.LazyFrame | pl.DataFrame],
    species: SpeciesSelectionContract,
    protocol: ProtocolConfigContract,
    data_source: str = "cache",
    guilds: GuildTable | None = None,
    guild_filters: dict[str, list[str]] | None = None,
) -> EcologicalEngineResult:
    """Execute the stages and return the observation table with stage counts.

    With ``guilds``, the target species are further limited to those passing
    ``guild_filters`` (AND with the species selection), and recorded species that have
    no guild row are reported.
    """
    if (
        protocol.temporal_routing_mode == TemporalRoutingMode.FIFTY_STOP_1997_PLUS
        and protocol.year_range[0] < FIFTY_STOP_FIRST_YEAR
    ):
        # Runs before 1997 exist but have no 50-stop rows, so zero-fill would invent zeros
        raise ValueError(
            f"50-stop data starts in {FIFTY_STOP_FIRST_YEAR}; years from "
            f"{protocol.year_range[0]} need the 10-stop or hybrid source."
        )
    for req_table in ("routes", "weather", "species"):
        if req_table not in tables:
            raise KeyError(f"Required table '{req_table}' missing from input tables dict.")

    routes_lf = normalize_canonical_keys(to_lazy(tables["routes"]))
    weather_raw_lf = normalize_canonical_keys(to_lazy(tables["weather"]))
    species_raw_lf = normalize_canonical_keys(to_lazy(tables["species"]))

    route_sel = select_routes(routes_lf, tables, protocol)
    run_sel = select_runs(weather_raw_lf, route_sel, protocol)

    mapping = subspecies_lump_map(tables, protocol.lump_subspecies_and_hybrids)
    target_species_lf, target_aous_df = resolve_target_species(species_raw_lf, species, mapping)

    # Observations only for routes that still have runs (memory: grouping the whole
    # release cost ~2 GB even for one state). Zero-fill history is per route, so it is
    # unchanged by this filter.
    obs_tables = _observations_for_routes(tables, run_sel.runs_lf)

    harmonized_obs_lf = harmonize_observations(
        tables=obs_tables,
        protocol=protocol,
        lump_subspecies_and_hybrids=protocol.lump_subspecies_and_hybrids,
    )
    count_cols = [
        c for c in harmonized_obs_lf.collect_schema().names() if c not in OBSERVATION_KEYS
    ]
    run_obs_lf = harmonized_obs_lf.select(["RouteDataID", "AOU", *count_cols])

    guild_stage = apply_guilds(
        guilds,
        guild_filters,
        mapping,
        target_species_lf,
        target_aous_df,
        run_sel.runs_lf,
        run_obs_lf,
    )
    target_aous_df = guild_stage.target_aous_df

    matrix_lf = build_matrix(
        obs_tables, run_sel.runs_lf, run_obs_lf, count_cols, target_aous_df, protocol
    )
    if protocol.rolling_window_years:
        matrix_lf = add_rolling_mean(matrix_lf, protocol.rolling_window_years)
    # Collect the narrow matrix once (it was recomputed for the count, the memory sample
    # and the output) and sort it here: the joins keep this order, so the wide output
    # needs no sort (a second full copy at the peak).
    matrix_df = matrix_lf.sort(["route_id", "Year", "AOU"]).collect()
    zero_filled_matrix_rows = matrix_df.height
    matrix_lf = matrix_df.lazy()

    sources = join_sources(
        routes_lf,
        run_sel.weather_lf,
        target_species_lf,
        tables,
        guild_stage.guild_join_df,
        guilds.traits if guilds is not None else [],
    )
    final_lf, joined, wide_index = enrich(matrix_lf, sources, protocol.join_columns)

    # H13: a full-extent build ran WSL out of memory. Size a sample of the real output
    # and stop before the big collect if it can't fit.
    sample = final_lf.head(MEMORY_SAMPLE_ROWS).collect()
    if sample.height:
        check_memory(zero_filled_matrix_rows, sample.width, sample.estimated_size() / sample.height)
    del sample

    del matrix_df
    observations_df = final_lf.collect()
    final_output_rows = int(observations_df.height)
    if final_output_rows != zero_filled_matrix_rows:
        raise AssertionError(
            f"Joins changed the row count ({zero_filled_matrix_rows} -> {final_output_rows})."
        )

    funnel_metrics = FunnelAuditMetrics(
        data_source=data_source,
        total_routes_raw=route_sel.total_routes,
        total_sampling_events_raw=run_sel.total_runs,
        routes_selected=route_sel.routes_selected,
        protocol_filtered_events=run_sel.runs_after_filters,
        runs_after_continuity=run_sel.runs_after_continuity,
        continuity_filtered_routes=run_sel.routes_with_runs,
        target_species_count=int(target_aous_df.height),
        zero_filled_matrix_rows=zero_filled_matrix_rows,
        final_output_rows=final_output_rows,
    )
    return EcologicalEngineResult(
        observations_df=observations_df,
        funnel_metrics=funnel_metrics,
        active_joins=list(joined),
        species_without_guild=guild_stage.species_without_guild,
        wide_index=wide_index,
    )
