"""Enrich: configurable joins of route, run, species, and guild columns onto observations.

Each table is a left join on its key. ``["*"]`` takes every joinable column, ``[]``
nothing. A column the table does not have raises with the list of valid names. A right
side with duplicate keys raises, so no join can multiply rows.
"""

from __future__ import annotations

from dataclasses import dataclass

import polars as pl

from bbs_pipeline.contracts import JOIN_TABLES
from bbs_pipeline.stages.common import to_lazy

# Route-level and species-level tables: their columns are constant per route or species,
# so they survive a wide pivot
ROUTE_LEVEL_TABLES: frozenset[str] = frozenset({"routes"})
SPECIES_LEVEL_TABLES: frozenset[str] = frozenset({"species", "guilds"})


@dataclass(frozen=True)
class JoinSource:
    """A table that can be joined: the frame, its key, and the columns it offers."""

    frame: pl.LazyFrame
    key: str
    columns: list[str]


def vehicle_source(vehicle: pl.LazyFrame) -> JoinSource:
    """Vehicle table with derived CarTotal and NoiseTotal (NULL when no stop has data)."""
    schema = set(vehicle.collect_schema().names())
    cars = [f"Car{i}" for i in range(1, 51) if f"Car{i}" in schema]
    noise = [f"Noise{i}" for i in range(1, 51) if f"Noise{i}" in schema]

    def total(cols: list[str], name: str) -> pl.Expr:
        exprs = [pl.col(c) for c in cols]
        return (
            pl.when(pl.all_horizontal([e.is_null() for e in exprs]))
            .then(pl.lit(None, dtype=pl.Int64))
            .otherwise(pl.sum_horizontal(exprs))
            .alias(name)
        )

    frame = vehicle.with_columns(
        pl.col("RouteDataID").cast(pl.Int64), total(cars, "CarTotal"), total(noise, "NoiseTotal")
    )
    offered = [
        c
        for c in ("RecordedCar", "CarTotal", "NoiseTotal", *cars, *noise)
        if c in {*schema, "CarTotal", "NoiseTotal"}
    ]
    return JoinSource(frame.select(["RouteDataID", *offered]), "RouteDataID", offered)


def resolve_columns(table: str, requested: list[str], source: JoinSource | None) -> list[str]:
    """Expand ["*"] and check every requested column exists in the table."""
    if not requested:
        return []
    if source is None:
        raise ValueError(f"joins.{table}: this release/profile has no {table} table to join.")
    if requested == ["*"]:
        return list(source.columns)
    if "*" in requested:
        raise ValueError(f'joins.{table}: use ["*"] alone, not mixed with column names.')
    unknown = [c for c in requested if c not in source.columns]
    if unknown:
        raise ValueError(
            f"joins.{table}: no column(s) {unknown}. Valid columns: {', '.join(source.columns)}"
        )
    return list(dict.fromkeys(requested))


def apply_joins(
    observations: pl.LazyFrame,
    sources: dict[str, JoinSource | None],
    spec: dict[str, list[str]],
) -> tuple[pl.LazyFrame, dict[str, list[str]]]:
    """Left-join the requested columns of each table; return the frame and columns joined."""
    present = set(observations.collect_schema().names())
    joined: dict[str, list[str]] = {}
    owner: dict[str, str] = {}
    for table in JOIN_TABLES:
        columns = resolve_columns(table, spec.get(table, []), sources.get(table))
        if spec.get(table) == ["*"]:
            # "all columns" skips a column an earlier table already joins from the *same*
            # frame (ObsN: weather and observer), so the values are identical. A clash with
            # a different frame (a guild trait named Family) or an explicit list still raises
            source = sources[table]
            columns = [
                c
                for c in columns
                if not (
                    c in owner and source is not None and sources[owner[c]].frame is source.frame
                )
            ]
        if not columns:
            continue
        for col in columns:
            if col in owner:
                raise ValueError(
                    f"Column '{col}' is requested from both joins.{owner[col]} and joins.{table}."
                )
            if col in present:
                raise ValueError(f"joins.{table}: column '{col}' is already an output column.")
            owner[col] = table
        source = sources[table]
        assert source is not None
        right = source.frame.select([source.key, *columns])
        dupes = right.group_by(source.key).len().filter(pl.col("len") > 1).collect()
        if dupes.height:
            raise ValueError(
                f"joins.{table}: {dupes.height} duplicate {source.key} value(s) "
                f"(e.g. {dupes[source.key].head(3).to_list()}); the join would multiply rows."
            )
        # maintain_order: the engine sorts the narrow matrix before the joins
        observations = observations.join(right, on=source.key, how="left", maintain_order="left")
        present.update(columns)
        joined[table] = columns
    return observations, joined


RUN_KEY_COLUMNS: frozenset[str] = frozenset(
    {"RouteDataID", "route_id", "CountryNum", "StateNum", "Route", "Year"}
)
OBSERVER_COLUMNS: list[str] = ["ObsN", "is_first_year"]


def join_sources(
    routes_lf: pl.LazyFrame,
    weather_lf: pl.LazyFrame,
    target_species_lf: pl.LazyFrame,
    tables: dict[str, pl.LazyFrame | pl.DataFrame],
    guild_join_df: pl.DataFrame | None,
    guild_traits: list[str],
) -> dict[str, JoinSource | None]:
    """The joinable tables and the columns each offers."""
    weather = weather_lf.with_columns(pl.col("RouteDataID").cast(pl.Int64))
    weather_cols = [
        c
        for c in weather.collect_schema().names()
        if c not in RUN_KEY_COLUMNS and c not in OBSERVER_COLUMNS
    ]
    return {
        "routes": JoinSource(
            routes_lf,
            "route_id",
            [c for c in routes_lf.collect_schema().names() if c != "route_id"],
        ),
        "weather": JoinSource(weather, "RouteDataID", ["ObsN", *weather_cols]),
        "observer": JoinSource(weather, "RouteDataID", OBSERVER_COLUMNS),
        "vehicle": vehicle_source(to_lazy(tables["vehicle"])) if "vehicle" in tables else None,
        "species": JoinSource(
            target_species_lf,
            "AOU",
            [c for c in target_species_lf.collect_schema().names() if c != "AOU"],
        ),
        "guilds": (
            JoinSource(guild_join_df.lazy(), "AOU", list(guild_traits))
            if guild_join_df is not None
            else None
        ),
    }


def enrich(
    observations: pl.LazyFrame,
    sources: dict[str, JoinSource | None],
    spec: dict[str, list[str]],
) -> tuple[pl.LazyFrame, dict[str, list[str]], list[str]]:
    """Apply the joins; return the frame, columns joined per table, and the wide index."""
    spec = dict(spec)
    if sources["guilds"] is None and spec.get("guilds") == ["*"]:
        spec["guilds"] = []  # the default: guild traits when a guild file is given
    observations, joined = apply_joins(observations, sources, spec)
    wide_index = [
        "route_id",
        *(c for t in sorted(ROUTE_LEVEL_TABLES) for c in joined.get(t, [])),
        "route_continuity_ratio",
        "AOU",
        *(c for t in ("species", "guilds") if t in SPECIES_LEVEL_TABLES for c in joined.get(t, [])),
    ]
    return observations, joined, wide_index
