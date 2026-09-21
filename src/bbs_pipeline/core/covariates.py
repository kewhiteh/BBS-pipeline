"""Observer experience and traffic rate covariates for the USGS BBS Pipeline.

Implements §2.6 and §2.5 (traffic portion) of docs/03_SCHEMAS.md:

§2.6 — Longitudinal Observer Covariates & Kendall Bias:

    CareerSurveysCompleted = |{ y ≤ CurrentYear | ObsN surveyed any route in y }|
    RouteTenure            = |{ y ≤ CurrentYear | ObsN surveyed RouteKey in y }|
    IsFirstYearObserver    = 1 if RouteTenure == 1, else 0

§2.5 (traffic aggregations):

    CarTotal    = Σ Car_i  for i = 1..TotalStops
    CarsPerStop = CarTotal / TotalStops

Domain Invariants enforced here:
- ``ObsN``, ``Year``, ``RouteKey`` are ``pl.String`` identifiers — never cast
  to numeric for keying or grouping.
- ``pl.Object`` is banned.
- Pandas is banned; all transforms use Polars exclusively.
- Zero-disk mandate: operates entirely in-memory on pre-parsed DataFrames.
"""

from __future__ import annotations

import logging
from typing import Optional

import polars as pl

logger = logging.getLogger(__name__)

#: All 50 Car columns expected in the VehicleData schema.
_ALL_CAR_COLS: tuple[str, ...] = tuple(f"Car{i}" for i in range(1, 51))


# ---------------------------------------------------------------------------
# §2.6 — Longitudinal Observer Covariates
# ---------------------------------------------------------------------------


def compute_observer_covariates(
    weather_df: pl.DataFrame,
    route_key_col: str = "RouteKey",
    obs_col: str = "ObsN",
    year_col: str = "Year",
) -> pl.DataFrame:
    """Compute per-survey-run observer longitudinal covariates.

    For every row in *weather_df* (one row = one survey run), calculates:

    - **CareerSurveysCompleted** — the number of *distinct* years ≤ the
      current run's year in which the observer (``ObsN``) conducted any
      BBS survey run on *any* route.
    - **RouteTenure** — the number of distinct years ≤ current year in which
      the observer surveyed *this specific* route (``RouteKey``).
    - **IsFirstYearObserver** — ``1`` when ``RouteTenure == 1``, else ``0``
      (binary Kendall bias indicator).

    All three are ``pl.Int32`` columns appended to the returned DataFrame.

    .. note::
        The input DataFrame must already contain a ``RouteKey`` column (added
        via :func:`~bbs_pipeline.core.filters.add_route_key`).

    Parameters
    ----------
    weather_df:
        Polars DataFrame typed with ``WEATHER_SCHEMA`` plus a ``RouteKey``
        column.  Must contain ``ObsN`` (``pl.String``), ``Year``
        (``pl.String``), and ``RouteKey`` (``pl.String``).
    route_key_col:
        Column name for the composite route identifier.  Defaults to
        ``"RouteKey"``.
    obs_col:
        Column name for observer identifier.  Defaults to ``"ObsN"``.
    year_col:
        Column name for survey year.  Defaults to ``"Year"``.

    Returns
    -------
    pl.DataFrame
        Input DataFrame with three appended columns:
        ``CareerSurveysCompleted`` (Int32), ``RouteTenure`` (Int32),
        ``IsFirstYearObserver`` (Int32).

    Raises
    ------
    TypeError
        If ``weather_df`` is not a :class:`polars.DataFrame`.
    ValueError
        If any required column is absent.
    """
    if not isinstance(weather_df, pl.DataFrame):
        raise TypeError(
            f"weather_df must be a polars.DataFrame, "
            f"got {type(weather_df).__name__}"
        )

    for col in (route_key_col, obs_col, year_col):
        if col not in weather_df.columns:
            raise ValueError(
                f"weather_df must contain '{col}' column for covariate "
                f"computation."
            )

    if weather_df.is_empty():
        # Return schema-correct empty frame
        return weather_df.with_columns(
            pl.lit(None, dtype=pl.Int32).alias("CareerSurveysCompleted"),
            pl.lit(None, dtype=pl.Int32).alias("RouteTenure"),
            pl.lit(None, dtype=pl.Int32).alias("IsFirstYearObserver"),
        )

    # Work with integer years for ≤ comparisons, keeping string keys intact.
    # We add a helper integer year column for arithmetic only — never used as
    # an identifier (Arithmetic Typing Invariant §1.3).
    df = weather_df.with_columns(
        pl.col(year_col).cast(pl.Int32).alias("_year_int")
    )

    # --- Build lookup tables for counting ---

    # All (ObsN, year_int) distinct survey events across the full dataset
    obs_years = (
        df.select([obs_col, "_year_int"])
        .unique()
    )

    # All (ObsN, RouteKey, year_int) distinct route-level events
    obs_route_years = (
        df.select([obs_col, route_key_col, "_year_int"])
        .unique()
    )

    # We need, per row: count of obs_years rows for same ObsN where
    # that row's year_int' ≤ current row's _year_int.
    # Polars doesn't have a native "asof group-by"; we join and filter.

    # Strategy: cross-join per observer, filter, aggregate.
    # For large datasets a self-join is efficient in Polars.

    # Step 1: CareerSurveysCompleted
    # Rename for the cross-join
    obs_years_r = obs_years.rename(
        {obs_col: "_obs_r", "_year_int": "_year_int_r"}
    )

    career_df = (
        df.select([obs_col, "_year_int"])
        .join(obs_years_r, left_on=obs_col, right_on="_obs_r", how="left", coalesce=True)
        .filter(pl.col("_year_int_r") <= pl.col("_year_int"))
        .group_by([obs_col, "_year_int"])
        .agg(pl.n_unique("_year_int_r").alias("CareerSurveysCompleted"))
        .with_columns(pl.col("CareerSurveysCompleted").cast(pl.Int32))
    )

    # Step 2: RouteTenure
    obs_route_years_r = obs_route_years.rename(
        {
            obs_col: "_obs_r",
            route_key_col: "_rk_r",
            "_year_int": "_year_int_r2",
        }
    )

    tenure_df = (
        df.select([obs_col, route_key_col, "_year_int"])
        .join(
            obs_route_years_r,
            left_on=[obs_col, route_key_col],
            right_on=["_obs_r", "_rk_r"],
            how="left",
            coalesce=True,
        )
        .filter(pl.col("_year_int_r2") <= pl.col("_year_int"))
        .group_by([obs_col, route_key_col, "_year_int"])
        .agg(pl.n_unique("_year_int_r2").alias("RouteTenure"))
        .with_columns(pl.col("RouteTenure").cast(pl.Int32))
    )

    # Step 3: Join covariates back to original DataFrame
    result = (
        df.join(
            career_df,
            on=[obs_col, "_year_int"],
            how="left",
            coalesce=True,
        )
        .join(
            tenure_df,
            on=[obs_col, route_key_col, "_year_int"],
            how="left",
            coalesce=True,
        )
        .with_columns(
            pl.when(pl.col("RouteTenure") == 1)
            .then(pl.lit(1, dtype=pl.Int32))
            .otherwise(pl.lit(0, dtype=pl.Int32))
            .alias("IsFirstYearObserver")
        )
        .drop("_year_int")
    )

    logger.debug(
        "compute_observer_covariates: %d rows processed.", len(result)
    )
    return result


