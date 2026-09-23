"""Traffic rate and noise covariates for the USGS BBS Pipeline.

Implements §2.5 (traffic portion) of docs/03_SCHEMAS.md:

    CarTotal    = Σ Car_i  for i = 1 .. TotalStops
    CarsPerStop = CarTotal / TotalStops

Profile A Invariant & Section 1 / Section 9 Ingestion Isolation:
All stop columns ('Car1'..'Car50' and 'Noise1'..'Noise50') are ingested as pl.String
to tolerate trailing whitespace, padding, and dirty non-numeric strings in raw CSVs.
All arithmetic defensively strips whitespace, casts with strict=False, and fills null with 0:
    pl.col(c).str.strip_chars().cast(pl.Int32, strict=False).fill_null(0)

Zero-Disk Mandate: Operates entirely in-memory on pre-parsed Polars DataFrames.
"""

from __future__ import annotations

import logging
from typing import Sequence

import polars as pl

logger = logging.getLogger(__name__)

#: All 50 Car columns expected in the VehicleData schema.
_ALL_CAR_COLS: tuple[str, ...] = tuple(f"Car{i}" for i in range(1, 51))

#: All 50 Noise columns expected in the VehicleData schema.
_ALL_NOISE_COLS: tuple[str, ...] = tuple(f"Noise{i}" for i in range(1, 51))


def _defensive_int_expr(df: pl.DataFrame, col_name: str) -> pl.Expr:
    """Defensively parse a column into pl.Int32, tolerating whitespace and nulls."""
    if df.schema.get(col_name) in (pl.String, pl.Utf8):
        return (
            pl.col(col_name)
            .str.strip_chars()
            .cast(pl.Int32, strict=False)
            .fill_null(0)
        )
    return pl.col(col_name).cast(pl.Int32, strict=False).fill_null(0)


def compute_traffic_covariates(
    vehicle_df: pl.DataFrame,
    total_stops_col: str = "TotalStops",
) -> pl.DataFrame:
    """Compute per-survey-run vehicle traffic rate covariates.

    Implements the formulas from §2.5::

        CarTotal    = Σ Car_i  for i = 1 .. TotalStops
        CarsPerStop = CarTotal / TotalStops

    Only ``Car`` columns present in *vehicle_df* are summed.
    The ``TotalStops`` column is used to guard the sum boundary (only Car
    columns with index ≤ TotalStops contribute; remainder are treated as 0).

    ``CarTotal`` is ``pl.Int32``; ``CarsPerStop`` is ``pl.Float64``.

    Parameters
    ----------
    vehicle_df:
        Polars DataFrame typed with ``VEHICLE_SCHEMA``. Must contain
        ``TotalStops`` (as ``pl.Int32`` or castable string) and at least
        one ``Car{i}`` column.
    total_stops_col:
        Column holding completed-stop count. Defaults to ``"TotalStops"``.

    Returns
    -------
    pl.DataFrame
        Input DataFrame with two appended columns:
        ``CarTotal`` (Int32) and ``CarsPerStop`` (Float64).
        Original input columns are preserved without in-place mutation.

    Raises
    ------
    TypeError
        If ``vehicle_df`` is not a :class:`polars.DataFrame`.
    ValueError
        If ``total_stops_col`` is absent or no ``Car{i}`` columns are found.
    """
    if not isinstance(vehicle_df, pl.DataFrame):
        raise TypeError(
            f"vehicle_df must be a polars.DataFrame, "
            f"got {type(vehicle_df).__name__}"
        )
    if total_stops_col not in vehicle_df.columns:
        raise ValueError(
            f"Column '{total_stops_col}' not found in vehicle_df."
        )

    present_car_cols = [c for c in _ALL_CAR_COLS if c in vehicle_df.columns]
    if not present_car_cols:
        raise ValueError(
            "No Car{i} columns found in vehicle_df. "
            "Ensure the DataFrame uses VEHICLE_SCHEMA."
        )

    if vehicle_df.is_empty():
        return vehicle_df.with_columns(
            pl.lit(None, dtype=pl.Int32).alias("CarTotal"),
            pl.lit(None, dtype=pl.Float64).alias("CarsPerStop"),
        )

    # Defensive TotalStops integer expression to support string or integer dtypes
    if vehicle_df.schema[total_stops_col] in (pl.String, pl.Utf8):
        total_stops_expr = (
            pl.col(total_stops_col)
            .str.strip_chars()
            .cast(pl.Int32, strict=False)
        )
    else:
        total_stops_expr = pl.col(total_stops_col).cast(pl.Int32, strict=False)

    # Null-guard Car columns beyond TotalStops for each row, mirroring the
    # stop nullification logic from filters.py §2.4. This ensures cars
    # recorded at stops beyond TotalStops are not included in the sum.
    # Defensively parse Car columns using:
    #   pl.col(c).str.strip_chars().cast(pl.Int32, strict=False).fill_null(0)
    guarded_car_exprs = []
    for car_col in present_car_cols:
        stop_index = int(car_col.removeprefix("Car"))  # 1-based
        car_expr = _defensive_int_expr(vehicle_df, car_col)

        guarded_car_exprs.append(
            pl.when(total_stops_expr >= stop_index)
            .then(car_expr)
            .otherwise(pl.lit(0, dtype=pl.Int32))
        )

    # Sum guarded car columns row-wise
    car_total_expr = pl.sum_horizontal(guarded_car_exprs).cast(pl.Int32).alias("CarTotal")

    # Defensive division for CarsPerStop:
    # Handles division defensively (TotalStops > 0 check) and casts to pl.Float64
    cars_per_stop_expr = (
        pl.when(total_stops_expr > 0)
        .then(
            pl.col("CarTotal").cast(pl.Float64)
            / total_stops_expr.cast(pl.Float64)
        )
        .otherwise(pl.lit(None, dtype=pl.Float64))
        .cast(pl.Float64)
        .alias("CarsPerStop")
    )

    result = (
        vehicle_df.with_columns(car_total_expr)
        .with_columns(cars_per_stop_expr)
    )

    logger.debug(
        "compute_traffic_covariates: %d rows, CarTotal range [%s, %s].",
        len(result),
        result["CarTotal"].min(),
        result["CarTotal"].max(),
    )
    return result


