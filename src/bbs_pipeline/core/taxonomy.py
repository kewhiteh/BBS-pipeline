"""Taxonomic set-union resolver for the USGS BBS Pipeline.

Implements §2.7 of docs/03_SCHEMAS.md:

    S_target = (⋃ S_guild ∪ ⋃ S_order ∪ ⋃ S_family ∪ S_custom_species)
               ∖ S_migrant_nonbreeder

Domain Invariants enforced here:
- AOU codes are strictly 5-digit zero-padded ``pl.String`` (Arithmetic Typing
  Invariant §1.3).
- ``pl.Object`` is banned; all intermediate sets are Python ``frozenset[str]``.
- Pandas is banned; SpeciesList ingestion uses Polars with explicit
  ``schema_overrides``.
- Zero-disk mandate: all parsing operates on pre-loaded ``io.BytesIO`` buffers
  or already-parsed DataFrames. No file I/O occurs inside these functions
  except ``load_guilds_json``, which accepts a filesystem path to the static
  ``data/guilds.json`` asset (a committed repository file, not a download).
"""

from __future__ import annotations

import io
import json
import logging
import zipfile
from collections.abc import Sequence
from pathlib import Path

import polars as pl

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Polars schema for SpeciesList.csv  (§1.6 of docs/03_SCHEMAS.md)
# Note: the physical file uses column name "Order" (not "ORDER").
# ---------------------------------------------------------------------------

SPECIES_LIST_SCHEMA: dict[str, type[pl.DataType]] = {
    "Seq": pl.String,
    "AOU": pl.String,
    "English_Common_Name": pl.String,
    "French_Common_Name": pl.String,
    "Order": pl.String,
    "Family": pl.String,
    "Genus": pl.String,
    "Species": pl.String,
}

# ---------------------------------------------------------------------------
# Migrant AOU extraction schema — the AOU column in Migrants.csv
# We only need the AOU column; remaining columns are ignored during extraction.
# ---------------------------------------------------------------------------

_MIGRANT_AOU_SCHEMA: dict[str, type[pl.DataType]] = {
    "AOU": pl.String,
}


def _pad_aou(aou: str) -> str:
    """Zero-pad an AOU code to exactly 5 digits."""
    return aou.strip().zfill(5)


# ---------------------------------------------------------------------------
# §1.6 — Parse SpeciesList
# ---------------------------------------------------------------------------


def parse_species_list(buf: io.BytesIO) -> pl.DataFrame:
    """Parse the SpeciesList CSV from an in-memory buffer.

    Produces a Polars DataFrame with explicit ``SPECIES_LIST_SCHEMA`` — no
    type inference.  The ``AOU`` column is zero-padded to 5 digits.

    Parameters
    ----------
    buf:
        ``io.BytesIO`` wrapping the raw SpeciesList CSV bytes.

    Returns
    -------
    pl.DataFrame
        DataFrame with columns matching :data:`SPECIES_LIST_SCHEMA`.

    Raises
    ------
    TypeError
        If ``buf`` is not an ``io.BytesIO``.
    ValueError
        If the buffer is empty or does not contain an ``AOU`` column.
    """
    if not isinstance(buf, io.BytesIO):
        raise TypeError(f"buf must be io.BytesIO, got {type(buf).__name__}")

    buf.seek(0)
    raw_bytes = buf.read()
    if not raw_bytes:
        raise ValueError("SpeciesList buffer is empty.")

    df = pl.read_csv(
        io.BytesIO(raw_bytes),
        schema=SPECIES_LIST_SCHEMA,
        encoding="latin1",
        infer_schema_length=0,
    )

    if "AOU" not in df.columns:
        raise ValueError("SpeciesList CSV does not contain an 'AOU' column.")

    # Zero-pad AOU to 5 digits (invariant: always pl.String)
    df = df.with_columns(pl.col("AOU").str.strip_chars().str.zfill(5).alias("AOU"))

    logger.debug("SpeciesList parsed: %d species rows.", len(df))
    return df


# ---------------------------------------------------------------------------
# §1.6 — Load guilds.json
# ---------------------------------------------------------------------------


