"""Route continuity, stop effort guards, and sub-route slicing for the BBS Pipeline.

Implements three algorithmic specifications from docs/03_SCHEMAS.md:

§2.1 Composite Route Keying
    RouteKey = CountryNum ∥ "_" ∥ StateNum ∥ "_" ∥ Route

§2.3 Proportional Route Continuity
    required_runs = ⌈|Y_eligible| × P_comp / 100⌉
    Exclude routes where valid_survey_runs < required_runs.

§2.4 Stop Effort Guard & Imputation Rules
    - Runs with TotalStops < min_stops are excluded.
    - Stop_i where i > TotalStops → NULL (never 0).

§2.5 Sub-route Stop Range Slicing
    SegmentCount = Σ Stop_i for i ∈ [start_stop, end_stop]

Domain invariants:
- pl.Object is banned; all identifier columns remain pl.String.
- Pandas is banned; all transforms use Polars exclusively.
- Zero-disk mandate: operates entirely in-memory on pre-parsed DataFrames.
"""

from __future__ import annotations

import logging
import math
from typing import FrozenSet, Optional

import polars as pl

logger = logging.getLogger(__name__)

#: Stop columns present in the 50-stop wide schema.
_ALL_STOP_COLS: tuple[str, ...] = tuple(f"Stop{i}" for i in range(1, 51))


# ---------------------------------------------------------------------------
# §2.1 — Composite Route Keying
# ---------------------------------------------------------------------------


def add_route_key(df: pl.DataFrame) -> pl.DataFrame:
    """Append a ``RouteKey`` column derived from composite identifier fields.

    Implements the specification::

        RouteKey = CountryNum ∥ "_" ∥ StateNum ∥ "_" ∥ Route

    All three source columns must already be ``pl.String`` (Arithmetic Typing
    Invariant §1.3).  The resulting ``RouteKey`` is also ``pl.String``.

    Parameters
    ----------
    df:
        Polars DataFrame containing at minimum ``CountryNum``, ``StateNum``,
        and ``Route`` columns of dtype ``pl.String``.

    Returns
    -------
    pl.DataFrame
        Input DataFrame with an additional ``RouteKey`` column appended.

    Raises
    ------
    TypeError
        If ``df`` is not a :class:`polars.DataFrame`.
    ValueError
        If any required source column is absent.
    """
    if not isinstance(df, pl.DataFrame):
        raise TypeError(
            f"df must be a polars.DataFrame, got {type(df).__name__}"
        )

    required = ("CountryNum", "StateNum", "Route")
    missing = [c for c in required if c not in df.columns]
    if missing:
        raise ValueError(
            f"DataFrame is missing required columns for RouteKey: {missing}"
        )

    return df.with_columns(
        (
            pl.col("CountryNum")
            + pl.lit("_")
            + pl.col("StateNum")
            + pl.lit("_")
            + pl.col("Route")
        ).alias("RouteKey")
    )


# ---------------------------------------------------------------------------
# §2.3 — Proportional Route Continuity Filtering
# ---------------------------------------------------------------------------


