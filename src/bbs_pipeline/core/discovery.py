"""Dynamic temporal discovery for the USGS BBS Pipeline.

Implements §2.2 of docs/03_SCHEMAS.md:

    max_observed_year = max({ int(y) | y ∈ weather.Year })

    Y_eligible = { y ∈ [start_year, min(end_year, max_observed_year)]
                   | y ≠ 2020 }

Domain Invariants enforced here:
- ``Year`` column must be ``pl.String`` (Arithmetic Typing Invariant §1.3).
- COVID-19 survey hiatus year 2020 is unconditionally excluded.
- Zero-disk mandate: no file I/O; accepts a pre-parsed Polars DataFrame.
"""

from __future__ import annotations

import logging
from typing import FrozenSet

import polars as pl

logger = logging.getLogger(__name__)

#: The COVID-19 BBS hiatus year — unconditionally excluded from eligible sets.
COVID_HIATUS_YEAR: int = 2020


def get_max_observed_year(weather_df: pl.DataFrame) -> int:
    """Extract the maximum observed survey year from the weather DataFrame.

    Implements the formula::

        max_observed_year = max({ int(y) | y ∈ weather.Year })

    Parameters
    ----------
    weather_df:
        Polars DataFrame typed with ``WEATHER_SCHEMA``.  Must contain a
        ``Year`` column of dtype ``pl.String``.

    Returns
    -------
    int
        The maximum year present in the weather records as a Python int.

    Raises
    ------
    TypeError
        If ``weather_df`` is not a :class:`polars.DataFrame`.
    ValueError
        If the ``Year`` column is absent, or if the DataFrame is empty /
        contains no parseable year values.
    """
    if not isinstance(weather_df, pl.DataFrame):
        raise TypeError(
            f"weather_df must be a polars.DataFrame, got {type(weather_df).__name__}"
        )

    if "Year" not in weather_df.columns:
        raise ValueError("weather_df must contain a 'Year' column.")

    if weather_df.is_empty():
        raise ValueError("weather_df is empty; cannot determine max_observed_year.")

    max_year: int = (
        weather_df.select(pl.col("Year").cast(pl.Int32).max())
        .item()
    )

    if max_year is None:
        raise ValueError(
            "All 'Year' values are null; cannot determine max_observed_year."
        )

    logger.debug("max_observed_year resolved to %d", max_year)
    return int(max_year)


def get_eligible_years(
    start_year: int,
    end_year: int,
    max_observed_year: int,
) -> FrozenSet[int]:
    """Compute the eligible survey year set with COVID-19 hiatus exclusion.

    Implements the formula::

        Y_eligible = { y ∈ [start_year, min(end_year, max_observed_year)]
                       | y ≠ 2020 }

    Parameters
    ----------
    start_year:
        Inclusive lower bound of the requested temporal window.
    end_year:
        Inclusive upper bound of the requested temporal window (further
        capped by ``max_observed_year``).
    max_observed_year:
        The dynamically derived ceiling from :func:`get_max_observed_year`.

    Returns
    -------
    frozenset[int]
        An immutable set of eligible integer years.

    Raises
    ------
    ValueError
        If ``start_year`` > ``end_year``, or if any year argument is not a
        positive integer.
    """
    for name, val in (
        ("start_year", start_year),
        ("end_year", end_year),
        ("max_observed_year", max_observed_year),
    ):
        if not isinstance(val, int) or val <= 0:
            raise ValueError(f"{name} must be a positive integer, got {val!r}.")

    if start_year > end_year:
        raise ValueError(
            f"start_year ({start_year}) must be ≤ end_year ({end_year})."
        )

    effective_ceiling = min(end_year, max_observed_year)
    eligible: FrozenSet[int] = frozenset(
        y
        for y in range(start_year, effective_ceiling + 1)
        if y != COVID_HIATUS_YEAR
    )

    logger.debug(
        "eligible_years: start=%d, end=%d, max_obs=%d, ceiling=%d, |Y|=%d",
        start_year,
        end_year,
        max_observed_year,
        effective_ceiling,
        len(eligible),
    )
    return eligible
