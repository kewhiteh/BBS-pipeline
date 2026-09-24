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

import polars as pl

logger = logging.getLogger(__name__)

from bbs_pipeline.covariates.traffic import (
    compute_noise_covariates,
    compute_traffic_covariates,
)

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
    - **ObserverCohort** — BBS experience classification: ``"Novice"`` (1 year),
      ``"Intermediate"`` (2-5 years), or ``"Veteran"`` (6+ years).

    The first three are ``pl.Int32`` columns, and ``ObserverCohort`` is ``pl.String``,
    appended to the returned DataFrame.

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
        Input DataFrame with four appended columns:
        ``CareerSurveysCompleted`` (Int32), ``RouteTenure`` (Int32),
        ``IsFirstYearObserver`` (Int32), ``ObserverCohort`` (String).

    Raises
    ------
    TypeError
        If ``weather_df`` is not a :class:`polars.DataFrame`.
    ValueError
        If any required column is absent.
    """
    if not isinstance(weather_df, pl.DataFrame):
        raise TypeError(
            f"weather_df must be a polars.DataFrame, got {type(weather_df).__name__}"
        )

    for col in (route_key_col, obs_col, year_col):
        if col not in weather_df.columns:
            raise ValueError(
                f"weather_df must contain '{col}' column for covariate computation."
            )

    if weather_df.is_empty():
        # Return schema-correct empty frame
        return weather_df.with_columns(
            pl.lit(None, dtype=pl.Int32).alias("CareerSurveysCompleted"),
            pl.lit(None, dtype=pl.Int32).alias("RouteTenure"),
            pl.lit(None, dtype=pl.Int32).alias("IsFirstYearObserver"),
            pl.lit(None, dtype=pl.String).alias("ObserverCohort"),
        )

    # Work with integer years for ≤ comparisons, keeping string keys intact.
    # We add a helper integer year column for arithmetic only — never used as
    # an identifier (Arithmetic Typing Invariant §1.3).
    df = weather_df.with_columns(pl.col(year_col).cast(pl.Int32).alias("_year_int"))

    # --- Build lookup tables for counting ---

    # All (ObsN, year_int) distinct survey events across the full dataset
    obs_years = df.select([obs_col, "_year_int"]).unique()

    # All (ObsN, RouteKey, year_int) distinct route-level events
    obs_route_years = df.select([obs_col, route_key_col, "_year_int"]).unique()

    # We need, per row: count of obs_years rows for same ObsN where
    # that row's year_int' ≤ current row's _year_int.
    # Polars doesn't have a native "asof group-by"; we join and filter.

    # Strategy: cross-join per observer, filter, aggregate.
    # For large datasets a self-join is efficient in Polars.

    # Step 1: CareerSurveysCompleted
    # Rename for the cross-join
    obs_years_r = obs_years.rename({obs_col: "_obs_r", "_year_int": "_year_int_r"})

    career_df = (
        df.select([obs_col, "_year_int"])
        .join(
            obs_years_r, left_on=obs_col, right_on="_obs_r", how="left", coalesce=True
        )
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
            .alias("IsFirstYearObserver"),
            pl.when(pl.col("RouteTenure") == 1)
            .then(pl.lit("Novice", dtype=pl.String))
            .when((pl.col("RouteTenure") >= 2) & (pl.col("RouteTenure") <= 5))
            .then(pl.lit("Intermediate", dtype=pl.String))
            .when(pl.col("RouteTenure") >= 6)
            .then(pl.lit("Veteran", dtype=pl.String))
            .otherwise(pl.lit(None, dtype=pl.String))
            .alias("ObserverCohort"),
        )
        .drop("_year_int")
    )

    logger.debug("compute_observer_covariates: %d rows processed.", len(result))
    return result


# ---------------------------------------------------------------------------
# §2.6 / §2.5 — Covariate Filtering Functions & Cohorts
# ---------------------------------------------------------------------------

from bbs_pipeline.core.constants import (
    BCR_NAMES,
    OBSERVER_COHORTS,
    STRATA_NAMES,
    classify_observer_cohort,
)
from bbs_pipeline.core.filters import (
    filter_by_observer_cohort,
    filter_observer_tenure,
    filter_traffic,
)

__all__ = [
    "BCR_NAMES",
    "OBSERVER_COHORTS",
    "STRATA_NAMES",
    "classify_observer_cohort",
    "compute_noise_covariates",
    "compute_observer_covariates",
    "compute_traffic_covariates",
    "filter_by_observer_cohort",
    "filter_observer_tenure",
    "filter_traffic",
]