def load_guilds_json(guilds_path: Path | str) -> dict[str, dict[str, str]]:
    """Load the ``guilds.json`` guild-trait registry from disk.

    The file is a committed repository asset, not a downloaded artifact, so
    reading it from the filesystem does not violate the Zero-Disk Mandate
    (which governs runtime pipeline downloads only).

    Parameters
    ----------
    guilds_path:
        Path to ``data/guilds.json``.

    Returns
    -------
    dict[str, dict[str, str]]
        Mapping of 5-digit zero-padded AOU string → trait dict with keys
        ``breeding_habitat``, ``foraging_guild``, ``migratory_status``.

    Raises
    ------
    FileNotFoundError
        If ``guilds_path`` does not exist.
    ValueError
        If the file is empty or not valid JSON.
    """
    path = Path(guilds_path)
    if not path.exists():
        raise FileNotFoundError(f"guilds.json not found at: {path}")

    text = path.read_text(encoding="utf-8")
    if not text.strip():
        raise ValueError(f"guilds.json is empty: {path}")

    raw: dict[str, dict[str, str]] = json.loads(text)

    # Normalise keys to 5-digit zero-padded strings
    guilds: dict[str, dict[str, str]] = {_pad_aou(k): v for k, v in raw.items()}
    logger.debug("guilds.json loaded: %d entries.", len(guilds))
    return guilds


# ---------------------------------------------------------------------------
# §1.6 — Parse MigrantNonBreeder AOU set
# ---------------------------------------------------------------------------


def parse_migrant_nonbreeder(buf: io.BytesIO) -> frozenset[str]:
    """Extract the set of migrant/non-breeder AOU codes from the zip buffer.

    Reads ``MigrantNonBreeder/Migrants.csv`` inside the ZIP archive using
    explicit ``schema_overrides`` and returns a frozen set of 5-digit zero-
    padded AOU strings.

    Parameters
    ----------
    buf:
        ``io.BytesIO`` wrapping the raw ``MigrantNonBreeder.zip`` bytes.

    Returns
    -------
    frozenset[str]
        Immutable set of 5-digit AOU strings to be excluded.

    Raises
    ------
    TypeError
        If ``buf`` is not an ``io.BytesIO``.
    ValueError
        If the buffer is empty, the ZIP is corrupt, or no AOU column is found.
    """
    if not isinstance(buf, io.BytesIO):
        raise TypeError(f"buf must be io.BytesIO, got {type(buf).__name__}")

    buf.seek(0)
    raw_bytes = buf.read()
    if not raw_bytes:
        raise ValueError("MigrantNonBreeder buffer is empty.")

    migrant_aous: set[str] = set()

    with zipfile.ZipFile(io.BytesIO(raw_bytes)) as zf:
        csv_entries = [
            n
            for n in zf.namelist()
            if n.endswith(".csv") and "Migrants" in n and "Summary" not in n
        ]
        if not csv_entries:
            raise ValueError("MigrantNonBreeder.zip contains no Migrants CSV file.")

        for entry in csv_entries:
            csv_bytes = zf.read(entry)
            df = pl.read_csv(
                io.BytesIO(csv_bytes),
                schema_overrides=_MIGRANT_AOU_SCHEMA,
                encoding="latin1",
                infer_schema_length=0,
                null_values=["", "NA", "null", "NULL", "*", "None"],
                truncate_ragged_lines=True,
            )
            df = df.rename({c: c.strip() for c in df.columns})
            if "AOU" not in df.columns:
                raise ValueError(f"Migrants CSV '{entry}' has no AOU column.")
            aous = (
                df.select(pl.col("AOU").str.strip_chars().str.zfill(5))
                .to_series()
                .unique()
                .to_list()
            )
            migrant_aous.update(aous)

    result: frozenset[str] = frozenset(migrant_aous)
    logger.debug("MigrantNonBreeder AOU set size: %d codes.", len(result))
    return result


# ---------------------------------------------------------------------------
# §2.7 — Taxonomic Set-Union Resolver
# ---------------------------------------------------------------------------


