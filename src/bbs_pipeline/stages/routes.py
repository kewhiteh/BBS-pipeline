"""Routes: state, BCR, stratum, route-id, and boundary filters on the routes table."""

from __future__ import annotations

from dataclasses import dataclass

import polars as pl

from bbs_pipeline.contracts import ProtocolConfigContract
from bbs_pipeline.sources.regions import resolve_states
from bbs_pipeline.spatial.selection import routes_within_boundary
from bbs_pipeline.stages.common import to_lazy

ROUTE_META_COLUMNS: tuple[str, ...] = (
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


@dataclass(frozen=True)
class RouteSelection:
    meta_lf: pl.LazyFrame  # one row per route, ROUTE_META_COLUMNS
    selected_lf: pl.LazyFrame  # meta_lf rows passing the route filters
    meta_columns: list[str]
    total_routes: int
    routes_selected: int


def select_routes(
    routes_lf: pl.LazyFrame,
    tables: dict[str, pl.LazyFrame | pl.DataFrame],
    protocol: ProtocolConfigContract,
) -> RouteSelection:
    """Apply the route filters; a filter on a missing column raises."""
    routes_schema = set(routes_lf.collect_schema().names())
    route_meta_cols = [c for c in ROUTE_META_COLUMNS if c in routes_schema]
    routes_dedup_lf = routes_lf.select(route_meta_cols).unique(subset=["route_id"], keep="first")

    def _route_column(name: str, setting: str) -> pl.Expr:
        if name not in routes_schema:
            raise KeyError(f"{setting} filter needs column '{name}' in routes.csv.")
        return pl.col(name).cast(pl.Int64)

    route_predicates: list[pl.Expr] = [pl.lit(True)]
    if protocol.states:
        regions_df = to_lazy(tables["regions"]).collect() if "regions" in tables else None
        state_nums = resolve_states(protocol.states, regions_df)
        route_predicates.append(_route_column("StateNum", "states").is_in(state_nums))
    if protocol.bcrs:
        route_predicates.append(_route_column("BCR", "bcrs").is_in(protocol.bcrs))
    if protocol.strata:
        route_predicates.append(_route_column("Stratum", "strata").is_in(protocol.strata))
    if protocol.routes:
        route_predicates.append(pl.col("route_id").is_in(protocol.routes))
    if protocol.boundary_path is not None:
        inside_ids = routes_within_boundary(routes_dedup_lf.collect(), protocol.boundary_path)
        route_predicates.append(pl.col("route_id").is_in(inside_ids))
    selected_routes_lf = routes_dedup_lf.filter(pl.all_horizontal(route_predicates))
    return RouteSelection(
        meta_lf=routes_dedup_lf,
        selected_lf=selected_routes_lf,
        meta_columns=route_meta_cols,
        total_routes=int(routes_lf.select(pl.col("route_id").n_unique()).collect().item()),
        routes_selected=int(selected_routes_lf.select(pl.len()).collect().item()),
    )
