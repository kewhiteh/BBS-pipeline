"""Long (one row per run and species) or wide (one row per route and species) output."""

from __future__ import annotations

import polars as pl


def to_wide(df: pl.DataFrame, value: str, index: list[str]) -> pl.DataFrame:
    """Pivot to one row per route and species with a ``{value}_{year}`` column per year.

    ``index`` lists the columns that are constant per route or species (route id and
    joined route columns, AOU and joined species/guild columns); every other column varies
    by run and is dropped. Only years with at least one surveyed run get a column. A blank
    cell means the route was not surveyed that year (or the run did not survive the
    filters); 0 means surveyed and not detected.
    """
    if value not in df.columns:
        raise KeyError(f"output.wide_value '{value}' is not an output column.")
    index = [c for c in dict.fromkeys(index) if c in df.columns]
    if df.is_empty():
        return df.select(index)
    wide = df.sort("Year").pivot(on="Year", index=index, values=value, aggregate_function=None)
    years = [c for c in wide.columns if c not in index]
    return wide.rename({y: f"{value}_{y}" for y in years}).sort(["route_id", "AOU"])