def resolve_target_species(
    species_df: pl.DataFrame,
    guilds: dict[str, dict[str, str]],
    migrant_aous: frozenset[str],
    *,
    orders: Sequence[str] | None = None,
    families: Sequence[str] | None = None,
    guild_breeding_habitats: Sequence[str] | None = None,
    guild_foraging_guilds: Sequence[str] | None = None,
    custom_aous: Sequence[str] | None = None,
    all_species: bool = False,
) -> frozenset[str]:
    """Evaluate the set-union taxonomic resolver and return the target AOU set.

    Implements the formula from §2.7::

        S_target = (⋃ S_guild ∪ ⋃ S_order ∪ ⋃ S_family ∪ S_custom_species)
                   ∖ S_migrant_nonbreeder

    When ``all_species=True`` (the ``--all-species`` / ``--community`` flag),
    the union operand is the complete SpeciesList universe before exclusion.

    Parameters
    ----------
    species_df:
        Parsed SpeciesList DataFrame (from :func:`parse_species_list`).
    guilds:
        Guild trait registry (from :func:`load_guilds_json`).
    migrant_aous:
        Frozen set of migrant/non-breeder AOU codes to subtract.
    orders:
        Taxonomic orders to include (matched against ``Order`` column).
    families:
        Taxonomic families to include (matched against ``Family`` column).
    guild_breeding_habitats:
        Breeding-habitat guild values to include (e.g. ``["wetland", "forest"]``).
    guild_foraging_guilds:
        Foraging-guild values to include (e.g. ``["aerial_insectivore"]``).
    custom_aous:
        Explicit AOU codes (zero-padded 5-digit strings or raw ints-as-strings)
        to include regardless of taxonomic membership.
    all_species:
        If ``True``, include all species in SpeciesList before migrant
        exclusion (``--all-species`` / ``--community`` flag).

    Returns
    -------
    frozenset[str]
        Immutable set of 5-digit AOU strings constituting S_target.

    Raises
    ------
    TypeError
        If ``species_df`` is not a :class:`polars.DataFrame`.
    ValueError
        If ``species_df`` is empty or missing the ``AOU`` column.
    """
    if not isinstance(species_df, pl.DataFrame):
        raise TypeError(
            f"species_df must be a polars.DataFrame, got {type(species_df).__name__}"
        )
    if species_df.is_empty():
        raise ValueError("species_df is empty; cannot resolve species.")
    if "AOU" not in species_df.columns:
        raise ValueError("species_df must contain an 'AOU' column.")

    all_aous: frozenset[str] = frozenset(species_df.select("AOU").to_series().to_list())

    broad_union: set[str] = set()

    if all_species:
        # --all-species / --community flag: universe minus migrants
        broad_union.update(all_aous)
    else:
        # --- Order filter ---
        if orders:
            order_set = {o.strip() for o in orders}
            if "Order" in species_df.columns:
                matched = (
                    species_df.filter(pl.col("Order").is_in(order_set))
                    .select("AOU")
                    .to_series()
                    .to_list()
                )
                broad_union.update(matched)

        # --- Family filter ---
        if families:
            family_set = {f.strip() for f in families}
            if "Family" in species_df.columns:
                matched = (
                    species_df.filter(pl.col("Family").is_in(family_set))
                    .select("AOU")
                    .to_series()
                    .to_list()
                )
                broad_union.update(matched)

        # --- Guild filters (breeding_habitat and/or foraging_guild) ---
        if guild_breeding_habitats or guild_foraging_guilds:
            bh_set = (
                {v.strip() for v in guild_breeding_habitats}
                if guild_breeding_habitats
                else None
            )
            fg_set = (
                {v.strip() for v in guild_foraging_guilds}
                if guild_foraging_guilds
                else None
            )
            for aou_code, traits in guilds.items():
                bh_match = bh_set is None or traits.get("breeding_habitat") in bh_set
                fg_match = fg_set is None or traits.get("foraging_guild") in fg_set
                if bh_match and fg_match:
                    broad_union.add(aou_code)

    # --- Custom AOU codes (Explicitly requested species bypass migrant exclusion) ---
    explicit_set: set[str] = set()
    if custom_aous:
        padded = {_pad_aou(a) for a in custom_aous}
        explicit_set.update(padded & all_aous)

    # Apply migrant exclusion ONLY to broad taxonomic sets, NOT explicit user selections
    target: frozenset[str] = frozenset((broad_union - migrant_aous) | explicit_set)

    logger.debug(
        "resolve_target_species: broad_union=%d, excluded=%d, explicit=%d, target=%d",
        len(broad_union),
        len(broad_union & migrant_aous),
        len(explicit_set),
        len(target),
    )
    return target


# ---------------------------------------------------------------------------
# Convenience: filter a SpeciesList DataFrame to target AOUs
# ---------------------------------------------------------------------------


def filter_species_df(
    species_df: pl.DataFrame,
    target_aous: frozenset[str],
) -> pl.DataFrame:
    """Return the SpeciesList rows whose AOU codes are in *target_aous*.

    Parameters
    ----------
    species_df:
        Full parsed SpeciesList DataFrame.
    target_aous:
        Frozen set of 5-digit AOU codes produced by
        :func:`resolve_target_species`.

    Returns
    -------
    pl.DataFrame
        Filtered rows, preserving all original columns.

    Raises
    ------
    TypeError
        If ``species_df`` is not a :class:`polars.DataFrame`.
    ValueError
        If ``target_aous`` is empty.
    """
    if not isinstance(species_df, pl.DataFrame):
        raise TypeError(
            f"species_df must be a polars.DataFrame, got {type(species_df).__name__}"
        )
    if not target_aous:
        raise ValueError("target_aous must be non-empty.")

    return species_df.filter(pl.col("AOU").is_in(target_aous))
