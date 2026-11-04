"""Helpers shared by the stages: lazy frames, canonical keys, NULL-aware sums."""

from __future__ import annotations

import polars as pl

# First year with 50-stop (per-stop) counts
FIFTY_STOP_FIRST_YEAR = 1997

# One harmonized observation row per survey run (RouteDataID) and species
OBSERVATION_KEYS: tuple[str, ...] = ("RouteDataID", "route_id", "Year", "AOU")


def to_lazy(frame: pl.DataFrame | pl.LazyFrame) -> pl.LazyFrame:
    return frame.lazy() if isinstance(frame, pl.DataFrame) else frame


def match_return_type[FrameT: (pl.DataFrame, pl.LazyFrame)](
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
    lf = to_lazy(frame)
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

    return match_return_type(lf, frame)


def null_aware_sum_horizontal(cols: list[str]) -> pl.Expr:
    """Sum across columns, ignoring NULLs, but stay NULL when every input is NULL."""
    exprs = [pl.col(c) for c in cols]
    return (
        pl.when(pl.all_horizontal([e.is_null() for e in exprs]))
        .then(pl.lit(None, dtype=pl.Int64))
        .otherwise(pl.sum_horizontal(exprs).cast(pl.Int64))
    )