def compute_noise_covariates(
    vehicle_df: pl.DataFrame,
    total_stops_col: str = "TotalStops",
) -> pl.DataFrame:
    """Compute per-survey-run excessive noise covariates.

    Calculates:
        NoiseTotal = Σ Noise_i  for i = 1 .. TotalStops

    Applies the exact same defensive strip and cast pattern:
        pl.col(c).str.strip_chars().cast(pl.Int32, strict=False).fill_null(0)

    Parameters
    ----------
    vehicle_df:
        Polars DataFrame typed with ``VEHICLE_SCHEMA``. Must contain
        ``TotalStops`` and at least one ``Noise{i}`` column.
    total_stops_col:
        Column holding completed-stop count. Defaults to ``"TotalStops"``.

    Returns
    -------
    pl.DataFrame
        Input DataFrame with appended column ``NoiseTotal`` (Int32).

    Raises
    ------
    TypeError
        If ``vehicle_df`` is not a :class:`polars.DataFrame`.
    ValueError
        If ``total_stops_col`` is absent or no ``Noise{i}`` columns are found.
    """
    if not isinstance(vehicle_df, pl.DataFrame):
        raise TypeError(
            f"vehicle_df must be a polars.DataFrame, "
            f"got {type(vehicle_df).__name__}"
        )
    if total_stops_col not in vehicle_df.columns:
        raise ValueError(
            f"Column '{total_stops_col}' not found in vehicle_df."
        )

    present_noise_cols = [c for c in _ALL_NOISE_COLS if c in vehicle_df.columns]
    if not present_noise_cols:
        raise ValueError(
            "No Noise{i} columns found in vehicle_df. "
            "Ensure the DataFrame uses VEHICLE_SCHEMA."
        )

    if vehicle_df.is_empty():
        return vehicle_df.with_columns(
            pl.lit(None, dtype=pl.Int32).alias("NoiseTotal"),
        )

    if vehicle_df.schema[total_stops_col] in (pl.String, pl.Utf8):
        total_stops_expr = (
            pl.col(total_stops_col)
            .str.strip_chars()
            .cast(pl.Int32, strict=False)
        )
    else:
        total_stops_expr = pl.col(total_stops_col).cast(pl.Int32, strict=False)

    guarded_noise_exprs = []
    for noise_col in present_noise_cols:
        stop_index = int(noise_col.removeprefix("Noise"))
        noise_expr = _defensive_int_expr(vehicle_df, noise_col)

        guarded_noise_exprs.append(
            pl.when(total_stops_expr >= stop_index)
            .then(noise_expr)
            .otherwise(pl.lit(0, dtype=pl.Int32))
        )

    noise_total_expr = (
        pl.sum_horizontal(guarded_noise_exprs).cast(pl.Int32).alias("NoiseTotal")
    )
    return vehicle_df.with_columns(noise_total_expr)