def filter_by_continuity(
    df: pl.DataFrame,
    eligible_years: FrozenSet[int],
    min_completeness_pct: float,
) -> pl.DataFrame:
    """Exclude routes that do not meet the proportional survey continuity threshold.

    Implements the formula::

        required_runs = ⌈|Y_eligible| × P_comp / 100⌉

    Routes with ``valid_survey_runs < required_runs`` are dropped.  A
    ``valid_survey_run`` is any row whose ``Year`` (as integer) belongs to
    ``eligible_years``.

    .. note::
        The DataFrame must contain a ``RouteKey`` column (add via
        :func:`add_route_key`) and a ``Year`` column of dtype ``pl.String``.

    Parameters
    ----------
    df:
        Polars DataFrame with ``RouteKey`` and ``Year`` columns.
    eligible_years:
        The immutable year set produced by
        :func:`~bbs_pipeline.core.discovery.get_eligible_years`.
    min_completeness_pct:
        Completeness threshold as a percentage (e.g. ``75.0`` means 75 %).
        Must be in the range ``(0, 100]``.

    Returns
    -------
    pl.DataFrame
        Filtered DataFrame retaining only routes that meet or exceed the
        continuity threshold.

    Raises
    ------
    TypeError
        If ``df`` is not a :class:`polars.DataFrame`.
    ValueError
        If required columns are absent, ``eligible_years`` is empty, or
        ``min_completeness_pct`` is outside ``(0, 100]``.
    """
    if not isinstance(df, pl.DataFrame):
        raise TypeError(
            f"df must be a polars.DataFrame, got {type(df).__name__}"
        )

    for col in ("RouteKey", "Year"):
        if col not in df.columns:
            raise ValueError(
                f"DataFrame must contain '{col}' column for continuity filtering."
            )

    if not eligible_years:
        raise ValueError("eligible_years must be non-empty.")

    if not (0 < min_completeness_pct <= 100):
        raise ValueError(
            f"min_completeness_pct must be in (0, 100], got {min_completeness_pct!r}."
        )

    required_runs: int = math.ceil(len(eligible_years) * min_completeness_pct / 100)
    logger.debug(
        "continuity filter: |Y_eligible|=%d, pct=%.2f, required_runs=%d",
        len(eligible_years),
        min_completeness_pct,
        required_runs,
    )

    # Cast eligible years to their zero-padded string equivalents for join safety,
    # but compare via integer cast to avoid zero-padding mismatches.
    eligible_year_ints = list(eligible_years)

    valid_runs = (
        df.filter(pl.col("Year").cast(pl.Int32).is_in(eligible_year_ints))
        .group_by("RouteKey")
        .agg(pl.len().alias("valid_runs"))
    )

    qualifying_keys = (
        valid_runs.filter(pl.col("valid_runs") >= required_runs)
        .select("RouteKey")
    )

    return df.join(qualifying_keys, on="RouteKey", how="inner")


# ---------------------------------------------------------------------------
# §2.4 — Discrete Stop Effort Guard & NULL Imputation
# ---------------------------------------------------------------------------


def filter_by_min_stops(
    df: pl.DataFrame,
    min_stops: int,
    total_stops_col: str = "TotalStops",
) -> pl.DataFrame:
    """Exclude survey runs with fewer completed stops than the required minimum.

    Valid runs must satisfy ``TotalStops ≥ min_stops``.

    Parameters
    ----------
    df:
        Polars DataFrame containing a ``TotalStops`` column of dtype
        ``pl.Int32`` (or any integer type).
    min_stops:
        Minimum number of stops required (must be one of 45, 48, 50 per §2.4,
        but validated only as a positive integer here to keep the guard
        composable).
    total_stops_col:
        Name of the column holding the completed-stop count.  Defaults to
        ``"TotalStops"``.

    Returns
    -------
    pl.DataFrame
        Rows where ``TotalStops ≥ min_stops``.

    Raises
    ------
    TypeError
        If ``df`` is not a :class:`polars.DataFrame`.
    ValueError
        If ``total_stops_col`` is absent or ``min_stops`` is not a positive int.
    """
    if not isinstance(df, pl.DataFrame):
        raise TypeError(
            f"df must be a polars.DataFrame, got {type(df).__name__}"
        )
    if total_stops_col not in df.columns:
        raise ValueError(
            f"Column '{total_stops_col}' not found in DataFrame."
        )
    if not isinstance(min_stops, int) or min_stops <= 0:
        raise ValueError(
            f"min_stops must be a positive integer, got {min_stops!r}."
        )

    return df.filter(pl.col(total_stops_col) >= min_stops)


def nullify_stops_beyond_total(
    df: pl.DataFrame,
    total_stops_col: str = "TotalStops",
) -> pl.DataFrame:
    """Set Stop_i to NULL for all i > TotalStops in each row.

    Enforces the domain invariant from §2.4::

        Stop_i where i > TotalStops → NULL  (never 0)

    This operates only on columns named ``Stop1`` … ``Stop50`` that are
    present in the DataFrame.  If a stop column is absent (e.g. the caller
    holds a 10-stop schema), it is silently skipped.

    Parameters
    ----------
    df:
        Polars DataFrame containing ``TotalStops`` and any subset of
        ``Stop1`` … ``Stop50``.
    total_stops_col:
        Name of the column encoding completed stop count.  Defaults to
        ``"TotalStops"``.

    Returns
    -------
    pl.DataFrame
        DataFrame with out-of-bounds stop values replaced by ``null``.

    Raises
    ------
    TypeError
        If ``df`` is not a :class:`polars.DataFrame`.
    ValueError
        If ``total_stops_col`` is absent.
    """
    if not isinstance(df, pl.DataFrame):
        raise TypeError(
            f"df must be a polars.DataFrame, got {type(df).__name__}"
        )
    if total_stops_col not in df.columns:
        raise ValueError(
            f"Column '{total_stops_col}' not found in DataFrame."
        )

    present_stop_cols = [c for c in _ALL_STOP_COLS if c in df.columns]
    if not present_stop_cols:
        # Nothing to nullify; return unchanged.
        return df

    nullify_exprs = []
    for stop_col in present_stop_cols:
        stop_index = int(stop_col.removeprefix("Stop"))  # 1-based
        # NULL when stop_index > TotalStops; keep value otherwise.
        nullify_exprs.append(
            pl.when(pl.col(total_stops_col) < stop_index)
            .then(pl.lit(None, dtype=pl.Int32))
            .otherwise(pl.col(stop_col))
            .alias(stop_col)
        )

    return df.with_columns(nullify_exprs)


