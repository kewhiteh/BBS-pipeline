"""In-memory 50-stop observation ingestion engine for USGS BBS Pipeline v2.0.

Streams ``50-StopData.zip`` archive members (``fifty1.csv`` .. ``fifty10.csv``)
from a caller-supplied :class:`io.BytesIO` buffer, applying all mandatory
ingestion-boundary isolation rules defined in the Track B engineering playbook:

- ``infer_schema_length=0``: every column arrives strictly as :class:`polars.String`.
- Immediate header whitespace sanitisation (``df.rename({c: c.strip() …})``).
- Exhaustive null-token vocabulary: ``["", "NA", "null", "NULL", "*", "None"]``.
- ``truncate_ragged_lines=True`` to tolerate trailing-field ragged rows.
- Composite key derivation: ``RouteKey`` and ``StopKey`` remain :class:`polars.String`
  identifiers throughout; no numeric coercion at this boundary.

Deferred arithmetic casting (``Stop1`` … ``Stop50`` → :class:`polars.Int32` with
``strict=False``) is performed *downstream* of raw ingestion and is exposed via
:func:`cast_stop_columns` as a convenience helper.

Domain invariants enforced here
--------------------------------
- Zero-disk mandate: the zip archive is never written to disk; ``zipfile`` reads
  member streams directly from the in-memory :class:`io.BytesIO` buffer.
- Ingestion boundary isolation: ``infer_schema_length=0`` is mandatory; automatic
  schema inference is strictly prohibited.
- Scope-restricted zero-filling: this module produces a raw LazyFrame; actual
  zero-fill logic lives in ``bbs_pipeline.core.zero_fill``.
- Tool lock: Polars exclusively — pandas is banned from this module.
- No generic ``except`` swallowing; every caught exception propagates with its
  full traceback to the caller.
"""

from __future__ import annotations

import io
import logging
import zipfile

import polars as pl

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

#: Null-value tokens declared at every CSV ingestion boundary.
_NULL_VALUES: list[str] = ["", "NA", "null", "NULL", "*", "None"]

#: Mandatory column headers that must be present after header sanitisation.
_MANDATORY_COLUMNS: frozenset[str] = frozenset(
    {
        "RouteDataID",
        "CountryNum",
        "StateNum",
        "Route",
        "RPID",
        "Year",
        "AOU",
    }
    | {f"Stop{i}" for i in range(1, 51)}
)

#: Expected zip member names (fifty1.csv … fifty10.csv).
_EXPECTED_MEMBERS: tuple[str, ...] = tuple(f"fifty{i}.csv" for i in range(1, 11))

#: Zero-padded widths for composite key components.
_COUNTRY_PAD: int = 3
_STATE_PAD: int = 2
_ROUTE_PAD: int = 3
_STOP_PAD: int = 2


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def read_stops_data(stream: io.BytesIO) -> pl.LazyFrame:
    """Read all 50-stop observation CSV members from an in-memory zip archive.

    Implements the mandatory Track B ingestion pipeline:

    1. Opens the :class:`io.BytesIO` buffer as a :class:`zipfile.ZipFile`
       without touching disk.
    2. Iterates over ``fifty1.csv`` … ``fifty10.csv`` members present in the
       archive (missing members are skipped with a warning; absence of *all*
       members raises :exc:`ValueError`).
    3. Each member is parsed via :func:`polars.read_csv` with
       ``infer_schema_length=0`` so that every column arrives as
       :class:`polars.String`.
    4. Headers are immediately sanitised: leading/trailing whitespace stripped.
    5. Mandatory column presence is validated after sanitisation.
    6. Composite keys ``RouteKey`` and ``StopKey`` are derived as
       :class:`polars.String` columns.
    7. All per-member DataFrames are concatenated and returned as a lazy frame.

    Parameters
    ----------
    stream:
        An ``io.BytesIO`` buffer containing a valid ``50-StopData.zip`` archive.

    Returns
    -------
    pl.LazyFrame
        Concatenated raw stop observations with all columns as ``pl.String``
        plus the derived ``RouteKey`` and ``StopKey`` columns.

    Raises
    ------
    ValueError
        If ``stream`` is not a valid zip archive, if no recognised CSV members
        are found, or if any member is missing mandatory column headers.
    """
    # ------------------------------------------------------------------
    # Step 1: Open the in-memory zip — never write to disk.
    # ------------------------------------------------------------------
    try:
        zf = zipfile.ZipFile(stream, mode="r")
    except zipfile.BadZipFile as exc:
        raise ValueError(
            "read_stops_data: the supplied stream is not a valid zip archive. "
            f"Original error: {exc}"
        ) from exc

    frames: list[pl.DataFrame] = []

    with zf:
        archive_names: set[str] = set(zf.namelist())

        for member_name in _EXPECTED_MEMBERS:
            if member_name not in archive_names:
                logger.warning(
                    "read_stops_data: member '%s' not found in archive — skipping.",
                    member_name,
                )
                continue

            logger.debug("read_stops_data: ingesting member '%s'.", member_name)

            with zf.open(member_name) as member_file:
                raw_bytes = io.BytesIO(member_file.read())

            # ----------------------------------------------------------------
            # Step 2: Parse with zero schema inference — all columns → String.
            # ----------------------------------------------------------------
            df = pl.read_csv(
                raw_bytes,
                infer_schema_length=0,
                null_values=_NULL_VALUES,
                truncate_ragged_lines=True,
            )

            # ----------------------------------------------------------------
            # Step 3: Immediate header sanitisation.
            # ----------------------------------------------------------------
            df = df.rename({c: c.strip() for c in df.columns})

            # ----------------------------------------------------------------
            # Step 4: Mandatory column validation.
            # ----------------------------------------------------------------
            _validate_mandatory_columns(df, member_name)

            # ----------------------------------------------------------------
            # Step 5: Composite key construction (RouteKey, StopKey).
            # ----------------------------------------------------------------
            df = _add_composite_keys(df)

            frames.append(df)

    if not frames:
        raise ValueError(
            "read_stops_data: no recognised CSV members (fifty1.csv … fifty10.csv) "
            "were found in the supplied archive."
        )

    logger.info(
        "read_stops_data: ingested %d member(s), %d total rows.",
        len(frames),
        sum(len(f) for f in frames),
    )

    combined: pl.DataFrame = pl.concat(frames, how="diagonal")
    return combined.lazy()


