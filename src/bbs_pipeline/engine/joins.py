"""Configurable joins: which columns of which table go onto the observation rows.

Each table is a left join on its key. ``["*"]`` takes every joinable column, ``[]``
nothing. A column the table does not have raises with the list of valid names. A right
side with duplicate keys raises, so no join can multiply rows.
"""

from __future__ import annotations

from dataclasses import dataclass

import polars as pl

from bbs_pipeline.contracts import DEFAULT_JOINS, JOIN_TABLES

__all__ = ["DEFAULT_JOINS", "JOIN_TABLES", "JoinSource", "apply_joins", "vehicle_source"]

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
        observations = observations.join(right, on=source.key, how="left")
        present.update(columns)
        joined[table] = columns
    return observations, joined