# ---------------------------------------------------------------------------
# §2.5 — Sub-route Stop Range Slicing
# ---------------------------------------------------------------------------


def slice_stop_range(
    df: pl.DataFrame,
    start_stop: int,
    end_stop: int,
    segment_col: str = "SegmentCount",
) -> pl.DataFrame:
    """Sum stop counts across a contiguous sub-route window.

    Implements::

        SegmentCount = Σ Stop_i  for i ∈ [start_stop, end_stop]

    Only stop columns present in ``df`` are included in the sum.

    Parameters
    ----------
    df:
        Polars DataFrame containing ``Stop{start_stop}`` … ``Stop{end_stop}``
        columns.
    start_stop:
        Inclusive lower bound of the stop index range (1-based).
    end_stop:
        Inclusive upper bound of the stop index range (1-based).
    segment_col:
        Name for the derived aggregate column.  Defaults to ``"SegmentCount"``.

    Returns
    -------
    pl.DataFrame
        Input DataFrame with a new ``SegmentCount`` column appended.

    Raises
    ------
    TypeError
        If ``df`` is not a :class:`polars.DataFrame`.
    ValueError
        If ``start_stop`` or ``end_stop`` are out of the 1–50 range, or if
        ``start_stop`` > ``end_stop``, or if no matching stop columns exist.
    """
    if not isinstance(df, pl.DataFrame):
        raise TypeError(
            f"df must be a polars.DataFrame, got {type(df).__name__}"
        )
    if not (1 <= start_stop <= 50):
        raise ValueError(
            f"start_stop must be in [1, 50], got {start_stop!r}."
        )
    if not (1 <= end_stop <= 50):
        raise ValueError(
            f"end_stop must be in [1, 50], got {end_stop!r}."
        )
    if start_stop > end_stop:
        raise ValueError(
            f"start_stop ({start_stop}) must be ≤ end_stop ({end_stop})."
        )

    cols_in_range = [
        f"Stop{i}" for i in range(start_stop, end_stop + 1) if f"Stop{i}" in df.columns
    ]
    if not cols_in_range:
        raise ValueError(
            f"No Stop columns found for range [{start_stop}, {end_stop}] in DataFrame."
        )

    return df.with_columns(
        pl.sum_horizontal([pl.col(c) for c in cols_in_range]).alias(segment_col)
    )


# ---------------------------------------------------------------------------
# §2.6 / §2.5 — Covariate Filtering Functions
# ---------------------------------------------------------------------------