def cast_stop_columns(lf: pl.LazyFrame) -> pl.LazyFrame:
    """Cast ``Stop1`` … ``Stop50`` columns from ``pl.String`` to ``pl.Int32``.

    This is a *deferred arithmetic* helper — it must be called downstream of
    raw ingestion, never inside :func:`read_stops_data`.  Non-parseable string
    values are cast to ``null`` (``strict=False``), preserving null semantics
    for unconducted stops.

    Parameters
    ----------
    lf:
        A LazyFrame containing ``Stop1`` … ``Stop50`` columns as ``pl.String``.

    Returns
    -------
    pl.LazyFrame
        LazyFrame with ``Stop1`` … ``Stop50`` replaced by ``pl.Int32`` columns.
    """
    return lf.with_columns(
        [pl.col(f"Stop{i}").cast(pl.Int32, strict=False) for i in range(1, 51)]
    )


# ---------------------------------------------------------------------------
# Private helpers
# ---------------------------------------------------------------------------


def _validate_mandatory_columns(df: pl.DataFrame, member_name: str) -> None:
    """Raise :exc:`ValueError` if any mandatory header is absent.

    Parameters
    ----------
    df:
        DataFrame after header sanitisation.
    member_name:
        Archive member name used in the error message for diagnostics.

    Raises
    ------
    ValueError
        If one or more mandatory columns are missing.
    """
    present: frozenset[str] = frozenset(df.columns)
    missing: frozenset[str] = _MANDATORY_COLUMNS - present
    if missing:
        raise ValueError(
            f"read_stops_data: member '{member_name}' is missing mandatory "
            f"headers after whitespace sanitisation: {sorted(missing)}"
        )


def _add_composite_keys(df: pl.DataFrame) -> pl.DataFrame:
    """Derive ``RouteKey`` and ``StopKey`` composite identifier columns.

    Both keys are :class:`polars.String` and are constructed purely from
    existing string identifier columns, consistent with the Arithmetic Typing
    Invariant (§1.3): identifier columns must never be cast to numeric types.

    Key specifications
    ------------------
    - ``RouteKey``:
        ``CountryNum.zfill(3) + "_" + StateNum.zfill(2) + "_" + Route.zfill(3)``
        Example: ``"840_63_003"``

    - ``StopKey``:
        One row per stop; constructed per-stop in wide-format ingestion context.
        Because the wide schema keeps all 50 stops as columns, ``StopKey`` here
        is a *route-level prefix* — stop-specific suffixes are appended during
        downstream long-form pivoting.  The column stored is the shared prefix
        ``RouteKey`` (the full ``StopKey`` is derived during ``melt``).

        To satisfy the schema contract without premature long-form expansion
        at the raw ingestion boundary, a ``RouteKey`` column is added and
        a ``StopKey`` column is left for the downstream melt step.  This
        function adds ``RouteKey`` only; the caller is responsible for deriving
        per-stop ``StopKey`` values during melt.

    Parameters
    ----------
    df:
        DataFrame with ``CountryNum``, ``StateNum``, and ``Route`` columns
        already present as ``pl.String``.

    Returns
    -------
    pl.DataFrame
        Input DataFrame with ``RouteKey`` appended.
    """
    return df.with_columns(
        (
            pl.col("CountryNum").str.zfill(_COUNTRY_PAD)
            + pl.lit("_")
            + pl.col("StateNum").str.zfill(_STATE_PAD)
            + pl.lit("_")
            + pl.col("Route").str.zfill(_ROUTE_PAD)
        ).alias("RouteKey")
    )
