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
from typing import FrozenSet, Optional, Sequence

import polars as pl

logger = logging.getLogger(__name__)

#: Stop columns present in the 50-stop wide schema.
_ALL_STOP_COLS: tuple[str, ...] = tuple(f"Stop{i}" for i in range(1, 51))

#: First year in which USGS BBS 50-stop individual-stop records are available.
#: Pre-1997 data exists only as 10-stop aggregates (Count10, Count20 … Count50).
FIFTY_STOP_MIN_YEAR: int = 1997


# ---------------------------------------------------------------------------
# §2.7 — 10-Stop vs 50-Stop Temporal Resolution Guard
# ---------------------------------------------------------------------------


def enforce_fifty_stop_temporal_guard(
    start_year: Optional[int],
    end_year: Optional[int],
    resolution: str = "50stop",
    start_stop: Optional[int] = None,
    end_stop: Optional[int] = None,
) -> tuple[Optional[int], Optional[int]]:
    """Enforce temporal boundaries for 50-stop vs 10-stop data resolution.

    BBS 50-stop individual-stop records are only available from 1997 onwards.
    Pre-1997 surveys are aggregated to 10-stop increments (Count10 … Count50).

    If 50-stop resolution or sub-stop slicing beyond stop 10 is requested with
    a ``start_year`` before 1997, this function:

    - Emits an informative ``logger.warning`` describing the conflict.
    - Clamps ``start_year`` to ``FIFTY_STOP_MIN_YEAR`` (1997) and returns the
      adjusted bounds, allowing the caller to gracefully continue.

    For queries that remain entirely within the 10-stop era (both ``start_year``
    and ``end_year`` before 1997), a ``ValueError`` is raised when 50-stop
    resolution is explicitly requested because no compatible data exists.

    Parameters
    ----------
    start_year:
        Inclusive lower survey year bound (``None`` means 1966).
    end_year:
        Inclusive upper survey year bound (``None`` means no cap).
    resolution:
        Data resolution requested: ``\"50stop\"`` (default) or ``\"10stop\"``.
        When ``\"10stop\"``, this function is a no-op and returns the bounds
        unchanged.
    start_stop:
        Sub-route lower stop bound (1-50). If provided alongside ``end_stop``,
        a stop range > 10 implies 50-stop resolution.
    end_stop:
        Sub-route upper stop bound (1-50).

    Returns
    -------
    tuple[Optional[int], Optional[int]]
        Possibly adjusted ``(start_year, end_year)`` tuple.

    Raises
    ------
    ValueError
        If 50-stop resolution is requested but the entire year range falls
        before 1997 (no 50-stop data available at all).
    """
    # Determine effective resolution: explicit 50stop mode OR stop slicing > 10
    is_fifty_stop = resolution == "50stop"
    if start_stop is not None and end_stop is not None:
        # A stop range touching stops 11-50 requires 50-stop individual records.
        if end_stop > 10:
            is_fifty_stop = True

    if not is_fifty_stop:
        return start_year, end_year

    eff_start = start_year if start_year is not None else 1966

    if eff_start >= FIFTY_STOP_MIN_YEAR:
        # Already within the valid 50-stop temporal window; nothing to adjust.
        return start_year, end_year

    eff_end = end_year  # may be None (open-ended toward max_observed_year)

    if eff_end is not None and eff_end < FIFTY_STOP_MIN_YEAR:
        raise ValueError(
            f"50-stop individual-stop records are only available from "
            f"{FIFTY_STOP_MIN_YEAR} onwards. The requested year range "
            f"[{eff_start}, {eff_end}] falls entirely before this boundary. "
            f"Use 10-stop (States.zip) data for pre-{FIFTY_STOP_MIN_YEAR} queries."
        )

    logger.warning(
        "enforce_fifty_stop_temporal_guard: start_year=%d precedes the "
        "50-stop data epoch (%d). Pre-%d surveys are available only as "
        "10-stop aggregates. Clamping start_year to %d.",
        eff_start,
        FIFTY_STOP_MIN_YEAR,
        FIFTY_STOP_MIN_YEAR,
        FIFTY_STOP_MIN_YEAR,
    )
    return FIFTY_STOP_MIN_YEAR, end_year


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

    total_stops_guard = (
        pl.col(total_stops_col).str.strip_chars().cast(pl.Int32, strict=False)
        if df[total_stops_col].dtype in (pl.String, pl.Utf8)
        else pl.col(total_stops_col).cast(pl.Int32, strict=False)
    )

    nullify_exprs = []
    for stop_col in present_stop_cols:
        stop_index = int(stop_col.removeprefix("Stop"))  # 1-based
        # Defensive cast: strip whitespace, parse as Int32 (strict=False →
        # unparseable values become null), then zero-fill genuine nulls.
        # Columns may arrive as pl.String (FIFTY_STOP_SCHEMA universal-string
        # ingestion) or already as pl.Int32.
        col_dtype = df[stop_col].dtype
        if col_dtype in (pl.String, pl.Utf8):
            cast_expr = (
                pl.col(stop_col)
                .str.strip_chars()
                .cast(pl.Int32, strict=False)
                .fill_null(0)
            )
        else:
            cast_expr = pl.col(stop_col).cast(pl.Int32, strict=False).fill_null(0)
        # NULL when stop_index > TotalStops; keep cast value otherwise.
        nullify_exprs.append(
            pl.when(total_stops_guard < stop_index)
            .then(pl.lit(None, dtype=pl.Int32))
            .otherwise(cast_expr)
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

    # Defensive cast: stop columns may be pl.String (universal-string ingestion)
    # or pl.Int32.  Strip whitespace, cast non-strictly, zero-fill nulls.
    def _stop_numeric(col: str) -> pl.Expr:
        if df[col].dtype in (pl.String, pl.Utf8):
            return (
                pl.col(col)
                .str.strip_chars()
                .cast(pl.Int32, strict=False)
                .fill_null(0)
            )
        return pl.col(col).cast(pl.Int32, strict=False).fill_null(0)

    return df.with_columns(
        pl.sum_horizontal([_stop_numeric(c) for c in cols_in_range]).alias(segment_col)
    )



# ---------------------------------------------------------------------------
# §2.6 / §2.5 — Covariate Filtering Functions
# ---------------------------------------------------------------------------


def filter_observer_tenure(
    df: pl.DataFrame,
    min_tenure: Optional[int] = None,
    max_tenure: Optional[int] = None,
    tenure_col: str = "RouteTenure",
    exclude_first_year: bool = False,
    cohorts: Optional[Sequence[str]] = None,
) -> pl.DataFrame:
    """Filter survey runs by observer tenure thresholds and experience cohorts.

    Supports:
    - Excluding first-year observer runs (``exclude_first_year=True`` / tenure > 1)
      to control for the Kendall first-year observer effect.
    - Filtering by standard BBS analytical cohorts:
      * Novice: 1 year
      * Intermediate: 2-5 years
      * Veteran: 6+ years
    - Pruning runs by continuous tenure thresholds (``min_tenure``, ``max_tenure``).

    Gracefully handles null/missing covariate values without dropping valid rows
    unless explicitly bounded by filters.

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
    exclude_first_year:
        If True, excludes runs where tenure is 1 (``eff_col > 1``).
    cohorts:
        Optional sequence of experience cohort names (e.g. ``["Intermediate", "Veteran"]``).
        Options: ``"Novice"`` (1 yr), ``"Intermediate"`` (2-5 yrs), ``"Veteran"`` (6+ yrs).

    Returns
    -------
    pl.DataFrame
        Filtered DataFrame with out-of-bounds runs removed.

    Raises
    ------
    TypeError
        If ``df`` is not a :class:`polars.DataFrame`.
    ValueError
        If bounds are invalid (e.g. negative or min > max), if tenure column
        is missing when bounds are explicitly specified, or if an unrecognized
        cohort name is supplied.
    """
    if not isinstance(df, pl.DataFrame):
        raise TypeError(
            f"df must be a polars.DataFrame, got {type(df).__name__}"
        )

    if min_tenure is None and max_tenure is None and not exclude_first_year and cohorts is None:
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

    valid_cohorts = {"Novice", "Intermediate", "Veteran"}
    norm_cohorts: set[str] = set()
    if cohorts is not None:
        if not isinstance(cohorts, (list, tuple, set, frozenset)) or len(cohorts) == 0:
            raise ValueError("cohorts must be a non-empty sequence when specified.")
        for c in cohorts:
            if not isinstance(c, str):
                raise ValueError(f"Cohort names must be strings, got {type(c).__name__}")
            c_clean = c.strip().split()[0].capitalize()
            if c_clean not in valid_cohorts:
                raise ValueError(
                    f"Invalid cohort '{c}'. Must be one of: Novice, Intermediate, Veteran."
                )
            norm_cohorts.add(c_clean)

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
    if exclude_first_year:
        filtered = filtered.filter(
            pl.col(eff_col).is_not_null() & (pl.col(eff_col) > 1)
        )

    if min_tenure is not None:
        filtered = filtered.filter(
            pl.col(eff_col).is_not_null() & (pl.col(eff_col) >= min_tenure)
        )

    if max_tenure is not None:
        filtered = filtered.filter(
            pl.col(eff_col).is_not_null() & (pl.col(eff_col) <= max_tenure)
        )

    if norm_cohorts:
        conds = []
        if "Novice" in norm_cohorts:
            conds.append(pl.col(eff_col) == 1)
        if "Intermediate" in norm_cohorts:
            conds.append((pl.col(eff_col) >= 2) & (pl.col(eff_col) <= 5))
        if "Veteran" in norm_cohorts:
            conds.append(pl.col(eff_col) >= 6)

        combined_cond = conds[0]
        for cond in conds[1:]:
            combined_cond = combined_cond | cond

        filtered = filtered.filter(
            pl.col(eff_col).is_not_null() & combined_cond
        )

    return filtered


def filter_by_observer_cohort(
    df: pl.DataFrame,
    cohorts: Sequence[str],
    tenure_col: str = "RouteTenure",
) -> pl.DataFrame:
    """Filter survey runs by standard BBS observer experience cohorts.

    Cohorts:
    - ``"Novice"``: 1 year
    - ``"Intermediate"``: 2-5 years
    - ``"Veteran"``: 6+ years

    Parameters
    ----------
    df:
        Polars DataFrame containing observer covariates.
    cohorts:
        Sequence of cohort names (e.g. ``["Intermediate", "Veteran"]``).
    tenure_col:
        Name of the tenure column. Defaults to ``"RouteTenure"``.

    Returns
    -------
    pl.DataFrame
        Filtered DataFrame retaining only runs in the selected cohorts.
    """
    return filter_observer_tenure(
        df=df,
        cohorts=cohorts,
        tenure_col=tenure_col,
    )


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
            logger.warning(
                "filter_traffic: column '%s' not found in DataFrame — "
                "traffic covariates have not been joined yet. "
                "Skipping CarsPerStop threshold filter.",
                cars_per_stop_col,
            )
            max_cars_per_stop = None  # disable this threshold for the rest of the function

    if max_car_total is not None:
        if not isinstance(max_car_total, int) or max_car_total < 0:
            raise ValueError(
                f"max_car_total must be a non-negative integer, got {max_car_total!r}."
            )
        if car_total_col not in df.columns:
            logger.warning(
                "filter_traffic: column '%s' not found in DataFrame — "
                "traffic covariates have not been joined yet. "
                "Skipping CarTotal threshold filter.",
                car_total_col,
            )
            max_car_total = None  # disable this threshold for the rest of the function

    filtered = df
    if max_cars_per_stop is not None:
        cars_per_stop_expr = pl.col(cars_per_stop_col)
        if df.schema.get(cars_per_stop_col) in (pl.String, pl.Utf8):
            cars_per_stop_expr = cars_per_stop_expr.str.strip_chars().cast(pl.Float64, strict=False)
        filtered = filtered.filter(
            cars_per_stop_expr.is_not_null()
            & (cars_per_stop_expr <= max_cars_per_stop)
        )

    if max_car_total is not None:
        car_total_expr = pl.col(car_total_col)
        if df.schema.get(car_total_col) in (pl.String, pl.Utf8):
            car_total_expr = car_total_expr.str.strip_chars().cast(pl.Int32, strict=False)
        filtered = filtered.filter(
            car_total_expr.is_not_null()
            & (car_total_expr <= max_car_total)
        )

    return filtered

