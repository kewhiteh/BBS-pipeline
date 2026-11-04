"""Long (one row per run and species) or wide (one row per route and species) output."""

from __future__ import annotations

import polars as pl

from bbs_pipeline.engine.ecology import BCR_JOIN_COLS, ROUTES_JOIN_COLS, SPECIES_JOIN_COLS

# Columns that are constant for a route and species, so they survive the pivot. Everything
# else (run ids, weather, per-run counts and flags) varies by year and is dropped.
WIDE_INDEX_COLUMNS: tuple[str, ...] = (
    "route_id",
    *ROUTES_JOIN_COLS,
    *BCR_JOIN_COLS,
    "route_continuity_ratio",
    "AOU",
    *SPECIES_JOIN_COLS,
)


def to_wide(df: pl.DataFrame, value: str) -> pl.DataFrame:
    """Pivot to one row per route and species with a ``{value}_{year}`` column per year.

    Only years with at least one surveyed run get a column. A blank cell means the route
    was not surveyed that year (or the run did not survive the filters); 0 means surveyed
    and not detected.
    """
    if value not in df.columns:
        raise KeyError(f"output.wide_value '{value}' is not an output column.")
    index = [c for c in WIDE_INDEX_COLUMNS if c in df.columns]
    if df.is_empty():
        return df.select(index)
    wide = df.sort("Year").pivot(on="Year", index=index, values=value, aggregate_function=None)
    years = [c for c in wide.columns if c not in index]
    return wide.rename({y: f"{value}_{y}" for y in years}).sort(["route_id", "AOU"])