def filter_observer_tenure(
    df: pl.DataFrame,
    min_tenure: Optional[int] = None,
    max_tenure: Optional[int] = None,
    tenure_col: str = "RouteTenure",
) -> pl.DataFrame:
    """Filter survey runs by observer tenure thresholds.

    Supports pruning runs by observer experience on a route (e.g. ``min_tenure=2``
    excludes first-year observers to control for Kendall bias, or ``max_tenure=1``
    selects first-year runs).

    Gracefully handles null/missing covariate values without dropping valid rows
    unless explicitly bounded by ``min_tenure`` or ``max_tenure``.

    Parameters
    ----------
    df:
        Polars DataFrame containing observer covariates.
    min_tenure:
        Optional inclusive minimum tenure (years surveying route).
    max_tenure:
        Optional inclusive maximum tenure (years surveying route).
    tenure_col:
        Name of the tenure column. Defaults to ``"RouteTenure"``. If not found,
        falls back to ``"ObserverTenure"`` if present.

    Returns
    -------
    pl.DataFrame
        Filtered DataFrame with out-of-bounds runs removed.

    Raises
    ------
    TypeError
        If ``df`` is not a :class:`polars.DataFrame`.
    ValueError
        If bounds are invalid (e.g. negative or min > max), or if tenure column
        is missing when bounds are explicitly specified.
    """
    if not isinstance(df, pl.DataFrame):
        raise TypeError(
            f"df must be a polars.DataFrame, got {type(df).__name__}"
        )

    if min_tenure is None and max_tenure is None:
        return df

    if min_tenure is not None:
        if not isinstance(min_tenure, int) or min_tenure < 0:
            raise ValueError(
                f"min_tenure must be a non-negative integer, got {min_tenure!r}."
            )

    if max_tenure is not None:
        if not isinstance(max_tenure, int) or max_tenure < 0:
            raise ValueError(
                f"max_tenure must be a non-negative integer, got {max_tenure!r}."
            )

    if min_tenure is not None and max_tenure is not None and min_tenure > max_tenure:
        raise ValueError(
            f"min_tenure ({min_tenure}) must be <= max_tenure ({max_tenure})."
        )

    # Resolve column name
    eff_col = tenure_col
    if eff_col not in df.columns:
        if "ObserverTenure" in df.columns:
            eff_col = "ObserverTenure"
        elif "RouteTenure" in df.columns:
            eff_col = "RouteTenure"
        else:
            raise ValueError(
                f"Column '{tenure_col}' not found in DataFrame for observer tenure filtering."
            )

    filtered = df
    if min_tenure is not None:
        filtered = filtered.filter(
            pl.col(eff_col).is_not_null() & (pl.col(eff_col) >= min_tenure)
        )

    if max_tenure is not None:
        filtered = filtered.filter(
            pl.col(eff_col).is_not_null() & (pl.col(eff_col) <= max_tenure)
        )

    return filtered


def filter_traffic(
    df: pl.DataFrame,
    max_cars_per_stop: Optional[float] = None,
    max_car_total: Optional[int] = None,
    cars_per_stop_col: str = "CarsPerStop",
    car_total_col: str = "CarTotal",
) -> pl.DataFrame:
    """Filter survey runs by vehicle traffic rate thresholds.

    Excludes survey runs that exceed traffic thresholds (``CarsPerStop`` or ``CarTotal``).

    Gracefully handles null/missing covariate values without dropping valid rows
    unless explicitly bounded by ``max_cars_per_stop`` or ``max_car_total``.

    Parameters
    ----------
    df:
        Polars DataFrame containing vehicle traffic covariates.
    max_cars_per_stop:
        Optional inclusive maximum threshold for average cars per stop.
    max_car_total:
        Optional inclusive maximum threshold for total vehicles across all stops.
    cars_per_stop_col:
        Column name for cars per stop. Defaults to ``"CarsPerStop"``.
    car_total_col:
        Column name for total cars. Defaults to ``"CarTotal"``.

    Returns
    -------
    pl.DataFrame
        Filtered DataFrame with high-traffic runs removed.

    Raises
    ------
    TypeError
        If ``df`` is not a :class:`polars.DataFrame`.
    ValueError
        If bounds are invalid (e.g. negative), or if required traffic columns
        are missing when thresholds are explicitly specified.
    """
    if not isinstance(df, pl.DataFrame):
        raise TypeError(
            f"df must be a polars.DataFrame, got {type(df).__name__}"
        )

    if max_cars_per_stop is None and max_car_total is None:
        return df

    if max_cars_per_stop is not None:
        if not isinstance(max_cars_per_stop, (int, float)) or max_cars_per_stop < 0:
            raise ValueError(
                f"max_cars_per_stop must be a non-negative number, got {max_cars_per_stop!r}."
            )
        if cars_per_stop_col not in df.columns:
            raise ValueError(
                f"Column '{cars_per_stop_col}' not found in DataFrame for traffic filtering."
            )

    if max_car_total is not None:
        if not isinstance(max_car_total, int) or max_car_total < 0:
            raise ValueError(
                f"max_car_total must be a non-negative integer, got {max_car_total!r}."
            )
        if car_total_col not in df.columns:
            raise ValueError(
                f"Column '{car_total_col}' not found in DataFrame for traffic filtering."
            )

    filtered = df
    if max_cars_per_stop is not None:
        filtered = filtered.filter(
            pl.col(cars_per_stop_col).is_not_null()
            & (pl.col(cars_per_stop_col) <= max_cars_per_stop)
        )

    if max_car_total is not None:
        filtered = filtered.filter(
            pl.col(car_total_col).is_not_null()
            & (pl.col(car_total_col) <= max_car_total)
        )

    return filtered