# ---------------------------------------------------------------------------
# §2.5 — Traffic Rate Metrics
# ---------------------------------------------------------------------------


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
        Polars DataFrame typed with ``VEHICLE_SCHEMA``.  Must contain
        ``TotalStops`` (as ``pl.Int32`` or castable integer) and at least
        one ``Car{i}`` column.
    total_stops_col:
        Column holding completed-stop count.  Defaults to ``"TotalStops"``.

    Returns
    -------
    pl.DataFrame
        Input DataFrame with two appended columns:
        ``CarTotal`` (Int32) and ``CarsPerStop`` (Float64).

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

    # Null-guard Car columns beyond TotalStops for each row, mirroring the
    # stop nullification logic from filters.py §2.4.  This ensures cars
    # recorded at stops beyond TotalStops are not included in the sum.
    guarded_car_exprs = []
    for car_col in present_car_cols:
        stop_index = int(car_col.removeprefix("Car"))  # 1-based
        guarded_car_exprs.append(
            pl.when(pl.col(total_stops_col) >= stop_index)
            .then(pl.col(car_col))
            .otherwise(pl.lit(0, dtype=pl.Int32))
            .alias(car_col)
        )

    df_guarded = vehicle_df.with_columns(guarded_car_exprs)

    # Sum guarded car columns row-wise
    car_sum_expr = pl.sum_horizontal(
        [pl.col(c) for c in present_car_cols]
    ).cast(pl.Int32)

    result = (
        df_guarded.with_columns(car_sum_expr.alias("CarTotal"))
        .with_columns(
            (
                pl.col("CarTotal").cast(pl.Float64)
                / pl.col(total_stops_col).cast(pl.Float64)
            ).alias("CarsPerStop")
        )
    )

    logger.debug(
        "compute_traffic_covariates: %d rows, CarTotal range [%s, %s].",
        len(result),
        result["CarTotal"].min(),
        result["CarTotal"].max(),
    )
    return result
