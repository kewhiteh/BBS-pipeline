"""4-step Cartesian zero-filling engine preserving historical extirpation boundaries.

Implements §2.8 of docs/03_SCHEMAS.md and domain constraints of docs/05_CONSTRAINTS.md:

    Step 1: Lifetime Route Taxa Identification
        S_r = { s ∈ S_target | ∃ y ∈ [1966, max_year] s.t. Count(r, y, s) ≥ 1 }

    Step 2: Target Survey Runs
        Extract eligible survey run years meeting criteria for route r: Y_r.

    Step 3: Cartesian Grid
        Form complete combinations G_r = Y_r × S_r.

    Step 4: Imputation Join
        Left-join G_r with observed count records:
        - On match: Emit observed Stop_1 … Stop_50 and SpeciesTotal.
        - On missing: Emit SpeciesTotal = 0, Stop_1 … Stop_TotalStops = 0,
          and Stop_i = NULL for i > TotalStops.

Domain Invariants enforced here:
- Zero Geographic Species Leakage: Species never documented historically on
  route r are strictly excluded from route r's matrix.
- Historical Extirpation Preservation: Species confirmed historically on route r
  receive imputed zeros in survey years where they were not detected.
- Discrete Stop Bounds: Stops exceeding TotalStops evaluate strictly to NULL,
  never 0.
- Arithmetic Typing Invariant (§1.3): RouteKey, Year, AOU, and codes are
  zero-padded pl.String. Counts and Stop totals are pl.Int32. pl.Object is banned.
- Tool Lock: Polars exclusively. Pandas is strictly prohibited.
- Zero-Disk In-Memory Mandate: Operates entirely in RAM on Polars DataFrames.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence

import polars as pl

from bbs_pipeline.core.filters import add_route_key

logger = logging.getLogger(__name__)

#: All 50 Stop columns expected in the 50-stop wide schema.
_ALL_STOP_COLS: tuple[str, ...] = tuple(f"Stop{i}" for i in range(1, 51))

#: Explicit detection/non-detection count column names that are zero-filled.
_EXPLICIT_COUNT_COLS: tuple[str, ...] = (
    "Count",
    "SpeciesTotal",
    "StopTotal",
    "Count10",
    "Count20",
    "Count30",
    "Count40",
    "Count50",
)


# ---------------------------------------------------------------------------
# Step 1: Lifetime Route Taxa Identification (S_r)
# ---------------------------------------------------------------------------


def get_confirmed_route_taxa(
    history_df: pl.DataFrame,
    target_species: frozenset[str] | Sequence[str] | set[str] | pl.Series | None = None,
    min_year: int = 1966,
    max_year: int | None = None,
    route_key: str | None = None,
    route_key_col: str = "RouteKey",
    year_col: str = "Year",
    aou_col: str = "AOU",
) -> pl.DataFrame:
    """Scan 1966–present history to determine confirmed route taxa S_r.

    Implements Step 1 of §2.8::

        S_r = { s ∈ S_target | ∃ y ∈ [1966, max_year] s.t. Count(r, y, s) ≥ 1 }

    A species is confirmed on route r if and only if it has at least one
    positive observation (count ≥ 1) during the historical survey window.
    Species never observed on route r are strictly excluded, preventing
    geographic species leakage.

    Parameters
    ----------
    history_df:
        Polars DataFrame containing historical observations. Must contain
        ``year_col``, ``aou_col``, and either ``route_key_col`` or
        (``CountryNum``, ``StateNum``, ``Route``).
    target_species:
        Optional restriction to a target taxonomic set S_target (e.g. from
        :func:`~bbs_pipeline.core.taxonomy.resolve_target_species`). If
        provided, AOU codes are standardized to 5-digit zero-padded strings.
    min_year:
        Lower year bound (inclusive). Defaults to 1966 (BBS inception).
    max_year:
        Optional upper year bound (inclusive). If None, all years ≥ min_year
        are evaluated.
    route_key:
        Optional single RouteKey filter. If None, taxa are scanned across all
        routes in ``history_df``.
    route_key_col:
        Column name for composite route identifier. Defaults to ``"RouteKey"``.
    year_col:
        Column name for survey year. Defaults to ``"Year"``.
    aou_col:
        Column name for AOU species code. Defaults to ``"AOU"``.

    Returns
    -------
    pl.DataFrame
        Deduplicated DataFrame of confirmed (RouteKey, AOU) pairs with any
        constituent route identifiers (CountryNum, StateNum, Route) preserved.

    Raises
    ------
    TypeError
        If ``history_df`` is not a :class:`polars.DataFrame`.
    ValueError
        If required columns are missing, ``history_df`` is empty, or
        ``min_year`` / ``max_year`` bounds are invalid.
    """
    if not isinstance(history_df, pl.DataFrame):
        raise TypeError(
            f"history_df must be a polars.DataFrame, got {type(history_df).__name__}"
        )
    if history_df.is_empty():
        raise ValueError("history_df must not be empty.")

    if min_year <= 0:
        raise ValueError(f"min_year must be positive, got {min_year!r}.")
    if max_year is not None and min_year > max_year:
        raise ValueError(f"min_year ({min_year}) must be ≤ max_year ({max_year}).")

    # Ensure composite RouteKey exists
    df = history_df
    if route_key_col not in df.columns:
        route_parts = ("CountryNum", "StateNum", "Route")
        if all(c in df.columns for c in route_parts):
            df = add_route_key(df)
        else:
            raise ValueError(
                f"Column '{route_key_col}' not found and cannot be constructed: "
                f"missing {[c for c in route_parts if c not in df.columns]}."
            )

    for col in (year_col, aou_col):
        if col not in df.columns:
            raise ValueError(f"Required column '{col}' not found in history_df.")

    # Standardize AOU codes to 5-digit zero-padded strings
    df = df.with_columns(pl.col(aou_col).cast(pl.String).str.zfill(5))

    # Construct temporal filter
    if df[year_col].dtype in (pl.String, pl.Utf8):
        year_num_expr = pl.col(year_col).str.strip_chars().cast(pl.Int32, strict=False)
    else:
        year_num_expr = pl.col(year_col).cast(pl.Int32, strict=False)
    year_expr = year_num_expr >= min_year
    if max_year is not None:
        year_expr = year_expr & (year_num_expr <= max_year)

    def _defensive_count_int(col_name: str) -> pl.Expr:
        if df[col_name].dtype in (pl.String, pl.Utf8):
            return (
                pl.col(col_name)
                .str.strip_chars()
                .cast(pl.Int32, strict=False)
                .fill_null(0)
            )
        return pl.col(col_name).cast(pl.Int32, strict=False).fill_null(0)

    # Construct count filter: Count(r, y, s) >= 1
    if "SpeciesTotal" in df.columns:
        count_expr = _defensive_count_int("SpeciesTotal") >= 1
    elif "StopTotal" in df.columns:
        count_expr = _defensive_count_int("StopTotal") >= 1
    elif any(c in df.columns for c in _ALL_STOP_COLS):
        present_stops = [c for c in _ALL_STOP_COLS if c in df.columns]
        count_expr = (
            pl.sum_horizontal([_defensive_count_int(c) for c in present_stops]) >= 1
        )
    elif "Count" in df.columns:
        count_expr = _defensive_count_int("Count") >= 1
    else:
        # If no explicit count column is present, treat rows as presence records
        count_expr = pl.lit(True)

    # Route filter
    if route_key is not None:
        route_expr = pl.col(route_key_col) == route_key
    else:
        route_expr = pl.lit(True)

    # Taxonomic filter
    if target_species is not None:
        target_set = [str(s).strip().zfill(5) for s in target_species]
        taxa_expr = pl.col(aou_col).is_in(target_set)
    else:
        taxa_expr = pl.lit(True)

    filtered = df.filter(year_expr & count_expr & route_expr & taxa_expr)

    # Retain route identifier columns alongside RouteKey
    select_cols = [route_key_col]
    for extra in ("CountryNum", "StateNum", "Route"):
        if extra in df.columns and extra not in select_cols:
            select_cols.append(extra)
    select_cols.append(aou_col)

    return filtered.select(select_cols).unique().sort([route_key_col, aou_col])


# ---------------------------------------------------------------------------
# Step 2: Target Survey Runs (Y_r)
# ---------------------------------------------------------------------------


def extract_valid_survey_years(
    survey_runs_df: pl.DataFrame,
    route_key: str | None = None,
    eligible_years: frozenset[int] | Sequence[int] | set[int] | None = None,
    route_key_col: str = "RouteKey",
    year_col: str = "Year",
    total_stops_col: str = "TotalStops",
    default_total_stops: int | None = None,
) -> pl.DataFrame:
    """Extract valid survey run years Y_r for route r.

    Implements Step 2 of §2.8. Preserves run-level metadata (such as
    ``TotalStops``, ``RouteDataID``, and ``RPID``) needed for downstream
    stop effort guards and Cartesian combinations.

    Parameters
    ----------
    survey_runs_df:
        Polars DataFrame of survey runs (e.g. from weather / run history).
        Must contain ``year_col`` and ``route_key_col`` (or component keys).
    route_key:
        Optional single RouteKey filter.
    eligible_years:
        Optional set of eligible years (e.g. excluding COVID-19 hiatus 2020).
    route_key_col:
        Column name for composite route identifier. Defaults to ``"RouteKey"``.
    year_col:
        Column name for survey year. Defaults to ``"Year"``.
    total_stops_col:
        Column name holding total stops for the run. Defaults to
        ``"TotalStops"``.
    default_total_stops:
        Optional fallback value for ``TotalStops`` if the column is absent.

    Returns
    -------
    pl.DataFrame
        Deduplicated valid survey runs with run metadata preserved.

    Raises
    ------
    TypeError
        If ``survey_runs_df`` is not a :class:`polars.DataFrame`.
    ValueError
        If required columns are absent, ``survey_runs_df`` is empty, or
        ``TotalStops`` is missing and no default is provided.
    """
    if not isinstance(survey_runs_df, pl.DataFrame):
        raise TypeError(
            f"survey_runs_df must be a polars.DataFrame, got {type(survey_runs_df).__name__}"
        )
    if survey_runs_df.is_empty():
        raise ValueError("survey_runs_df must not be empty.")

    df = survey_runs_df
    if route_key_col not in df.columns:
        route_parts = ("CountryNum", "StateNum", "Route")
        if all(c in df.columns for c in route_parts):
            df = add_route_key(df)
        else:
            raise ValueError(
                f"Column '{route_key_col}' not found and cannot be constructed."
            )

    if year_col not in df.columns:
        raise ValueError(f"Column '{year_col}' not found in survey_runs_df.")

    # Guard TotalStops column
    if total_stops_col not in df.columns:
        if default_total_stops is not None:
            if not isinstance(default_total_stops, int) or default_total_stops <= 0:
                raise ValueError(
                    f"default_total_stops must be a positive integer, got {default_total_stops!r}."
                )
            df = df.with_columns(
                pl.lit(default_total_stops, dtype=pl.Int32).alias(total_stops_col)
            )
        else:
            raise ValueError(
                f"Column '{total_stops_col}' not found in survey_runs_df. "
                f"Must provide '{total_stops_col}' or specify default_total_stops."
            )
    else:
        # Cast TotalStops to Int32 and validate positivity
        df = df.with_columns(pl.col(total_stops_col).cast(pl.Int32))
        if (df[total_stops_col] <= 0).any():
            raise ValueError(
                f"All values in '{total_stops_col}' must be positive integers."
            )

    # Standardize Year as string
    df = df.with_columns(pl.col(year_col).cast(pl.String))

    # Apply eligible years filter
    if eligible_years is not None:
        eligible_list = [int(y) for y in eligible_years]
        df = df.filter(pl.col(year_col).cast(pl.Int32).is_in(eligible_list))

    # Apply route filter
    if route_key is not None:
        df = df.filter(pl.col(route_key_col) == route_key)

    # Deduplicate runs by RouteKey and Year (or RouteDataID if present)
    dedup_keys = [route_key_col, year_col]
    if "RouteDataID" in df.columns:
        dedup_keys.append("RouteDataID")

    return df.unique(subset=dedup_keys).sort([route_key_col, year_col])


# ---------------------------------------------------------------------------
# Step 3: Cartesian Grid (G_r = Y_r × S_r)
# ---------------------------------------------------------------------------


def build_cartesian_grid(
    survey_runs_df: pl.DataFrame,
    confirmed_taxa_df: pl.DataFrame,
    route_key: str | None = None,
    route_key_col: str = "RouteKey",
    aou_col: str = "AOU",
) -> pl.DataFrame:
    """Construct Cartesian product grid G_r = Y_r × S_r per route.

    Implements Step 3 of §2.8. Forms the full combination of valid survey run
    events and confirmed taxa *strictly within each route*. Cross-joining
    occurs strictly on ``RouteKey``, ensuring zero geographic leakage:
    taxa confirmed on Route A are never generated for Route B.

    Parameters
    ----------
    survey_runs_df:
        Polars DataFrame of valid survey runs Y_r (from Step 2).
    confirmed_taxa_df:
        Polars DataFrame of confirmed route taxa S_r (from Step 1).
    route_key:
        Optional single RouteKey filter.
    route_key_col:
        Column name for composite route identifier. Defaults to ``"RouteKey"``.
    aou_col:
        Column name for AOU code. Defaults to ``"AOU"``.

    Returns
    -------
    pl.DataFrame
        Cartesian grid G = ⋃_r G_r containing all run metadata cross-joined
        with confirmed taxa.

    Raises
    ------
    TypeError
        If inputs are not :class:`polars.DataFrame` instances.
    ValueError
        If required key columns are absent.
    """
    if not isinstance(survey_runs_df, pl.DataFrame):
        raise TypeError(
            f"survey_runs_df must be a polars.DataFrame, got {type(survey_runs_df).__name__}"
        )
    if not isinstance(confirmed_taxa_df, pl.DataFrame):
        raise TypeError(
            f"confirmed_taxa_df must be a polars.DataFrame, got {type(confirmed_taxa_df).__name__}"
        )

    if route_key_col not in survey_runs_df.columns:
        raise ValueError(f"Column '{route_key_col}' not found in survey_runs_df.")
    if route_key_col not in confirmed_taxa_df.columns:
        raise ValueError(f"Column '{route_key_col}' not found in confirmed_taxa_df.")
    if aou_col not in confirmed_taxa_df.columns:
        raise ValueError(f"Column '{aou_col}' not found in confirmed_taxa_df.")

    runs = survey_runs_df
    taxa = confirmed_taxa_df

    if route_key is not None:
        runs = runs.filter(pl.col(route_key_col) == route_key)
        taxa = taxa.filter(pl.col(route_key_col) == route_key)

    if runs.is_empty() or taxa.is_empty():
        # Return empty schema with runs columns + AOU
        schema = dict(runs.schema)
        schema[aou_col] = pl.String
        return pl.DataFrame(schema=schema)

    # Join strictly on route_key_col:
    # Taxa rows for route r cross with run rows for route r.
    taxa_subset = taxa.select([route_key_col, aou_col]).unique()
    grid = runs.join(taxa_subset, on=route_key_col, how="inner")

    sort_cols = [route_key_col]
    if "Year" in grid.columns:
        sort_cols.append("Year")
    sort_cols.append(aou_col)

    return grid.sort(sort_cols)


# ---------------------------------------------------------------------------
# Step 4: Imputation Join (Left-join G_r with observations)
# ---------------------------------------------------------------------------


def impute_zero_observations(
    grid_df: pl.DataFrame,
    observations_df: pl.DataFrame,
    total_stops_col: str = "TotalStops",
    default_total_stops: int | None = None,
    route_key_col: str = "RouteKey",
    year_col: str = "Year",
    aou_col: str = "AOU",
) -> pl.DataFrame:
    """Left-join Cartesian grid with observations and impute zeros and NULLs.

    Implements Step 4 of §2.8 and discrete stop bounds of §2.4:
    - On match: Emit observed Stop_1 … Stop_50 and SpeciesTotal.
    - On missing: Emit SpeciesTotal = 0, Stop_1 … Stop_TotalStops = 0.
    - For all rows: Stop_i for i > TotalStops evaluates strictly to NULL
      (never 0).

    Parameters
    ----------
    grid_df:
        Cartesian grid DataFrame G_r (from Step 3).
    observations_df:
        Observed count DataFrame (e.g. from 50-stop or summary records).
    total_stops_col:
        Column name holding completed stop count. Defaults to ``"TotalStops"``.
    default_total_stops:
        Optional fallback for ``TotalStops`` if absent from ``grid_df``.
    route_key_col:
        Column name for composite route identifier. Defaults to ``"RouteKey"``.
    year_col:
        Column name for survey year. Defaults to ``"Year"``.
    aou_col:
        Column name for AOU code. Defaults to ``"AOU"``.

    Returns
    -------
    pl.DataFrame
        Zero-filled DataFrame containing all grid columns plus Stop1…Stop50
        and SpeciesTotal with discrete stop effort bounds enforced.

    Raises
    ------
    TypeError
        If inputs are not :class:`polars.DataFrame` instances.
    ValueError
        If required key columns or TotalStops are missing or invalid.
    """
    if not isinstance(grid_df, pl.DataFrame):
        raise TypeError(
            f"grid_df must be a polars.DataFrame, got {type(grid_df).__name__}"
        )
    if not isinstance(observations_df, pl.DataFrame):
        raise TypeError(
            f"observations_df must be a polars.DataFrame, got {type(observations_df).__name__}"
        )

    # Empty grid handling
    if grid_df.is_empty():
        schema = dict(grid_df.schema)
        is_ten_stop = any(
            c in observations_df.columns
            for c in ("Count10", "Count20", "Count30", "Count40", "Count50")
        )
        if not is_ten_stop:
            for i in range(1, 51):
                schema[f"Stop{i}"] = pl.Int32
        schema["SpeciesTotal"] = pl.Int32
        for col in _EXPLICIT_COUNT_COLS:
            if col in observations_df.columns:
                schema[col] = pl.Int32
        return pl.DataFrame(schema=schema)

    # TotalStops guard
    grid = grid_df
    if total_stops_col not in grid.columns:
        if default_total_stops is not None:
            if not isinstance(default_total_stops, int) or default_total_stops <= 0:
                raise ValueError(
                    f"default_total_stops must be positive int, got {default_total_stops!r}."
                )
            grid = grid.with_columns(
                pl.lit(default_total_stops, dtype=pl.Int32).alias(total_stops_col)
            )
        else:
            raise ValueError(
                f"Column '{total_stops_col}' not found in grid DataFrame. "
                f"Specify '{total_stops_col}' or provide default_total_stops."
            )
    else:
        grid = grid.with_columns(pl.col(total_stops_col).cast(pl.Int32))
        if (grid[total_stops_col] <= 0).any():
            raise ValueError(
                f"All values in '{total_stops_col}' must be positive integers."
            )

    # Prepare observations DataFrame
    obs = observations_df
    if route_key_col not in obs.columns:
        route_parts = ("CountryNum", "StateNum", "Route")
        if all(c in obs.columns for c in route_parts):
            obs = add_route_key(obs)
        else:
            raise ValueError(f"Column '{route_key_col}' not found in observations_df.")

    if year_col not in obs.columns:
        raise ValueError(f"Column '{year_col}' not found in observations_df.")
    if aou_col not in obs.columns:
        raise ValueError(f"Column '{aou_col}' not found in observations_df.")

    obs = obs.with_columns(
        pl.col(route_key_col).cast(pl.String),
        pl.col(year_col).cast(pl.String),
        pl.col(aou_col).cast(pl.String).str.zfill(5),
    )

    # Determine join keys
    join_keys = [route_key_col, year_col, aou_col]
    if "RouteDataID" in grid.columns and "RouteDataID" in obs.columns:
        join_keys.append("RouteDataID")
    if "RPID" in grid.columns and "RPID" in obs.columns:
        join_keys.append("RPID")

    # Select only join keys and explicit count columns from obs.
    # Covariates in obs (e.g. weather, observer, traffic) are strictly ignored
    # so they never collide with or overwrite carryover covariates from grid_df.
    present_stop_cols = [c for c in _ALL_STOP_COLS if c in obs.columns]
    has_species_total = "SpeciesTotal" in obs.columns

    obs_cols = list(join_keys)
    for c in present_stop_cols:
        if c not in obs_cols:
            obs_cols.append(c)
    for c in _EXPLICIT_COUNT_COLS:
        if c in obs.columns and c not in obs_cols:
            obs_cols.append(c)

    obs_subset = obs.select(obs_cols).unique(subset=join_keys)

    # Left-join grid with observations
    joined = grid.join(obs_subset, on=join_keys, how="left", coalesce=True)

    is_ten_stop = any(
        c in observations_df.columns
        for c in ("Count10", "Count20", "Count30", "Count40", "Count50")
    )
    if not is_ten_stop:
        stop_exprs = []
        for i in range(1, 51):
            col_name = f"Stop{i}"
            if col_name in joined.columns:
                if joined[col_name].dtype == pl.String:
                    val_expr = (
                        pl.col(col_name)
                        .str.strip_chars()
                        .cast(pl.Int32, strict=False)
                        .fill_null(0)
                    )
                else:
                    val_expr = (
                        pl.col(col_name).cast(pl.Int32, strict=False).fill_null(0)
                    )
            else:
                val_expr = pl.lit(0, dtype=pl.Int32)

            total_stops_guard = (
                pl.col(total_stops_col).str.strip_chars().cast(pl.Int32, strict=False)
                if joined[total_stops_col].dtype in (pl.String, pl.Utf8)
                else pl.col(total_stops_col).cast(pl.Int32, strict=False)
            )
            stop_exprs.append(
                pl.when(total_stops_guard >= i)
                .then(val_expr)
                .otherwise(pl.lit(None, dtype=pl.Int32))
                .alias(col_name)
            )
        imputed = joined.with_columns(stop_exprs)
    else:
        imputed = joined

    # Impute explicit count columns (SpeciesTotal, Count, StopTotal, Count10..Count50).
    # Environmental, observer, and traffic covariates (e.g. StartTemp, EndTemp,
    # Wind, Sky, CarsPerStop, CarTotal, ObserverTenure, FirstYearRun) are strictly
    # preserved without blanket .fill_null(0) coercion.
    count_mutation_exprs: list[pl.Expr] = []

    def _defensive_imputed_int(col_name: str) -> pl.Expr:
        if col_name in imputed.columns and imputed[col_name].dtype in (
            pl.String,
            pl.Utf8,
        ):
            return (
                pl.col(col_name)
                .str.strip_chars()
                .cast(pl.Int32, strict=False)
                .fill_null(0)
            )
        return pl.col(col_name).cast(pl.Int32, strict=False).fill_null(0)

    # 1. SpeciesTotal:
    # If observed and present: retain observed count (or 0 if null).
    # If missing: 0 (or sum of Stop1..Stop_TotalStops).
    if has_species_total and "SpeciesTotal" in imputed.columns:
        st_val = _defensive_imputed_int("SpeciesTotal")
        species_total_expr = (
            pl.when(pl.col("SpeciesTotal").is_not_null())
            .then(st_val)
            .otherwise(
                pl.sum_horizontal(
                    [
                        _defensive_imputed_int(f"Stop{i}")
                        for i in range(1, 51)
                        if f"Stop{i}" in imputed.columns
                    ]
                )
                if present_stop_cols
                else (
                    _defensive_imputed_int("Count")
                    if "Count" in imputed.columns
                    else pl.lit(0, dtype=pl.Int32)
                )
            )
            .alias("SpeciesTotal")
        )
    else:
        if present_stop_cols or any(c in joined.columns for c in _ALL_STOP_COLS):
            stops_to_sum = [c for c in _ALL_STOP_COLS if c in imputed.columns]
            species_total_expr = pl.sum_horizontal(
                [_defensive_imputed_int(c) for c in stops_to_sum]
            ).alias("SpeciesTotal")
        elif "Count" in joined.columns:
            species_total_expr = _defensive_imputed_int("Count").alias("SpeciesTotal")
        else:
            species_total_expr = pl.sum_horizontal(
                [
                    _defensive_imputed_int(f"Stop{i}")
                    for i in range(1, 51)
                    if f"Stop{i}" in imputed.columns
                ]
            ).alias("SpeciesTotal")
    count_mutation_exprs.append(species_total_expr)

    # 2. Count:
    if "Count" in imputed.columns:
        count_val = _defensive_imputed_int("Count")
        count_mutation_exprs.append(
            pl.when(pl.col("Count").is_not_null())
            .then(count_val)
            .otherwise(pl.lit(0, dtype=pl.Int32))
            .alias("Count")
        )

    # 3. StopTotal:
    if "StopTotal" in imputed.columns:
        stoptotal_val = _defensive_imputed_int("StopTotal")
        count_mutation_exprs.append(
            pl.when(pl.col("StopTotal").is_not_null())
            .then(stoptotal_val)
            .otherwise(pl.lit(0, dtype=pl.Int32))
            .alias("StopTotal")
        )

    # 4. 10-stop summary count columns (Count10..Count50):
    band_limits = {
        "Count10": 10,
        "Count20": 20,
        "Count30": 30,
        "Count40": 40,
        "Count50": 50,
    }
    total_stops_guard = (
        pl.col(total_stops_col).str.strip_chars().cast(pl.Int32, strict=False)
        if imputed[total_stops_col].dtype in (pl.String, pl.Utf8)
        else pl.col(total_stops_col).cast(pl.Int32, strict=False)
    )
    for c, limit in band_limits.items():
        if c in imputed.columns or is_ten_stop:
            c_val = (
                _defensive_imputed_int(c)
                if c in imputed.columns
                else pl.lit(0, dtype=pl.Int32)
            )
            count_mutation_exprs.append(
                pl.when(total_stops_guard < limit)
                .then(pl.lit(None, dtype=pl.Int32))
                .when(
                    pl.col(c).is_not_null() if c in imputed.columns else pl.lit(False)
                )
                .then(c_val)
                .otherwise(pl.lit(0, dtype=pl.Int32))
                .alias(c)
            )

    result = imputed.with_columns(count_mutation_exprs)

    sort_keys = [route_key_col, year_col, aou_col]
    return result.sort(sort_keys)


# ---------------------------------------------------------------------------
# Integrated Pipeline Orchestrator
# ---------------------------------------------------------------------------


def zero_fill_route_observations(
    history_df: pl.DataFrame,
    survey_runs_df: pl.DataFrame,
    observations_df: pl.DataFrame | None = None,
    target_species: frozenset[str] | Sequence[str] | set[str] | pl.Series | None = None,
    eligible_years: frozenset[int] | Sequence[int] | set[int] | None = None,
    route_key: str | None = None,
    total_stops_col: str = "TotalStops",
    default_total_stops: int | None = None,
    min_year: int = 1966,
    max_year: int | None = None,
) -> pl.DataFrame:
    """Execute the full 4-step Cartesian zero-filling pipeline.

    Preserves historical extirpation boundaries while preventing geographic
    species leakage.

    Workflow:
    1. Scan ``history_df`` (1966–present) for confirmed route taxa S_r.
    2. Extract valid survey runs Y_r from ``survey_runs_df``.
    3. Construct Cartesian product grid G_r = Y_r × S_r.
    4. Left-join G_r with observations; impute 0 for stops 1..TotalStops,
       and NULL for stops > TotalStops.

    Parameters
    ----------
    history_df:
        Full historical observation record (1966–present) used to identify
        lifetime confirmed taxa per route.
    survey_runs_df:
        Survey run records (from weather / run quality filters) containing
        the target survey years and TotalStops per run.
    observations_df:
        Observed detection counts to left-join with the Cartesian grid.
        If None, defaults to ``history_df``.
    target_species:
        Optional taxonomic subset S_target.
    eligible_years:
        Optional eligible year set Y_eligible.
    route_key:
        Optional single RouteKey filter.
    total_stops_col:
        Name of the completed-stops column. Defaults to ``"TotalStops"``.
    default_total_stops:
        Optional fallback TotalStops if absent from ``survey_runs_df``.
    min_year:
        Inception year for historical taxa scan. Defaults to 1966.
    max_year:
        Terminal year for historical taxa scan. If None, unbounded.

    Returns
    -------
    pl.DataFrame
        Zero-filled observation DataFrame with full stop matrix (Stop1..Stop50)
        and SpeciesTotal.
    """
    if observations_df is None:
        observations_df = history_df

    # Step 1: Confirmed route taxa S_r
    confirmed_taxa = get_confirmed_route_taxa(
        history_df=history_df,
        target_species=target_species,
        min_year=min_year,
        max_year=max_year,
        route_key=route_key,
        route_key_col="RouteKey",
    )

    # Step 2: Valid survey runs Y_r
    valid_runs = extract_valid_survey_years(
        survey_runs_df=survey_runs_df,
        route_key=route_key,
        eligible_years=eligible_years,
        route_key_col="RouteKey",
        total_stops_col=total_stops_col,
        default_total_stops=default_total_stops,
    )

    # Step 3: Cartesian grid G_r = Y_r × S_r
    grid = build_cartesian_grid(
        survey_runs_df=valid_runs,
        confirmed_taxa_df=confirmed_taxa,
        route_key=route_key,
        route_key_col="RouteKey",
    )

    # Step 4: Imputation join
    return impute_zero_observations(
        grid_df=grid,
        observations_df=observations_df,
        total_stops_col=total_stops_col,
        default_total_stops=default_total_stops,
        route_key_col="RouteKey",
    )


#: Pipeline convenience alias
zero_fill = zero_fill_route_observations
